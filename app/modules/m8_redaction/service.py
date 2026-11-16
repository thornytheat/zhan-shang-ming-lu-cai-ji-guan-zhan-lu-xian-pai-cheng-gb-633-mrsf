"""撤回清除服务：建回执 → 逐批清除 → 续跑 → 回执查询。

流程要点：
- 回执先落库、再清除；回执里没有原文，只有主体、字段、来源、行号与数量；
- 每个受影响批次单独一个事务、单独一个 target 状态，中断后从剩下的批次续跑；
- 回执编号由撤回键决定（主体 + 字段），同一请求重发或续跑都沿用同一张回执，
  不会重复生成；
- 清除只改对接人姓名与备注原文，展商名称、展位、行号、原因码与数量保留。

清除与采集的并发由 store 的写事务保证：采集落库前先读回执规则，
已撤回的字段一律写成清除标记，所以不会把原文写回去。
"""
from __future__ import annotations

from ... import errors, redaction


def create_and_scrub(store, payload: dict, *, max_batches: int | None = None) -> dict:
    """建回执并尽量一次清完；中断时返回 partial，可再调 resume 续跑。"""
    try:
        subject = redaction.build_subject(payload or {})
        fields = redaction.build_fields((payload or {}).get("fields"))
    except ValueError as exc:
        raise errors.BadRequest(str(exc), code="E_BAD_REDACTION") from None
    key = redaction.withdrawal_key(
        subject, fields, str((payload or {}).get("idempotency_key") or ""))
    receipt_id = redaction.receipt_id_for(key)
    receipt = store.create_redaction(
        withdrawal_key=key, receipt_id=receipt_id, subject=subject, fields=fields,
        note=str((payload or {}).get("note") or ""))
    # 回执之前可能已经存在：同一请求重发或上一次中断，都只补扫新增批次，
    # 不会重扫旧批次，也不会重新生成回执。
    receipt = store.refresh_redaction_targets(receipt_id) or receipt
    return _drain(store, receipt, max_batches=max_batches)


def resume(store, receipt_id: str, *, max_batches: int | None = None) -> dict:
    receipt = store.get_redaction(receipt_id)
    if receipt is None:
        raise errors.NotFound(f"清除回执 {receipt_id} 不存在",
                              code="E_RECEIPT_NOT_FOUND")
    return _drain(store, receipt, max_batches=max_batches)


def _drain(store, receipt: dict, *, max_batches: int | None = None) -> dict:
    """把剩下待清除的批次逐个跑完；每个批次一个事务，失败就停在那一个。

    中断（进程退出、单批写失败、调用方给了 max_batches）之后回执仍在，
    下一个 pending 批次也还在；再调一次就从这个批次继续，回执编号不变。
    """
    receipt_id = receipt["receipt_id"]
    if receipt["state"] == "done":
        return receipt
    done = 0
    while max_batches is None or done < max_batches:
        pending = store.pending_redaction_targets(receipt_id, limit=1)
        if not pending:
            break
        batch_id = pending[0]
        try:
            after = store.scrub_target(receipt_id, batch_id)
        except Exception as exc:                        # noqa: BLE001
            store.mark_redaction_events(
                receipt_id, "interrupted", batch_id=batch_id,
                detail=f"{type(exc).__name__}: {exc}")
            break
        if not after:
            break
        receipt = after
        done += 1
        if any(target["batch_id"] == batch_id and target["status"] == "pending"
               for target in receipt["targets"]):
            # 这个批次没清成功：保持回执为可续跑状态，不往后跳。
            break
    return store.get_redaction(receipt_id) or receipt


def receipts(store, limit: int = 30) -> list[dict]:
    return store.list_redactions(limit=limit)


def receipt(store, receipt_id: str) -> dict:
    found = store.get_redaction(receipt_id)
    if found is None:
        raise errors.NotFound(f"清除回执 {receipt_id} 不存在",
                              code="E_RECEIPT_NOT_FOUND")
    return found


def summary(receipt: dict) -> dict:
    """页面 / 接口共用的一段话：清了什么、留了什么。"""
    return {
        "receipt_id": receipt["receipt_id"],
        "state": receipt["state"],
        "fields": receipt["fields"],
        "batches": receipt["summary"]["batches"],
        "matched_rows": receipt["summary"]["matched_rows"],
        "scrubbed_rows": receipt["summary"]["scrubbed_rows"],
        "pending_batches": receipt["summary"]["pending_batches"],
        "retained": ["来源", "行号", "原因码", "数量"],
    }
