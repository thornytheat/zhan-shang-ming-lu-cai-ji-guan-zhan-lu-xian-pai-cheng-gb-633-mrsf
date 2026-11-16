"""M5 入口：批次列表、单批详情、版本链，以及批次版本页。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...web import Ctx, get_ctx, render
from . import history, service

router = APIRouter(tags=["M5 批次版本"])


@router.get("/api/batches")
def api_batches(limit: int = 20, ctx: Ctx = Depends(get_ctx)) -> dict:
    rows = history.rows(ctx.store, limit=limit)
    return {"count": len(rows), "batches": rows}


@router.get("/api/batches/{batch_id}")
def api_batch(batch_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    payload = service.detail(ctx.store, batch_id)
    return {
        "batch": payload["batch"],
        "stages": payload["stage_rows"],
        "counts": payload["counts"],
        "totals": payload["totals"],
        "reconciliation": payload["reconciliation"],
        "watermark": payload["watermark"],
        "versions": payload["version_rows"],
    }


@router.get("/api/batches/{batch_id}/plan")
def api_resume_plan(batch_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    return service.plan(ctx.store, batch_id)


@router.get("/batches")
def page_batches(request: Request, batch_id: str | None = None,
                 ctx: Ctx = Depends(get_ctx)):
    rows = history.rows(ctx.store, limit=20)
    current = None
    if batch_id:
        current = service.detail(ctx.store, batch_id)
    elif rows:
        current = service.detail(ctx.store, rows[0]["batch_id"])
    if current is not None:
        current["totals_rows"] = history.totals_rows(current["totals"])
    return render(request, "batches.html", batch=ctx.current_batch(batch_id),
                  page="batches", rows=rows, current=current,
                  page_title="批次版本")
