"""M4 入口：按批次重算排程（确定性重算，不写库）、排程结果页。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from ... import config, errors
from ...web import Ctx, get_ctx, render
from . import service

router = APIRouter(tags=["M4 路线排程"])


@router.post("/api/plans")
def api_plans(payload: dict | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    """对同一批次重算一次排程：同一输入必须得到同一条路线。"""
    body = payload or {}
    batch_id = body.get("batch_id")
    batch = ctx.store.get_batch(batch_id) if batch_id else ctx.store.latest_batch()
    if batch is None:
        raise errors.NotFound(
            "没有可排程的批次，先调用 POST /api/crawl", code="E_NO_BATCH")
    day = body.get("day")
    if day is not None and day not in (1, 2):
        raise errors.BadRequest(f"day 只支持 1 / 2，收到 {day!r}", code="E_BAD_DAY")
    result = service.run(ctx.store.load_exhibitors(batch["batch_id"]),
                         ctx.store.load_forums(batch["batch_id"]))
    rows = service.stop_rows(result["stops"] or [])
    if day is not None:
        rows = [row for row in rows if row["day"] == day]
    return {
        "batch_id": batch["batch_id"],
        "rule_version": ctx.settings.rule_version,
        "totals": result["totals"],
        "reconciliation": result["reconciliation"],
        "days": result["days"],
        "stops": rows,
    }


@router.get("/plan")
def page_plan(request: Request, batch_id: str | None = None,
              day: str = "", ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    stops: list[dict] = []
    totals: dict = {}
    reconciliation = None
    days: list[dict] = []
    if batch is not None:
        bid = batch["batch_id"]
        stops = ctx.store.load_stops(bid)
        totals = ctx.store.load_totals(bid)
        reconciliation = json.loads(totals.get("reconcile_json") or "null")
        days = json.loads(totals.get("days_json") or "[]")
    wanted = int(day) if day in ("1", "2") else 0
    placed = [row for row in stops if row["status"] in ("SCHEDULED", "RESCHEDULED")]
    if wanted:
        placed = [row for row in placed if row["plan_day"] == wanted]
    rest = [row for row in stops if row["status"] not in ("SCHEDULED", "RESCHEDULED")]
    if wanted:
        rest = [row for row in rest if row["plan_day"] == wanted]
    return render(request, "plan.html", batch=batch, page="route",
                  placed=placed, rest=rest, totals=totals, days=days,
                  reconciliation=reconciliation, day=day,
                  day_options=[("", "两天"), ("1", "第 1 天"), ("2", "第 2 天")],
                  classify_labels=config.CLASSIFY_LABELS,
                  page_title="路线排程")
