"""M3 入口：时间窗冲突清单、原因码分布、隔离区页面。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import errors, reasons
from ...web import Ctx, get_ctx, render
from . import service

router = APIRouter(tags=["M3 时间窗"])


@router.get("/api/conflicts")
def api_conflicts(batch_id: str | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        raise errors.NotFound("还没有完成的批次", code="E_NO_BATCH")
    bid = batch["batch_id"]
    sessions = ctx.store.load_forums(bid)
    issues = ctx.store.load_issues(bid)
    conflict_issues = [i for i in issues if i["stage"] == "windows"]
    rows = [i for i in issues if i["level"] == reasons.ERROR]
    return {
        "batch_id": bid,
        "sessions": service.session_rows(sessions),
        "issues": conflict_issues,
        "distribution": service.distribution(issues),
        "mapped": len(rows),
        "summary": service.summary(sessions, conflict_issues),
    }


@router.get("/conflicts")
def page_conflicts(request: Request, batch_id: str | None = None,
                   level: str = "", ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    issues: list[dict] = []
    sessions: list[dict] = []
    if batch is not None:
        bid = batch["batch_id"]
        sessions = ctx.store.load_forums(bid)
        issues = ctx.store.load_issues(bid)
    if level:
        issues = [i for i in issues if i["level"] == level]
    return render(request, "conflicts.html", batch=batch, page="windows",
                  rows=issues, session_rows=service.session_rows(sessions),
                  distribution=service.distribution(issues),
                  level=level, level_options=[("", "全部"), ("error", "隔离"),
                                              ("warn", "提示"), ("info", "记录")],
                  page_title="冲突隔离")
