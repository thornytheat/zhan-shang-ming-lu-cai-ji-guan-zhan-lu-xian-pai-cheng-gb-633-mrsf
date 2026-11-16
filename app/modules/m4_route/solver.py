"""带时间窗的贪心初排 + 2-opt 改善。

顺序：固定论坛窗先落位（时间钉死，只能整体插入），展商按优先级依次插入最佳位置，
最后对整天序列做 2-opt（反转一段），只在「时间窗仍全部满足且总步行更短」时才接受。
所有比较键都确定，同一输入两次跑出同一条路线。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from . import distance

EPS = Decimal("0.05")
OPEN_OFFSET = 0


def simulate(day: int, stops: list[dict]) -> tuple[bool, list[dict]]:
    """按顺序推时间；任一站的窗口装不下就整体判为不可行。"""
    open_min, close_min = config.SHOW_HOURS[day]
    clock = open_min
    prev_hall, prev_pos = "", distance.ENTRY
    rows: list[dict] = []
    for stop in stops:
        walk = distance.segment(prev_hall, prev_pos, stop["hall"], stop["pos"])
        walk_min = distance.walk_minutes(walk)
        arrive = clock + walk_min
        if arrive < stop["window_start"]:
            arrive = stop["window_start"]
        if arrive + stop["dwell"] > stop["window_end"]:
            return False, []
        if arrive >= close_min:
            return False, []
        depart = arrive + stop["dwell"]
        rows.append({**stop, "arrival": arrive, "depart": depart,
                     "walk_m": walk, "walk_min": walk_min})
        clock = depart
        prev_hall, prev_pos = stop["hall"], stop["pos"]
    if rows and rows[-1]["depart"] > close_min:
        return False, []
    return True, rows


def best_position(day: int, route: list[dict], candidate: dict,
                  prefer: tuple[int, int] | None = None) -> tuple | None:
    """在所有插入位置里选一个。

    有首选时段时先只在「到站落在首选时段内」的位置里挑最早的；
    首选时段确实放不下，才退回到站最早的位置（由调用方标成改期）。
    """
    options = []
    for index in range(len(route) + 1):
        trial = route[:index] + [candidate] + route[index:]
        ok, rows = simulate(day, trial)
        if not ok:
            continue
        options.append((rows[index]["arrival"], index, trial))
    if not options:
        return None
    if prefer is not None:
        inside = [item for item in options if prefer[0] <= item[0] < prefer[1]]
        if inside:
            return min(inside, key=lambda item: (item[0], item[1]))[2]
    return min(options, key=lambda item: (item[0], item[1]))[2]


def two_opt(day: int, route: list[dict], passes: int = 3) -> list[dict]:
    """反转区间改善总步行；窗口不满足就跳过该次反转。"""
    best = list(route)
    best_walk = distance.route_total(best)
    for _ in range(passes):
        improved = False
        size = len(best)
        for i in range(size - 1):
            for j in range(i + 2, size + 1):
                trial = best[:i] + list(reversed(best[i:j])) + best[j:]
                ok, _rows = simulate(day, trial)
                if not ok:
                    continue
                walk = distance.route_total(trial)
                if walk < best_walk - EPS:
                    best, best_walk = trial, walk
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break
    return best


def day_clock(rows: list[dict]) -> tuple[int, int]:
    if not rows:
        return (0, 0)
    return (rows[0]["arrival"], rows[-1]["depart"])


def slack(day: int, rows: list[dict]) -> int:
    """收工到闭馆还剩多少分钟；负数说明排爆了（不该出现）。"""
    _open_min, close_min = config.SHOW_HOURS[day]
    if not rows:
        return close_min - config.SHOW_HOURS[day][0]
    return close_min - rows[-1]["depart"]


def arrange(day: int, hards: list[dict], softs: list[dict],
            prefer=None) -> dict:
    """一天的排程：固定窗先落位，展商按优先级插入，再 2-opt。

    prefer 是取首选时段的函数（入参为候选），不给就只求到站最早。
    """
    route: list[dict] = []
    rejected_hard: list[dict] = []
    rejected_soft: list[dict] = []

    for candidate in sorted(hards, key=lambda c: (c["window_start"], c["ref"])):
        placed = best_position(day, route, candidate)
        if placed is None:
            rejected_hard.append(candidate)
        else:
            route = placed

    for candidate in softs:
        slot = prefer(candidate) if prefer else None
        placed = best_position(day, route, candidate, slot)
        if placed is None:
            rejected_soft.append(candidate)
        else:
            route = placed

    route = two_opt(day, route)
    ok, rows = simulate(day, route)
    if not ok:
        raise AssertionError(f"第 {day} 天 2-opt 后不可行，排程实现有 bug")
    return {
        "day": day,
        "route": route,
        "rows": rows,
        "rejected_hard": rejected_hard,
        "rejected_soft": rejected_soft,
        "walk_m": distance.route_total(route),
        "clock": day_clock(rows),
        "slack": slack(day, rows),
    }


def improvement(hards: list[dict], softs: list[dict], day: int) -> Decimal:
    """相对「不做 2-opt」时省下的米数，用于页面上说明排程收益。"""
    baseline = list(hards) + list(softs)
    ok, _rows = simulate(day, baseline)
    base = distance.route_total(baseline) if ok else Decimal("0.0")
    arranged = arrange(day, hards, softs)
    return base - arranged["walk_m"]


def day_open(day: int) -> int:
    return config.SHOW_HOURS[day][0]


def day_close(day: int) -> int:
    return config.SHOW_HOURS[day][1]
