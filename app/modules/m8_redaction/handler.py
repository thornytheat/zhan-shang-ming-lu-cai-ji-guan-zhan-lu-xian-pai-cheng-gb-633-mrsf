"""M8 入口：撤回清除回执、续跑、回执列表与详情页。

清除动作是写操作，单独一个 POST；其余都是只读回执。回执页面与证据下载
共用同一份回执数据，页面看到的数量与 CSV 行数一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ... import errors
from ...web import Ctx, get_ctx, render
from ..m6_export import csv_out
from . import service

router = APIRouter(tags=["M8 撤回清除"])


@router.post("/api/redactions")
def api_create(payload: dict | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    """登记一次撤回并执行清除；同一请求重发沿用同一张回执。"""
    body = payload or {}
    receipt = service.create_and_scrub(ctx.store, body,
                                       max_batches=_max_batches(body))
    return _payload(receipt)


@router.post("/api/exhibitors/{ex_id}/withdraw")
def api_withdraw_exhibitor(ex_id: str, payload: dict | None = None,
                           ctx: Ctx = Depends(get_ctx)) -> dict:
    """按展商编号撤回的便捷入口：等价于 POST /api/redactions 带 ex_id。"""
    body = dict(payload or {})
    body["ex_id"] = ex_id
    receipt = service.create_and_scrub(ctx.store, body,
                                       max_batches=_max_batches(body))
    return _payload(receipt)


@router.post("/api/redactions/{receipt_id}/resume")
def api_resume(receipt_id: str, payload: dict | None = None,
               ctx: Ctx = Depends(get_ctx)) -> dict:
    body = payload or {}
    receipt = service.resume(ctx.store, receipt_id,
                             max_batches=_max_batches(body))
    return _payload(receipt)


@router.get("/api/redactions")
def api_list(limit: int = 20, ctx: Ctx = Depends(get_ctx)) -> dict:
    rows = service.receipts(ctx.store, limit=limit)
    return {"count": len(rows), "receipts": [_payload(row) for row in rows]}


@router.get("/api/redactions/{receipt_id}")
def api_detail(receipt_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    return _payload(service.receipt(ctx.store, receipt_id))


@router.get("/api/redactions/{receipt_id}/receipt.csv")
def api_receipt_csv(receipt_id: str, ctx: Ctx = Depends(get_ctx)) -> Response:
    return _receipt_csv(ctx, receipt_id)


@router.get("/api/redactions/{receipt_id}/evidence.csv")
def api_receipt_evidence_csv(receipt_id: str,
                             ctx: Ctx = Depends(get_ctx)) -> Response:
    """证据下载别名：回执本身就是可审计证据，两个名字给同一份内容。"""
    return _receipt_csv(ctx, receipt_id)


def _receipt_csv(ctx: Ctx, receipt_id: str) -> Response:
    receipt = service.receipt(ctx.store, receipt_id)
    text = csv_out.receipt_csv([receipt])
    return Response(
        content=text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{receipt_id}-receipt.csv"'})


@router.get("/redactions")
def page_redactions(request: Request, receipt_id: str = "",
                    ctx: Ctx = Depends(get_ctx)):
    rows = service.receipts(ctx.store, limit=30)
    current = None
    if receipt_id:
        current = service.receipt(ctx.store, receipt_id)
    elif rows:
        current = rows[0]
    return render(request, "redactions.html", page="redactions", rows=rows,
                  current=current, page_title="撤回清除")


def _max_batches(body: dict) -> int | None:
    raw = body.get("max_batches")
    if raw is None:
        return None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        raise errors.BadRequest("max_batches 必须是正整数",
                                code="E_BAD_REDACTION") from None


def _payload(receipt: dict) -> dict:
    return {
        "receipt_id": receipt["receipt_id"],
        "withdrawal_key": receipt["withdrawal_key"],
        "subject": receipt["subject"],
        "fields": receipt["fields"],
        "fields_label": receipt["fields_label"],
        "state": receipt["state"],
        "state_label": receipt["state_label"],
        "reason_code": receipt["reason_code"],
        "created_at": receipt["created_at"],
        "updated_at": receipt["updated_at"],
        "completed_at": receipt["completed_at"],
        "summary": receipt["summary"],
        "retained": ["来源", "行号", "原因码", "数量"],
        "targets": [
            {key: target[key] for key in
             ("batch_id", "batch_key", "batch_status", "status", "matched",
              "scrubbed", "source_url", "row_refs", "owner_cleared",
              "note_cleared", "cleared_at", "note")}
            for target in receipt["targets"]
        ],
        "events": receipt["events"],
    }
