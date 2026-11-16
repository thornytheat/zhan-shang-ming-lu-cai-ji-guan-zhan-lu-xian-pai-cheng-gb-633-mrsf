"""M7 备选方案服务：同一批候选按三套规则各排一次，再与主方案比损失。

三套方案（都吃同一批候选，前置分类原样继承 M4）：

- A 主方案：沿用 M4 的插入顺序，跨馆不限；
- B 减站换权重：排完之后，对没排进去的高权重点位，按权重升序放弃低权重站点
  腾时段；只有「换进来更重」才接受，所以 B 的权重合计不会低于 A；
- C 限跨馆：按馆聚类插入，并守住每天跨馆次数上限（超限给 M7_HALL_CAP）。

因为候选集合相同、只是取舍规则不同，每个方案都满足
「排入权重 + 未排入权重 = 候选权重合计」（见 `build()["invariant"]`）。
时间轴按 `directed.simulate` 推：换乘分钟计入到站，距离按方向逐段相加。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config, reasons
from ..m3_windows import windows
from ..m4_route import score
from ..m4_route import service as route_service
from . import directed, limits

PLANS = (
    {"id": "A", "title": "主方案", "order": "priority", "swap": False,
     "use_cap": False},
    {"id": "B", "title": "减站换权重", "order": "priority", "swap": True,
     "use_cap": False},
    {"id": "C", "title": "限跨馆", "order": "hall", "swap": False,
     "use_cap": True},
)
PLACED = (score.SCHEDULED, score.RESCHEDULED)


def build(exhibitors: list[dict], sessions: list[dict], *,
          day: int | None = None, cap: int | None = None) -> dict:
    cap = config.MAX_HALL_SWITCHES if cap is None else int(cap)
    candidates, decided = route_service.build_candidates(exhibitors, sessions)
    days = [key for key in sorted(config.SHOW_DAYS) if day in (None, key)]
    candidates = [row for row in candidates if row["day"] in days]
    decided = [row for row in decided if row.get("plan_day") in days]

    plans = [_plan(spec, candidates, decided, days, cap) for spec in PLANS]
    comparison = [limits.compare(plans[0], other) for other in plans[1:]]
    symmetry = directed.symmetry_audit()
    total_weight = _candidate_weight(candidates, decided)
    return {
        "day": day,
        "cap": cap,
        "days": days,
        "symmetry": symmetry,
        "plans": plans,
        "comparison": comparison,
        "invariant": {
            "total_weight": total_weight,
            "balanced": all(item["totals"]["weight"] + item["totals"]["weight_rest"]
                            == total_weight for item in plans),
        },
        "codes": _code_rows(plans, symmetry),
    }


def plan_by_id(result: dict, plan_id: str | None) -> dict:
    wanted = (plan_id or "A").upper()
    for plan in result["plans"]:
        if plan["id"] == wanted:
            return plan
    return result["plans"][0]


def plan_rows(plan: dict, day: int | None = None) -> list[dict]:
    """页面与接口共用：把分钟数换成时段字符串。"""
    rows = []
    for row in plan["route"]:
        if day is not None and row["day"] != day:
            continue
        rows.append({**row,
                     "slot": f"{windows.hhmm(row['arrival'])}"
                             f"–{windows.hhmm(row['depart'])}",
                     "status_label": config.CLASSIFY_LABELS.get(row["status"],
                                                                row["status"])})
    return rows


def unplaced_rows(plan: dict) -> list[dict]:
    rows = []
    for row in plan["unplaced"]:
        rows.append({**row,
                     "status_label": config.CLASSIFY_LABELS.get(row["status"],
                                                                row["status"]),
                     "code_title": reasons.label(row["code"]) if row.get("code") else "—"})
    rows.sort(key=lambda row: (row["day"], row["status"], row["ref"]))
    return rows


def _plan(spec: dict, candidates: list[dict], decided: list[dict],
          days: list[int], cap: int) -> dict:
    use_cap = cap if spec["use_cap"] else None
    unplaced: list[dict] = [_unplaced_row(row, row["status"], row.get("code"),
                                          row.get("note") or "")
                            for row in decided]
    day_rows: list[dict] = []
    route_out: list[dict] = []
    placed: list[dict] = []
    two_opt_moves = 0
    for day in days:
        hards = [row for row in candidates if row["hard"] and row["day"] == day]
        softs = sorted([row for row in candidates if not row["hard"]
                        and row["day"] == day], key=limits.order_key(spec["order"]))
        route, rejected_hard, rejected_soft = _arrange_day(day, hards, softs, use_cap)
        if spec["swap"]:
            route, rejected_soft = _rebalance(day, route, rejected_soft, use_cap)
        route, dropped = limits.enforce(route, use_cap)
        route, moves = directed.two_opt(day, route, use_cap)
        two_opt_moves += moves
        for candidate in dropped:
            rejected_soft.append({"candidate": candidate, "code": "M7_HALL_CAP",
                                  "note": f"当天换馆已达 {use_cap} 次上限"})
        for candidate in rejected_hard:
            unplaced.append(_unplaced_row(candidate, score.CONFLICT,
                                          "P_OVERLAP_FIXED",
                                          "固定窗放不进当天时间轴"))
        for item in rejected_soft:
            unplaced.append(_unplaced_row(item["candidate"], score.SKIPPED,
                                          item["code"], item["note"]))
        ok, rows = directed.simulate(day, route)
        if not ok:
            raise AssertionError(f"第 {day} 天 {spec['id']} 方案排完不可行")
        day_rows.append({
            "day": day, "stops": len(rows),
            "walk_m": str(directed.total(route)),
            "transfer_min": directed.transfer_minutes(rows),
            "switches": directed.switches(route),
        })
        for index, row in enumerate(rows, 1):
            status, code = (score.SCHEDULED, None) if row["hard"] else \
                score.judge(row["arrival"], row["priority"])
            item = {
                "day": day, "seq": index, "kind": row["kind"], "ref": row["ref"],
                "name": row["name"], "hall": row["hall"],
                "arrival": row["arrival"], "depart": row["depart"],
                "dwell": row["dwell"], "walk_m": row["walk_m"],
                "walk_min": row["walk_min"], "transfer_min": row["transfer_min"],
                "status": status, "code": code or "—",
                "weight": limits.weight(row),
            }
            route_out.append(item)
            placed.append(item)

    totals = _totals(placed, unplaced, day_rows)
    return {
        "id": spec["id"], "title": spec["title"], "order": spec["order"],
        "cap": use_cap, "days": day_rows, "totals": totals,
        "codes": _plan_codes(spec, unplaced, day_rows),
        "two_opt": two_opt_moves,
        "route": route_out, "unplaced": unplaced,
        "note": "站点数与 /plan 略有差异" if spec["id"] == "A" else "",
    }


def _arrange_day(day: int, hards: list[dict], softs: list[dict],
                 cap: int | None) -> tuple[list[dict], list[dict], list[dict]]:
    """一天的插入：固定窗先落位，再由 spec 的顺序插软性站点。"""
    route: list[dict] = []
    rejected_hard: list[dict] = []
    for candidate in sorted(hards, key=lambda row: (row["window_start"],
                                                     row["ref"])):
        placed = _place(day, route, candidate, None, cap)
        if placed is None:
            rejected_hard.append(candidate)
        else:
            route = placed
    rejected_soft: list[dict] = []
    for candidate in softs:
        prefer = score.preferred_slot(candidate["priority"])
        placed = _place(day, route, candidate, prefer, cap)
        if placed is not None:
            route = placed
        elif cap is not None and _time_only_feasible(day, route, candidate):
            rejected_soft.append({"candidate": candidate, "code": "M7_HALL_CAP",
                                  "note": f"当天换馆已达 {cap} 次上限"})
        else:
            rejected_soft.append({"candidate": candidate, "code": "P_NO_SLOT",
                                  "note": "当天已排满"})
    return route, rejected_hard, rejected_soft


def _rebalance(day: int, route: list[dict], rejected_soft: list[dict],
               cap: int | None) -> tuple[list[dict], list[dict]]:
    """减站换权重：只在「换进来更重」时接受，一次换一个。

    被换掉的低权重站点记 M7_ALT_DROP；换进去的候选从「未排入」里移除。
    全程不改候选集合，所以权重守恒（见 build() 的 invariant）。
    """
    out = list(rejected_soft)
    blocked = sorted(out, key=lambda item: (-limits.weight(item["candidate"]),
                                            item["candidate"].get("ref") or ""))
    dropped: list[dict] = []
    for item in blocked:
        candidate = item["candidate"]
        ceiling = limits.weight(candidate)
        if ceiling <= config.DEFAULT_WEIGHT:
            continue
        victims = sorted([stop for stop in route
                          if 0 < limits.weight(stop) < ceiling],
                         key=lambda stop: (limits.weight(stop),
                                           str(stop.get("ref") or "")))
        for victim in victims:
            trial = [stop for stop in route if stop.get("ref") != victim.get("ref")]
            placed = _place(day, trial, candidate,
                            score.preferred_slot(candidate["priority"]), cap)
            if placed is None:
                continue
            route = placed
            dropped.append(victim)
            break
    if not dropped:
        return route, out
    out = [item for item in out
           if item["candidate"].get("ref") not in {str(c.get("ref")) for c in dropped}]
    return route, out + [{"candidate": victim, "code": "M7_ALT_DROP",
                          "note": "腾时段给更重的点位"} for victim in dropped]


def _place(day: int, route: list[dict], candidate: dict,
           prefer: tuple[int, int] | None, cap: int | None) -> list[dict] | None:
    """在所有插入位置里挑一个；上限挡住的插入位置直接淘汰。"""
    options = []
    for index in range(len(route) + 1):
        trial = route[:index] + [candidate] + route[index:]
        ok, rows = directed.simulate(day, trial)
        if not ok or not limits.within_cap(trial, cap):
            continue
        options.append((rows[index]["arrival"], index, trial))
    if not options:
        return None
    if prefer is not None:
        inside = [item for item in options if prefer[0] <= item[0] < prefer[1]]
        if inside:
            return min(inside, key=lambda item: (item[0], item[1]))[2]
    return min(options, key=lambda item: (item[0], item[1]))[2]


def _time_only_feasible(day: int, route: list[dict], candidate: dict) -> bool:
    """不看上限是否放得下：把「上限挡住」和「真的排满」分开（TRAP-27）。"""
    for index in range(len(route) + 1):
        trial = route[:index] + [candidate] + route[index:]
        ok, _rows = directed.simulate(day, trial)
        if ok:
            return True
    return False


def _unplaced_row(candidate: dict, status: str, code: str | None, note: str) -> dict:
    return {
        "kind": candidate.get("kind") or "展商",
        "ref": candidate.get("ref") or "",
        "name": candidate.get("name") or "",
        "hall": candidate.get("hall") or "",
        "day": candidate.get("plan_day") or candidate.get("day") or 0,
        "status": status,
        "code": code or "",
        "note": note,
        "weight": limits.weight(candidate),
    }


def _totals(placed: list[dict], unplaced: list[dict], days: list[dict]) -> dict:
    """权重直接取行上算好的值：候选已被翻过一次，不能再按优先级猜。"""
    walk = Decimal("0")
    for row in placed:
        walk += row["walk_m"]
    return {
        "stops": len(placed),
        "weight": sum(int(row["weight"]) for row in placed),
        "weight_rest": sum(int(row["weight"]) for row in unplaced),
        "walk_m": str(Decimal(walk).quantize(config.DIST_QUANT)),
        "transfer_min": sum(int(row["transfer_min"]) for row in placed),
        "switches": sum(int(day["switches"]) for day in days),
        "dropped": len(unplaced),
    }


def _plan_codes(spec: dict, unplaced: list[dict], days: list[dict]) -> list[str]:
    codes = {row["code"] for row in unplaced if row["code"]}
    if any(row["transfer_min"] for row in days):
        codes.add("M7_TRANSFER")
    if spec["id"] != "A":
        codes.add("M7_ALT_LOSS")
    return sorted(codes)


def _code_rows(plans: list[dict], symmetry: dict) -> list[dict]:
    """页面顶部的结论行：每条原因码在全部方案里出现过几次。"""
    counts: dict[str, int] = {}
    for plan in plans:
        for row in plan["unplaced"]:
            if row["code"]:
                counts[row["code"]] = counts.get(row["code"], 0) + 1
    if symmetry["asymmetric"]:
        counts["M7_ASYMMETRY"] = symmetry["asymmetric"]
    if any(plan["totals"]["transfer_min"] for plan in plans):
        counts["M7_TRANSFER"] = sum(1 for plan in plans
                                    if plan["totals"]["transfer_min"])
    base = plans[0]["totals"]
    shifted = [plan for plan in plans[1:]
               if (plan["totals"]["stops"], plan["totals"]["weight"],
                   plan["totals"]["walk_m"], plan["totals"]["switches"])
               != (base["stops"], base["weight"], base["walk_m"], base["switches"])]
    if shifted:
        counts["M7_ALT_LOSS"] = len(shifted)
    if not counts:
        counts["M7_NO_UNPLACED"] = 1
    rows = []
    for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        reason = reasons.get(code)
        rows.append({"code": code, "count": count, "level": reason.level,
                     "title": reason.title})
    return rows


def _candidate_weight(candidates: list[dict], decided: list[dict]) -> int:
    return limits.total_weight(candidates) + limits.total_weight(decided)


def day_label(day: int) -> str:
    date_value = config.SHOW_DAYS.get(day)
    return f"第 {day} 天 · {date_value.isoformat()}" if date_value else f"第 {day} 天"
