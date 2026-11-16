"""批次编排：采信 → 展位解析 → 时间窗 → 排程 → 导出，一个批次的完整流水线。

再跑一次的三种情况：
- 同一份输入、同样来源：命中批次键，复用已完成批次，不新增行；
- 上一次失败后点续跑：复用已采信页面，版本号 +1，记 resume_of；
- 来源或数据变了：新批次键，全新一批。

批次键与版本校验也在这份编排里（原来拆在 idempotent.py，只有转发与校验，
合并回来少一层壳）。
"""
from __future__ import annotations

import json
from decimal import Decimal

from ... import errors, manifest, reasons
from ..m1_catalog import service as catalog_service
from ..m2_booth import service as booth_service
from ..m3_windows import service as window_service
from ..m4_route import service as route_service
from . import history


def run(*, settings, store, source: str = "all", fetcher=None,
        resume_of: str | None = None, mode: str = "full") -> dict:
    timer = history.Timer()
    started = history.now_iso()
    run_id = manifest.new_run_id()
    batch_id = history.new_batch_id()
    params = {"source": source, "mode": mode, "resume_of": resume_of}
    batch = {
        "batch_id": batch_id, "batch_key": "", "run_id": run_id,
        "source": source, "status": "running", "created_at": started,
        "input_digest": "", "resume_of": resume_of, "params": params,
        "version": 1,
    }

    try:
        bundle = catalog_service.ingest(settings, source=source, fetcher=fetcher)
    except errors.BoothplanError:
        raise
    except Exception as exc:                            # noqa: BLE001
        _fail(store, batch, history.empty_stages(f"{type(exc).__name__}: {exc}"),
              started, timer.total(), str(exc))
        return {"status": "failed", "batch": batch, "stages": [],
                "failed_stage": "catalog", "error": str(exc), "totals": {}}

    digest = manifest.digest_parts(bundle.input_digest_parts)
    batch["input_digest"] = digest
    batch["batch_key"] = key_for(source, digest)

    if mode == "full" and not resume_of:
        hit = replay(store, batch["batch_key"])
        if hit is not None:
            return replay_payload(hit, store.load_stages(hit["batch_id"]),
                                             store.load_totals(hit["batch_id"]))
    batch["version"] = next_version(store, batch["batch_key"])

    stages: list[dict] = []
    pages: list[dict] = []
    exhibitors: list[dict] = []
    sessions: list[dict] = []
    issues: list[dict] = []
    plan: dict = {}
    failed_stage = None
    error_text = None

    try:
        pages = catalog_service.page_rows(bundle)
        catalog_issues = bundle.issues
        issues.extend(catalog_issues)
        stages.append(history.stage(
            "catalog", in_count=len(pages),
            out_count=sum(page["row_count"] for page in pages),
            quarantine_count=_errors(catalog_issues),
            elapsed_ms=timer.lap(), note=f"分段 {bundle.segments} 个"))

        exhibitors, booth_issues = booth_service.build(bundle.pages)
        issues.extend(booth_issues)
        booth_stats = booth_service.summary(exhibitors, booth_issues)
        stages.append(history.stage(
            "booth", in_count=sum(page.row_count for page in bundle.pages),
            out_count=len(exhibitors), quarantine_count=_errors(booth_issues),
            elapsed_ms=timer.lap(),
            note=f"可排程 {booth_stats['ok']} · 无坐标 {booth_stats['unplaced']}"))

        sessions, _conflict_rows, window_issues = window_service.build(bundle.pages)
        issues.extend(window_issues)
        window_stats = window_service.summary(sessions, window_issues)
        stages.append(history.stage(
            "windows", in_count=len(sessions),
            out_count=window_stats["fixed"], quarantine_count=_errors(window_issues),
            elapsed_ms=timer.lap(),
            note=f"冲突 {window_stats['conflicts']} 场"))

        plan = route_service.run(exhibitors, sessions)
        stages.append(history.stage(
            "route", in_count=plan["totals"]["candidates"],
            out_count=plan["totals"]["scheduled"] + plan["totals"]["rescheduled"],
            quarantine_count=(plan["totals"]["conflict"] + plan["totals"]["skipped"]),
            elapsed_ms=timer.lap(),
            note=f"总步行 {plan['totals']['walk_total_m']} 米"))

        stages.append(history.stage(
            "export", in_count=plan["totals"]["stops"],
            out_count=plan["totals"]["stops"], quarantine_count=0,
            elapsed_ms=timer.lap(), note=f"{len(plan['days'])} 天行程单"))
    except Exception as exc:                            # noqa: BLE001
        failed_stage = _next_stage(stages)
        error_text = f"{type(exc).__name__}: {exc}"
        stages.append(history.stage(failed_stage, in_count=0, out_count=0,
                                  quarantine_count=0, status="failed",
                                  elapsed_ms=timer.lap(), note=error_text))
        _fail(store, batch, stages, started, timer.total(), error_text)
        return {"status": "failed", "batch": batch, "stages": stages,
                "failed_stage": failed_stage, "error": error_text, "totals": {}}

    totals = _totals(pages, exhibitors, sessions, issues, plan)
    finished = history.now_iso()
    batch["status"] = "done"
    batch["finished_at"] = finished
    manifest_doc = manifest.build(
        run_id=run_id, batch_id=batch_id, batch_key_value=batch["batch_key"],
        settings=settings, source=source, input_digest=digest, pages=pages,
        params=params, started_at=started, finished_at=finished,
        elapsed_ms=timer.total(), stages=stages, counters=totals)
    store.save_run(batch=batch, pages=pages, exhibitors=exhibitors,
                   forums=sessions, issues=issues, stops=plan["stops"],
                   stages=stages, totals=totals, manifest=manifest_doc)
    return {
        "status": "done",
        "batch": batch,
        "stages": stages,
        "totals": totals,
        "reconciliation": plan["reconciliation"],
        "reconcile_note": plan["reconciliation"]["note"],
        "idempotent_replay": False,
        "replay_of": resume_of,
        "resume_hint": None,
    }


