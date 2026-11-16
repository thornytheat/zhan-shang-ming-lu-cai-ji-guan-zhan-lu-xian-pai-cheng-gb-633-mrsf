"""站点权重与上限：谁该被放弃、一天跨几次馆、两套方案差在哪。

三条规则都要能被单独钉死：

- 权重：优先级 1 最高（5），5 最低（1）；固定论坛窗权重 0，不可放弃。
  放弃顺序先按权重升序，权重相同按编号升序，结果与输入行序无关。
- 跨馆上限：同一天换馆次数超过上限的插入位置一律不选。没有位置可行时
  先判「是不是上限挡住的」——是就给 M7_HALL_CAP，别混成 P_NO_SLOT（TRAP-27）。
- 超限裁剪：固定窗自身就超限时从软性站点里按权重升序放弃，直到不超；
  裁剪只动软性站点，固定窗永远保留。
"""
from __future__ import annotations

from ... import config

FIXED_KIND = "论坛"


def weight(candidate: dict) -> int:
    """站点权重；固定论坛窗不可放弃，权重记 0。"""
    if candidate.get("hard") or candidate.get("kind") == FIXED_KIND:
        return config.FIXED_WEIGHT
    return config.WEIGHT_BY_PRIORITY.get(int(candidate.get("priority") or 5),
                                         config.DEFAULT_WEIGHT)


def total_weight(rows: list[dict]) -> int:
    return sum(weight(row) for row in rows)


def within_cap(stops: list[dict], cap: int | None) -> bool:
    if cap is None:
        return True
    from . import directed

    return directed.switches(stops) <= cap


def drop_order(stops: list[dict]) -> list[dict]:
    """可以放弃的站点顺序：软性在前，权重升序，编号升序。"""
    soft = [stop for stop in stops if weight(stop) > 0]
    return sorted(soft, key=lambda stop: (weight(stop), str(stop.get("ref") or "")))


def enforce(stops: list[dict], cap: int | None) -> tuple[list[dict], list[dict]]:
    """按上限裁剪：先放弃低权重软性站点，固定窗保持不动。"""
    from . import directed

    if cap is None or directed.switches(stops) <= cap:
        return list(stops), []
    kept = list(stops)
    dropped: list[dict] = []
    for candidate in drop_order(stops):
        if directed.switches(kept) <= cap:
            break
        kept = [stop for stop in kept if stop.get("ref") != candidate.get("ref")]
        dropped.append(candidate)
    return kept, dropped


def order_key(mode: str):
    """三套插入顺序：优先级 / 权重 / 按馆聚类。全部确定性。"""
    if mode == "weight":
        return lambda cand: (-weight(cand), cand.get("ref") or "")
    if mode == "hall":
        return lambda cand: (config.HALL_ORDER.index(cand["hall"])
                             if cand.get("hall") in config.HALL_ORDER else 99,
                             cand.get("window_start", 0),
                             cand.get("ref") or "")
    return lambda cand: (int(cand.get("priority") or 5),
                         config.HALL_ORDER.index(cand["hall"])
                         if cand.get("hall") in config.HALL_ORDER else 99,
                         cand.get("booth_code") or "", cand.get("ref") or "")


def compare(base: dict, other: dict) -> dict:
    """两套方案的差额：站点、权重、步行、换乘、跨馆次数。"""
    return {
        "plan": other["id"],
        "title": other["title"],
        "delta_stops": other["totals"]["stops"] - base["totals"]["stops"],
        "delta_weight": other["totals"]["weight"] - base["totals"]["weight"],
        "delta_walk_m": str(_q(other["totals"]["walk_m"], base["totals"]["walk_m"])),
        "delta_transfer_min": (other["totals"]["transfer_min"]
                               - base["totals"]["transfer_min"]),
        "delta_switches": other["totals"]["switches"] - base["totals"]["switches"],
        "codes": list(other["codes"]),
    }


def _q(left, right):
    from decimal import Decimal

    return (Decimal(str(left)) - Decimal(str(right))).quantize(Decimal("0.1"))
