"""导出服务：行程单 / 证据 / 分段 / 清单，四种产物共用一次批次读取。

导出内容与页面一致：同一批次、同一筛选，页面看到的行数与 CSV 行数相等。
"""
from __future__ import annotations

import json

from ... import reasons
from ..m2_booth import geometry
from ..m3_windows import service as window_service
from ..m4_route import distance as distance_mod
from . import csv_out, itinerary

KINDS = ("itinerary", "evidence", "segment", "unplaced", "manifest", "receipt")
KIND_LABELS = {
    "itinerary": "行程单", "evidence": "隔离证据", "segment": "分段明细",
    "unplaced": "未排入候选", "manifest": "运行清单", "receipt": "清除回执",
}


def build(store, batch: dict, kind: str = "itinerary") -> dict:
    bid = batch["batch_id"]
    stops = store.load_stops(bid)
    days = json.loads(store.load_totals(bid).get("days_json") or "[]")
    issues = store.load_issues(bid)
    manifest_doc = store.load_manifest(bid)
    positions = _positions(store.load_exhibitors(bid), store.load_forums(bid))
    trip = itinerary.build(stops, days)
    receipt_rows = receipt_evidence(store, bid)
    return {
        "kind": kind,
        "label": KIND_LABELS.get(kind, kind),
        "batch_id": bid,
        "trip": trip,
        "groups": itinerary.day_groups(trip["rows"]),
        "rows": trip["rows"],
        "unplaced": itinerary.skipped_rows(stops),
        # 证据 CSV = 原生隔离证据 + 清除回执；回执行带来源、行号、原因码与数量，
        # 不包含姓名与备注原文。页面与接口的 issues 计数仍只算隔离证据。
        "issues": issues,
        "evidence_rows": issues + receipt_rows,
        "receipts": receipts_for_batch(store, bid),
        "receipt_rows": receipt_rows,
        "segments": _segments(stops, positions),
        "manifest": manifest_pick(manifest_doc),
        "manifest_rows": manifest_digest_rows(manifest_doc),
        "stage_rows": manifest_stage_rows(manifest_doc),
        "distribution": window_service.distribution(issues),
    }


def as_csv(kind: str, payload: dict) -> tuple[str, str]:
    """返回 (文件名, 内容)；未知类型抛 ValueError 由 handler 映射成 400。"""
    if kind == "itinerary":
        return (f"{payload['batch_id']}-itinerary.csv",
                csv_out.itinerary_csv(payload["rows"]))
    if kind == "evidence":
        return (f"{payload['batch_id']}-evidence.csv",
                csv_out.evidence_csv(payload.get("evidence_rows", payload["issues"])))
    if kind == "segment":
        days = payload["trip"]["days"]
        grouped = [{"day": day,
                    "segments": [s for s in payload["segments"] if s["day"] == day]}
                   for day in days]
        return f"{payload['batch_id']}-segment.csv", csv_out.segment_csv(grouped)
    if kind == "unplaced":
        return (f"{payload['batch_id']}-unplaced.csv",
                csv_out.unplaced_csv(payload["unplaced"]))
    if kind == "manifest":
        return (f"{payload['batch_id']}-manifest.json",
                json.dumps(payload["manifest"], ensure_ascii=False, indent=2))
    if kind == "receipt":
        return (f"{payload['batch_id']}-receipt.csv",
                csv_out.receipt_csv(payload["receipts"]))
    raise ValueError(f"未知导出类型 {kind!r}；可选 " + " / ".join(KINDS))


def receipts_for_batch(store, batch_id: str) -> list[dict]:
    """与该批次有关的清除回执（只取命中该批次的 target 之后的副本）。"""
    out: list[dict] = []
    for receipt in store.list_redactions(limit=500):
        targets = [target for target in receipt["targets"]
                   if target["batch_id"] == batch_id and target["matched"]]
        if not targets:
            continue
        copy = dict(receipt)
        copy["targets"] = targets
        copy["summary"] = _target_summary(targets)
        out.append(copy)
    return out


def receipt_evidence(store, batch_id: str) -> list[dict]:
    """把回执转成证据行：原因码、对象、字段、说明、行号、来源，无原文。"""
    rows: list[dict] = []
    for receipt in receipts_for_batch(store, batch_id):
        for target in receipt["targets"]:
            rows.append({
                "code": receipt["reason_code"],
                "level": "info",
                "stage": "booth",
                "subject": receipt["subject"].get("name")
                           or receipt["subject"].get("ex_id") or "",
                "field": receipt["fields_label"],
                "raw_value": "",
                "detail": (f"回执 {receipt['receipt_id']}：命中 {target['matched']} 行，"
                           f"清除姓名 {target['owner_cleared']} 行、备注 "
                           f"{target['note_cleared']} 行，原文已永久清除"),
                "source_url": target.get("source_url") or "",
                "row_ref": " ".join(target.get("row_refs") or []),
            })
    return rows


