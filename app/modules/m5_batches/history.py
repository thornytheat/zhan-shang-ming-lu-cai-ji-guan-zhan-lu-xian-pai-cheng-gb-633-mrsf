"""批次台账：批次号、阶段记录、耗时、水位、列表、单批详情、版本链。

阶段顺序固定为 catalog → booth → windows → route → export，
每个阶段都记「进去多少 / 出来多少 / 隔离多少」，页面直接读这张表。
批次状态（编号 / 时间戳 / 阶段 / 水位）与历史视图放在一起，只此一份。
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid

from ... import config

STATUS_LABELS = {"done": "完成", "failed": "失败", "running": "运行中"}


# ---------- 批次状态 ----------
def new_batch_id() -> str:
    stamp = dt.datetime.now(config.UTC).strftime("%Y%m%d%H%M%S")
    return f"B{stamp}{uuid.uuid4().hex[:4]}"


def now_iso() -> str:
    return dt.datetime.now(config.UTC).isoformat(timespec="seconds")


def stage(stage_key: str, *, in_count: int, out_count: int,
          quarantine_count: int, status: str = "ok", elapsed_ms: int = 0,
          note: str = "") -> dict:
    return {
        "stage": stage_key,
        "label": config.STAGE_LABELS.get(stage_key, stage_key),
        "in_count": int(in_count),
        "out_count": int(out_count),
        "quarantine_count": int(quarantine_count),
        "status": status,
        "elapsed_ms": int(elapsed_ms),
        "note": note,
    }


class Timer:
    """阶段计时；毫秒向下取整，便于重跑对比。"""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self._last = self.started

    def lap(self) -> int:
        now = time.perf_counter()
        elapsed = int((now - self._last) * 1000)
        self._last = now
        return elapsed

    def total(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


def empty_stages(reason: str) -> list[dict]:
    """失败批次也要有五行阶段，前端不必判空。"""
    return [stage(key, in_count=0, out_count=0, quarantine_count=0,
                  status="failed" if index == 0 else "skipped", note=reason)
            for index, key in enumerate(config.STAGES)]


def watermark(stages: list[dict]) -> str | None:
    """水位 = 最后一个 ok 阶段之后的那个阶段键。"""
    last_ok = None
    for row in stages:
        if row["status"] == "ok":
            last_ok = row["stage"]
    if last_ok is None:
        return None
    order = list(config.STAGES)
    index = order.index(last_ok)
    return order[index + 1] if index + 1 < len(order) else None


def monotonic(previous: str | None, current: str | None) -> bool:
    """水位只能前进：当前阶段下标不小于上一次。"""
    if current is None:
        return True
    if previous is None:
        return True
    order = list(config.STAGES)
    if previous not in order or current not in order:
        return True
    return order.index(current) >= order.index(previous)


# ---------- 历史视图 ----------
def rows(store, limit: int = 30) -> list[dict]:
    out = []
    for batch in store.list_batches(limit=limit):
        stages = store.load_stages(batch["batch_id"])
        out.append({
            "batch_id": batch["batch_id"],
            "batch_key": batch["batch_key"],
            "run_id": batch["run_id"],
            "source": batch["source"],
            "status": batch["status"],
            "status_label": STATUS_LABELS.get(batch["status"], batch["status"]),
            "version": batch.get("version", 1),
            "created_at": batch["created_at"],
            "finished_at": batch.get("finished_at") or "—",
            "input_digest": (batch["input_digest"] or "")[:16],
            "resume_of": batch.get("resume_of") or "—",
            "note": batch.get("note") or "",
            "stages": stages,
            "watermark": watermark(stages) or "—",
        })
    return out


def detail(store, batch_id: str) -> dict | None:
    batch = store.get_batch(batch_id)
    if batch is None:
        return None
    counts = store.count_rows(batch_id)
    totals = store.load_totals(batch_id)
    stages = store.load_stages(batch_id)
    return {
        "batch": batch,
        "stages": stages,
        "counts": counts,
        "totals": {k: v for k, v in totals.items()
                   if not k.endswith("_json")},
        "reconciliation": json.loads(totals.get("reconcile_json") or "null"),
        "watermark": watermark(stages),
    }


def versions(store, batch_key: str) -> list[dict]:
    """同一批次键的所有版本，按版本号升序。"""
    chain = [row for row in store.list_batches(limit=200)
             if row["batch_key"] == batch_key]
    chain.sort(key=lambda row: (row.get("version", 1), row["created_at"]))
    return [{"batch_id": row["batch_id"], "version": row.get("version", 1),
             "status": row["status"], "created_at": row["created_at"],
             "finished_at": row.get("finished_at") or "—"}
            for row in chain]


def stage_matrix(stages: list[dict]) -> list[dict]:
    """阶段表补齐到五行，缺的阶段显示为未运行。"""
    present = {row["stage"]: row for row in stages}
    out = []
    for key in config.STAGES:
        row = present.get(key)
        if row is None:
            out.append({"stage": key, "label": config.STAGE_LABELS[key],
                        "in_count": 0, "out_count": 0, "quarantine_count": 0,
                        "status": "skipped", "elapsed_ms": 0, "note": "未运行"})
        else:
            out.append(row)
    return out


def totals_rows(totals: dict) -> list[dict]:
    labels = {
        "candidates": "候选站点", "scheduled": "排入", "rescheduled": "改期",
        "conflict": "冲突", "skipped": "跳过", "walk_total_m": "总步行(米)",
        "stops": "行程点位", "pages": "采集页数", "exhibitors": "名录行",
        "sessions": "议程场次", "issues": "原因码行",
    }
    order = ("pages", "exhibitors", "sessions", "candidates", "scheduled",
             "rescheduled", "conflict", "skipped", "stops", "walk_total_m",
             "issues")
    rows = []
    for key in order:
        if key in totals:
            rows.append({"key": key, "label": labels.get(key, key),
                         "value": totals[key]})
    return rows
