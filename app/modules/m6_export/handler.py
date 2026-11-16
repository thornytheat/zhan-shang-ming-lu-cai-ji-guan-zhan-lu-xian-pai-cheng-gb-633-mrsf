"""M6 入口：四种导出（页面 + CSV/JSON），以及行程单页。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ... import errors
from ...web import Ctx, get_ctx, render
from . import service

router = APIRouter(tags=["M6 行程导出"])


@router.get("/api/export")
def api_export(batch_id: str | None = None, kind: str = "itinerary",
               ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = _batch(ctx, batch_id)
    payload = service.build(ctx.store, batch, kind)
    return {"batch_id": batch["batch_id"], "kind": kind,
            "summary": service.summary(payload),
            "rows": payload["rows"],
            "unplaced": payload["unplaced"],
            "manifest": payload["manifest"]}


@router.get("/api/export/{kind}.csv")
def api_export_csv(kind: str, batch_id: str | None = None,
                   ctx: Ctx = Depends(get_ctx)) -> Response:
    if kind not in service.KINDS:
        raise errors.BadRequest(
            f"未知导出类型 {kind!r}；可选 " + " / ".join(service.KINDS),
            code="E_BAD_EXPORT_KIND")
    batch = _batch(ctx, batch_id)
    payload = service.build(ctx.store, batch, kind)
    try:
        filename, text = service.as_csv(kind, payload)
    except ValueError as exc:
        raise errors.BadRequest(str(exc), code="E_BAD_EXPORT_KIND") from None
    media = "text/csv; charset=utf-8" if filename.endswith(".csv") else "application/json"
    return Response(content=text, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/export")
def page_export(request: Request, batch_id: str | None = None,
                kind: str = "itinerary", ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    payload = None
    if batch is not None:
        payload = service.build(ctx.store, batch, kind)
    return render(request, "export.html", batch=batch, page="export",
                  payload=payload, kind=kind,
                  kinds=[(key, service.KIND_LABELS[key]) for key in service.KINDS],
                  page_title="行程单")


def _batch(ctx: Ctx, batch_id: str | None) -> dict:
    batch = ctx.store.get_batch(batch_id) if batch_id else ctx.store.latest_batch()
    if batch is None:
        raise errors.NotFound(
            "没有可导出的批次，先调用 POST /api/crawl", code="E_NO_BATCH")
    return batch
