"""平面几何：展位坐标与步行距离。

坐标规则：馆锚点是该馆「1 排 1 位」的地面坐标，位向 x 正方向每 8 米一格，
排向 y 正方向每 6 米一格。距离 = 直线距离 + 跨馆 150 米通道折算，
结果一律 Decimal 量化到 0.1 米，避免浮点累加误差。
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from ... import config

PITCH_COL = config.COL_PITCH_M
PITCH_ROW = config.ROW_PITCH_M


def position(hall: str, row: str | int | None, col: str | int | None) -> tuple | None:
    """馆 + 排 + 位 → (x, y)；馆不在平面表内返回 None。"""
    anchor = config.HALLS.get(hall)
    if anchor is None:
        return None
    row_no = _int(row)
    col_no = _int(col)
    if row_no is None or col_no is None or row_no < 1 or col_no < 1:
        return None
    x = anchor["x"] + Decimal(col_no - 1) * PITCH_COL
    y = anchor["y"] + Decimal(row_no - 1) * PITCH_ROW
    return (x, y)


def straight(a: tuple, b: tuple) -> Decimal:
    dx = float(a[0] - b[0])
    dy = float(a[1] - b[1])
    return Decimal(str(math.hypot(dx, dy)))


def distance(from_hall: str, from_pos: tuple | None,
             to_hall: str, to_pos: tuple | None) -> Decimal:
    """两点步行距离；缺坐标返回 0 且由调用方标记为不可达。"""
    if from_pos is None or to_pos is None:
        return _q(Decimal("0"))
    base = straight(from_pos, to_pos)
    if from_hall != to_hall:
        base += config.CROSS_HALL_PENALTY_M
    return _q(base)


def distance_to_entry(pos: tuple | None) -> Decimal:
    if pos is None:
        return _q(Decimal("0"))
    return _q(straight(pos, config.DEFAULT_ENTRY))


def walk_minutes(dist: Decimal) -> int:
    """步行时间向上取整到分钟；0 米也要 0 分钟。"""
    if dist <= 0:
        return 0
    minutes = (dist / config.WALK_M_PER_MIN).to_integral_value(rounding=ROUND_CEILING)
    return int(minutes)


def total(pairs: list[tuple]) -> Decimal:
    acc = Decimal("0")
    for value in pairs:
        acc += value
    return _q(acc)


def _q(value: Decimal) -> Decimal:
    return value.quantize(config.DIST_QUANT, rounding=ROUND_HALF_UP)


def _int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def grid_cells(rows: int, cols: int) -> int:
    """馆内网格容量，用于给平面示意图算比例。"""
    return max(rows, 1) * max(cols, 1)
