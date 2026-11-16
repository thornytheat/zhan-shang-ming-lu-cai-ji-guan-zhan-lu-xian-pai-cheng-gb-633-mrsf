"""距离累加：单段步行、整条路线合计、逐段明细。

入口固定是北登录厅 (0, 0)；同馆算直线，跨馆另加 150 米通道折算。
合计用 Decimal 逐段相加再量化到 0.1，避免浮点累加吃掉尾差。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m2_booth import geometry

ENTRY = config.DEFAULT_ENTRY


def segment(from_hall: str, from_pos: tuple | None,
            to_hall: str, to_pos: tuple | None) -> Decimal:
    return geometry.distance(from_hall, from_pos, to_hall, to_pos)


def route_total(stops: list[dict]) -> Decimal:
    acc = Decimal("0")
    prev_hall, prev_pos = "", ENTRY
    for stop in stops:
        acc += segment(prev_hall, prev_pos, stop["hall"], stop["pos"])
        prev_hall, prev_pos = stop["hall"], stop["pos"]
    return geometry.total([acc])


def walk_minutes(value: Decimal) -> int:
    return geometry.walk_minutes(value)


def segment_rows(stops: list[dict]) -> list[dict]:
    rows = []
    prev_hall, prev_pos = "", ENTRY
    for index, stop in enumerate(stops, 1):
        value = segment(prev_hall, prev_pos, stop["hall"], stop["pos"])
        rows.append({
            "seq": index, "ref": stop["ref"], "name": stop["name"],
            "hall": stop["hall"], "walk_m": value,
            "walk_min": walk_minutes(value),
            "from": prev_hall or "入口",
        })
        prev_hall, prev_pos = stop["hall"], stop["pos"]
    return rows


def entry_distance(stops: list[dict]) -> Decimal:
    """入口 → 第一个点位 → 最后一个点位回入口，用于比较两种排法。"""
    if not stops:
        return Decimal("0.0")
    total = route_total(stops)
    total += geometry.distance_to_entry(stops[-1]["pos"])
    return geometry.total([total])


def hall_changes(stops: list[dict]) -> int:
    changes = 0
    prev = ""
    for stop in stops:
        if prev and stop["hall"] != prev:
            changes += 1
        prev = stop["hall"]
    return changes


def describe(stops: list[dict]) -> dict:
    total = route_total(stops)
    return {
        "stops": len(stops),
        "walk_m": total,
        "walk_min": walk_minutes(total),
        "hall_changes": hall_changes(stops),
        "halls": len({s["hall"] for s in stops}),
        "preferred_ok": sum(1 for s in stops if s.get("in_preferred")),
        "entry_m": entry_distance(stops),
    }