def resume_batch(*, settings, store, batch_id: str, fetcher=None,
                 submitted_version: int | None = None) -> dict:
    """断点续跑：校验版本后按同一来源重跑，版本号 +1。"""
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_NOT_FOUND")
    guard(store, batch_id, submitted_version)
    params = json.loads(batch.get("params_json") or "{}")
    result = run(settings=settings, store=store,
                 source=params.get("source", "all"), fetcher=fetcher,
                 resume_of=batch_id, mode="resume")
    result["resume_hint"] = _resume_hint(result)
    return result


def plan(store, batch_id: str) -> dict:
    """能不能续跑：看阶段表里已经 ok 的阶段。"""
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_NOT_FOUND")
    stages = store.load_stages(batch_id)
    completed = [row["stage"] for row in stages if row["status"] == "ok"]
    can_resume = ("catalog" in completed) and batch["status"] == "failed"
    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "version": batch.get("version", 1),
        "completed": completed,
        "watermark": history.watermark(stages),
        "can_resume": can_resume,
        "reason": "已完成的阶段可复用" if can_resume else (
            "批次已完成，重跑命中批次键" if batch["status"] == "done"
            else reasons.label("B_RESUME_NO_STATE")),
        "versions": history.versions(store, batch["batch_key"]),
    }


def detail(store, batch_id: str) -> dict:
    payload = history.detail(store, batch_id)
    if payload is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_NOT_FOUND")
    payload["stage_rows"] = history.stage_matrix(payload["stages"])
    payload["version_rows"] = history.versions(store, payload["batch"]["batch_key"])
    return payload


def _totals(pages, exhibitors, sessions, issues, plan) -> dict:
    totals = {
        "pages": len(pages),
        "exhibitors": len(exhibitors),
        "sessions": len(sessions),
        "issues": len(issues),
    }
    for key, value in plan["totals"].items():
        totals[key] = value
    totals["reconcile_json"] = json.dumps(plan["reconciliation"], ensure_ascii=False)
    totals["days_json"] = json.dumps(
        [{"day": day["day"], "stops": day["stops"], "walk_m": str(day["walk_m"]),
          "open": day["open"], "close": day["close"], "halls": day["halls"],
          "fixed": day["fixed"], "slack": day["slack"]} for day in plan["days"]],
        ensure_ascii=False)
    return totals


def _errors(issues: list[dict]) -> int:
    return sum(1 for item in issues if item["level"] == reasons.ERROR)


def _next_stage(stages: list[dict]) -> str:
    from ... import config

    done = {row["stage"] for row in stages}
    for key in config.STAGES:
        if key not in done:
            return key
    return "export"


def _resume_hint(result: dict) -> str | None:
    if result.get("status") == "failed":
        return f"停在 {result.get('failed_stage')}，可再次续跑"
    return None


def _fail(store, batch: dict, stages: list[dict], started: str, elapsed_ms: int,
          note: str) -> None:
    batch["status"] = "failed"
    batch["finished_at"] = history.now_iso()
    store.save_attempt(batch=batch, pages=[], stages=stages, note=note)


def decimal_text(value) -> str:
    return str(value if isinstance(value, Decimal) else Decimal(str(value)))

# ---------- 幂等与版本 ----------
# 批次键 = 来源 + 输入摘要前 16 位。同一份输入重跑命中同一个键，
# 直接复用已完成的批次，不新增行（幂等重跑）。
# 版本号只在同一批次键内递增；提交的版本小于当前最新版本时拒绝，
# 不允许旧结果覆盖新结果。这几条是编排的一部分，不单独成文件。
def key_for(source: str, digest: str) -> str:
    return manifest.batch_key(source, digest)


def replay(store, batch_key: str) -> dict | None:
    """已完成批次里找同键的一条；没有就返回 None。"""
    return store.find_done_by_key(batch_key)


def replay_payload(batch: dict, stages: list[dict], totals: dict) -> dict:
    return {
        "status": "done",
        "batch": batch,
        "stages": stages,
        "totals": totals,
        "idempotent_replay": True,
        "replay_of": batch["batch_id"],
        "reconciliation": None,
        "reconcile_note": "同一输入摘要命中已完成批次，复用结果，未新增行",
    }


def next_version(store, batch_key: str) -> int:
    return store.next_version(batch_key)


def guard(store, batch_id: str, submitted: int | None) -> int:
    """校验提交的版本不早于当前最新版本；返回应写入的版本号。"""
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_NOT_FOUND")
    latest = store.next_version(batch["batch_key"]) - 1
    if submitted is not None and submitted < latest:
        raise errors.Conflict(
            f"提交版本 v{submitted} 早于当前 v{latest}，已拒绝",
            code="B_STALE_REJECTED",
            detail={"submitted": submitted, "latest": latest})
    return max(latest, 1) + 1


def same_digest(left: str | None, right: str | None) -> bool:
    return bool(left) and bool(right) and left == right


def describe(batch: dict) -> dict:
    return {
        "batch_id": batch["batch_id"],
        "batch_key": batch["batch_key"],
        "version": batch.get("version", 1),
        "status": batch["status"],
        "created_at": batch["created_at"],
    }
