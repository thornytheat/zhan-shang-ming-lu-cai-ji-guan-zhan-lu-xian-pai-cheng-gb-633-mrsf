"""行程单：把排好的点位拼成可执行的一张表。

一行 = 一个点位：日期 / 时段 / 展商或论坛 / 展馆 / 停留 / 上一段步行 / 状态。
未排入的候选不进主表，但在对账与证据 CSV 里逐条出现，避免"少了几家"说不清。
"""
from __future__ import annotations

from ... import config
from ..m3_windows import windows
from ..m4_route import service as route_service

PLACED = ("SCHEDULED", "RESCHEDULED")


def build(stops: list[dict], days: list[dict]) -> dict:
    placed = [row for row in stops if row["status"] in PLACED]
    placed.sort(key=lambda row: (row["plan_day"], row["seq"]))
    rows = []
    for row in placed:
        start_min = windows.local_minutes(row["arrival_utc"])
        end_min = windows.local_minutes(row["depart_utc"])
        rows.append({
            "day": row["plan_day"],
            "day_label": route_service.day_label(row["plan_day"]),
            "seq": row["seq"],
            "kind": row["kind"],
            "ref": row["ref"],
            "name": row["name"],
            "hall": row["hall"],
            "slot": f"{windows.hhmm(start_min)}–{windows.hhmm(end_min)}",
            "start_min": start_min,
            "end_min": end_min,
            "dwell": row.get("dwell", row.get("dwell_min", 0)),
            "walk_m": row["walk_m"],
            "status": row["status"],
            "status_label": config.CLASSIFY_LABELS.get(row["status"], row["status"]),
            "code": row["code"] or "",
            "note": row["note"],
        })
    return {
        "rows": rows,
        "placed": len(rows),
        "days": [day["day"] for day in days],
        "walk_total_m": _sum_walk(rows),
    }


def day_groups(rows: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for day in sorted({row["day"] for row in rows}):
        subset = [row for row in rows if row["day"] == day]
        groups.append({
            "day": day,
            "label": route_service.day_label(day),
            "rows": subset,
            "walk_m": _sum_walk(subset),
            "first": subset[0]["slot"].split("–")[0] if subset else "—",
            "last": subset[-1]["slot"].split("–")[-1] if subset else "—",
        })
    return groups


def skipped_rows(stops: list[dict]) -> list[dict]:
    rows = []
    for row in stops:
        if row["status"] in PLACED:
            continue
        rows.append({
            "day": row["plan_day"], "ref": row["ref"], "name": row["name"],
            "kind": row["kind"], "hall": row["hall"],
            "status": row["status"],
            "status_label": config.CLASSIFY_LABELS.get(row["status"], row["status"]),
            "code": row["code"] or "", "note": row["note"],
        })
    rows.sort(key=lambda row: (row["day"], row["status"], row["code"]))
    return rows


def _sum_walk(rows: list[dict]):
    total = 0
    decimals = []
    for row in rows:
        decimals.append(row["walk_m"])
    if not decimals:
        from decimal import Decimal
        return Decimal("0.0")
    from decimal import Decimal
    acc = Decimal("0")
    for value in decimals:
        acc += value if isinstance(value, Decimal) else Decimal(str(value))
    return acc.quantize(config.DIST_QUANT)
