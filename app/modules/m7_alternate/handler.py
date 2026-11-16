"""M7 入口：三套备选方案的对比、按方案切换查看，以及备选方案页。

同一批候选按三套规则各排一次（A 主方案 / B 减站换权重 / C 限跨馆），
接口与页面吃同一份结果，差别只在交付形状。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import config, errors
from ...web import Ctx, get_ctx, render
from . import service

router = APIRouter(tags=["M7 备选方案与重排"])
PLAN_IDS = tuple(spec["id"] for spec in service.PLANS)


def _parse_day(raw: str | None) -> int | None:
    if raw in (None, ""):
        return None
    if raw not in ("1", "2"):
        raise errors.BadRequest(f"day 只支持 1 / 2，收到 {raw!r}", code="E_BAD_DAY")
    return int(raw)


def _parse_cap(raw: str | None) -> int:
    if raw in (None, ""):
        return config.MAX_HALL_SWITCHES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise errors.BadRequest(
            f"cap 必须是整数，收到 {raw!r}", code="E_BAD_CAP") from None
    low, high = config.HALL_SWITCH_RANGE
    if not low <= value <= high:
        raise errors.BadRequest(
            f"cap 只支持 {low}-{high}，收到 {value}", code="E_BAD_CAP")
    return value


def _parse_plan(raw: str | None) -> str:
    wanted = (raw or "A").upper()
    if wanted not in PLAN_IDS:
        raise errors.BadRequest(
            "plan 只支持 " + " / ".join(PLAN_IDS), code="E_BAD_PLAN")
    return wanted


def _result(ctx: Ctx, batch: dict, day: int | None, cap: int) -> dict:
    return service.build(ctx.store.load_exhibitors(batch["batch_id"]),
                         ctx.store.load_forums(batch["batch_id"]),
                         day=day, cap=cap)


def _group(rows: list[dict]) -> list[dict]:
    """按天分组供页面渲染；天序稳定，组内保持方案顺序。"""
    groups: list[dict] = []
    for row in rows:
        if not groups or groups[-1]["day"] != row["day"]:
            groups.append({"day": row["day"], "rows": []})
        groups[-1]["rows"].append(row)
    return groups


@router.get("/api/alternate")
def api_alternate(day: str = "", cap: str = "", plan: str = "A",
                  batch_id: str | None = None,
                  ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        raise errors.NotFound("没有可用的批次，先调用 POST /api/crawl",
                              code="E_NO_BATCH")
    wanted_day = _parse_day(day)
    wanted_cap = _parse_cap(cap)
    wanted_plan = _parse_plan(plan)
    result = _result(ctx, batch, wanted_day, wanted_cap)
    chosen = service.plan_by_id(result, wanted_plan)
    return {
        "batch_id": batch["batch_id"],
        "rule_version": ctx.settings.rule_version,
        "day": wanted_day,
        "cap": wanted_cap,
        "plan": chosen["id"],
        "plans": [
            {"id": item["id"], "title": item["title"], "order": item["order"],
             "cap": item["cap"], "days": item["days"], "totals": item["totals"],
             "codes": item["codes"], "two_opt": item["two_opt"]}
            for item in result["plans"]
        ],
        "comparison": result["comparison"],
        "symmetry": result["symmetry"],
        "invariant": result["invariant"],
        "codes": result["codes"],
        "stops": service.plan_rows(chosen, wanted_day),
        "unplaced": service.unplaced_rows(chosen),
    }


@router.get("/alternate")
def page_alternate(request: Request, batch_id: str | None = None,
                   day: str = "", cap: str = "", plan: str = "A",
                   ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    result = None
    chosen = None
    rows: list[dict] = []
    groups: list[dict] = []
    unplaced: list[dict] = []
    form_error = ""
    picked = "A"
    if batch is not None:
        try:
            wanted_day = _parse_day(day)
            wanted_cap = _parse_cap(cap)
            picked = _parse_plan(plan)
        except errors.BoothplanError as exc:
            form_error = exc.message
            wanted_day, wanted_cap = None, config.MAX_HALL_SWITCHES
        result = _result(ctx, batch, wanted_day, wanted_cap)
        chosen = service.plan_by_id(result, picked)
        rows = service.plan_rows(chosen, wanted_day)
        groups = _group(rows)
        unplaced = service.unplaced_rows(chosen)
    return render(request, "alternate.html", batch=batch, page="alternate",
                  result=result, chosen=chosen, rows=rows, groups=groups,
                  unplaced=unplaced, form_error=form_error,
                  day=day, cap=cap or str(config.MAX_HALL_SWITCHES), plan=picked,
                  plan_options=[(spec["id"], spec["title"])
                                for spec in service.PLANS],
                  day_options=[("", "两天"), ("1", "第 1 天"), ("2", "第 2 天")],
                  page_title="备选方案")
