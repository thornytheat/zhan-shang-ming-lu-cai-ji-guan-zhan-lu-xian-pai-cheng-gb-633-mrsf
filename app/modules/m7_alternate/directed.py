"""方向化距离与换乘：M7 备选方案的距离 / 时间规则。

与 M4 的距离有三处不同，每一处都是朴素实现会算错的地方：

- 二层连廊是单向扶梯：A2→B2 直行，B2→A2 要下到一层绕中央通道再上来，多 60 米。
  往返距离本来就不相等，所以距离函数有方向；2-opt 反转一个区间会翻转区间内
  每条边的方向，合计必须按方向逐段相加，不能拿对称表反推（TRAP-28）。
- 跨馆要多花一次换乘（安检 + 扶梯）4 分钟，这 4 分钟必须并入到站时刻，
  不能只把步行分钟排进去（TRAP-27 的对偶：只算步行的排程会在跨馆那天全线晚点）。
- 同一天跨馆次数有上限，插入位置要连上限一起判，见 `limits.within_cap`。

本文件的函数只吃普通数据结构，不碰数据库，便于单测钉死边界。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m2_booth import geometry

ENTRY = config.DEFAULT_ENTRY


def walk_m(from_hall: str | None, from_pos: tuple | None,
           to_hall: str | None, to_pos: tuple | None) -> Decimal:
    """一段步行的米数；单向通道只在「去程」多算，回程另算。"""
    base = geometry.distance(from_hall or "", from_pos, to_hall or "", to_pos)
    extra = config.ONE_WAY_EXTRA_M.get((from_hall, to_hall))
    if extra is None:
        return base
    return geometry._q(base + extra)


def leg(from_hall: str | None, from_pos: tuple | None,
        to_hall: str | None, to_pos: tuple | None) -> dict:
    """一段的可执行成本：米、步行分钟、换乘分钟、合计分钟。

    入口（from_hall 为空）不算换乘；同馆也不算换乘。
    """
    value = walk_m(from_hall, from_pos, to_hall, to_pos)
    transfer = 0 if (not from_hall or from_hall == to_hall) else config.TRANSFER_MIN
    walk_min = geometry.walk_minutes(value)
    return {"walk_m": value, "walk_min": walk_min,
            "transfer_min": transfer, "minutes": walk_min + transfer}


def simulate(day: int, stops: list[dict]) -> tuple[bool, list[dict]]:
    """按顺序推时间：换乘分钟计入到站，任一站的窗口装不下就整体不可行。"""
    open_min, close_min = config.SHOW_HOURS[day]
    clock = open_min
    prev_hall: str = ""
    prev_pos = ENTRY
    rows: list[dict] = []
    for stop in stops:
        item = leg(prev_hall, prev_pos, stop["hall"], stop["pos"])
        arrive = clock + item["minutes"]
        if arrive < stop["window_start"]:
            arrive = stop["window_start"]
        if arrive + stop["dwell"] > stop["window_end"]:
            return False, []
        if arrive >= close_min:
            return False, []
        depart = arrive + stop["dwell"]
        rows.append({**stop, "arrival": arrive, "depart": depart,
                     "walk_m": item["walk_m"], "walk_min": item["walk_min"],
                     "transfer_min": item["transfer_min"]})
        clock = depart
        prev_hall, prev_pos = stop["hall"], stop["pos"]
    if rows and rows[-1]["depart"] > close_min:
        return False, []
    return True, rows


def total(stops: list[dict]) -> Decimal:
    """按方向逐段相加，量化到 0.1 米。"""
    acc = Decimal("0")
    prev_hall: str = ""
    prev_pos = ENTRY
    for stop in stops:
        acc += walk_m(prev_hall, prev_pos, stop["hall"], stop["pos"])
        prev_hall, prev_pos = stop["hall"], stop["pos"]
    return geometry._q(acc)


def switches(stops: list[dict]) -> int:
    """顺序里的换馆次数；同馆连续不算。"""
    changes = 0
    prev = ""
    for stop in stops:
        if prev and stop["hall"] != prev:
            changes += 1
        prev = stop["hall"]
    return changes


def transfer_minutes(rows: list[dict]) -> int:
    return sum(int(row.get("transfer_min") or 0) for row in rows)


def two_opt(day: int, route: list[dict],
            cap: int | None = None) -> tuple[list[dict], int]:
    """带方向的 2-opt：只反转区间，每次按新顺序重算合计。

    M4 的 2-opt 用对称距离的增量公式判收益，这里不能照搬：反转区间会把
    区间内每条边的方向翻过来，A2→B2 与 B2→A2 差 60 米，增量公式会算反
    （TRAP-28）。所以每试一次都重新 simulate + total，只在实际更短且时间窗
    仍满足时接受；给了 cap 还要复核反转后的跨馆次数没超限，否则只省米却破上限。
    返回（新顺序, 接受的移动次数），移动次数单调不减、总里程严格下降，循环收敛。
    """
    best = list(route)
    ok, _rows = simulate(day, best)
    if not ok or len(best) < 3:
        return best, 0
    best_m = total(best)
    moves = 0
    improved = True
    while improved:
        improved = False
        for left in range(len(best) - 1):
            for right in range(left + 1, len(best)):
                trial = (best[:left] + list(reversed(best[left:right + 1]))
                         + best[right + 1:])
                feasible, _rows = simulate(day, trial)
                if not feasible:
                    continue
                if cap is not None and switches(trial) > cap:
                    continue
                value = total(trial)
                if value < best_m:
                    best, best_m = trial, value
                    moves += 1
                    improved = True
                    break
            if improved:
                break
    return best, moves


def pair(a_hall: str, b_hall: str) -> dict:
    """一对馆锚点（1 排 1 位）的双向距离，用于核验表。"""
    a_pos = geometry.position(a_hall, 1, 1)
    b_pos = geometry.position(b_hall, 1, 1)
    forward = walk_m(a_hall, a_pos, b_hall, b_pos)
    back = walk_m(b_hall, b_pos, a_hall, a_pos)
    return {"from": a_hall, "to": b_hall,
            "forward_m": forward, "back_m": back,
            "delta_m": geometry._q(forward - back),
            "ok": geometry._q(forward - back) == Decimal("0.0")}


def symmetry_audit() -> dict:
    """全馆两两核验往返距离：不相等的对必须点出来，不能默认对称。"""
    rows = []
    halls = list(config.HALL_ORDER)
    for index, left in enumerate(halls):
        for right in halls[index + 1:]:
            item = pair(left, right)
            if not item["ok"]:
                rows.append({**item, "forward_m": str(item["forward_m"]),
                             "back_m": str(item["back_m"]),
                             "delta_m": str(item["delta_m"])})
    checked = len(halls) * (len(halls) - 1) // 2
    return {
        "checked": checked,
        "asymmetric": len(rows),
        "ok": not rows,
        "rows": sorted(rows, key=lambda row: (row["from"], row["to"])),
    }