def _target_summary(targets: list[dict]) -> dict:
    return {
        "batches": len(targets),
        "pending_batches": sum(1 for t in targets if t["status"] == "pending"),
        "matched_rows": sum(t["matched"] for t in targets),
        "scrubbed_rows": sum(t["scrubbed"] for t in targets),
        "owner_cleared": sum(t["owner_cleared"] for t in targets),
        "note_cleared": sum(t["note_cleared"] for t in targets),
    }


def summary(payload: dict) -> dict:
    return {
        "batch_id": payload["batch_id"],
        "placed": payload["trip"]["placed"],
        "walk_total_m": str(payload["trip"]["walk_total_m"]),
        "unplaced": len(payload["unplaced"]),
        "issues": len(payload["issues"]),
        "days": payload["trip"]["days"],
        "distribution": payload["distribution"],
    }


def reason_text(code: str) -> str:
    return reasons.label(code)


def _positions(exhibitors: list[dict], forums: list[dict]) -> dict:
    """点位坐标表：展商用排/位还原，论坛用场馆固定点位。"""
    out: dict[str, dict] = {}
    for record in exhibitors:
        out[record["ex_id"]] = {
            "hall": record["hall"],
            "pos": geometry.position(record["hall"], record["row_no"], record["col_no"]),
        }
    for session in forums:
        out[session["session_id"]] = {
            "hall": session["hall"],
            "pos": geometry.position(session["hall"], 1, 1),
        }
    return out


def _segments(stops: list[dict], positions: dict) -> list[dict]:
    """逐段明细：按天重建点位顺序后累加距离，只做展示不动排程。"""
    placed = [row for row in stops if row["status"] in itinerary.PLACED]
    rows: list[dict] = []
    for day in sorted({row["plan_day"] for row in placed}):
        subset = sorted([row for row in placed if row["plan_day"] == day],
                        key=lambda row: row["seq"])
        pseudo = []
        for row in subset:
            info = positions.get(row["ref"]) or {"hall": row["hall"], "pos": None}
            pseudo.append({"ref": row["ref"], "name": row["name"],
                           "hall": info["hall"], "pos": info["pos"]})
        for index, item in enumerate(distance_mod.segment_rows(pseudo), 1):
            rows.append({"day": day, "seq": index, "from": item["from"],
                         "ref": item["ref"], "hall": item["hall"],
                         "walk_m": item["walk_m"], "walk_min": item["walk_min"]})
    return rows

# ---------- 运行清单格式化 ----------
# 清单是 JSON，字段名固定；同一批次导出两次内容逐字相同（不含实时时间字段）。
# 与四种产物共用一次批次读取，所以并进导出服务，不再单独一层。
MANIFEST_FIELDS = ("run_id", "batch_id", "batch_key", "source", "rule_version",
                   "eval_date", "input_digest", "window_rule", "dist_decimals",
                   "timezone", "counters", "stages", "inputs", "params")


def manifest_pick(manifest_doc: dict | None) -> dict:
    if not manifest_doc:
        return {"error": "E_NO_MANIFEST", "message": "该批次没有运行清单"}
    return {key: manifest_doc.get(key) for key in MANIFEST_FIELDS}


def manifest_json(manifest_doc: dict | None) -> str:
    return json.dumps(manifest_pick(manifest_doc), ensure_ascii=False, indent=2,
                      default=_decimal_default)


def manifest_digest_rows(manifest_doc: dict | None) -> list[dict]:
    if not manifest_doc:
        return []
    rows = []
    for item in manifest_doc.get("inputs", []):
        rows.append({
            "url": item.get("url"),
            "status": item.get("status"),
            "bytes": item.get("bytes"),
            "sha256": item.get("sha256"),
            "short": (item.get("sha256") or "")[:16],
            "encoding": item.get("encoding") or "—",
        })
    return rows


def manifest_stage_rows(manifest_doc: dict | None) -> list[dict]:
    if not manifest_doc:
        return []
    return list(manifest_doc.get("stages") or [])


def _decimal_default(value):
    from decimal import Decimal

    if isinstance(value, Decimal):
        return f"{value:.1f}"
    return str(value)
