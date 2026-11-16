"""排程服务：候选装配 → 每日排程 → 状态判定 → 对账。

候选来源只有两处：名录里带坐标的展商、议程里没有冲突的固定场次。
对账恒等式：候选 = 排入 + 改期 + 冲突 + 跳过，四条相加必须等于候选数。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from ... import config
from ..m2_booth import geometry
from ..m3_windows import windows
from . import distance, score, solver

# 馆 → 参观日：1 号馆群放第一天，2 号馆群放第二天，减少跨馆往返
HALL_DAY = {hall: (1 if hall.endswith("1") else 2) for hall in config.HALL_ORDER}
FORUM_PRIORITY = 9


def build_candidates(exhibitors: list[dict], sessions: list[dict]) -> tuple[list, list]:
    """返回 (可排候选, 前置已判定行)。前置判定不再进排程。"""
    candidates: list[dict] = []
    decided: list[dict] = []
    for record in exhibitors:
        day = HALL_DAY.get(record["hall"], 1)
        position = geometry.position(record["hall"], record["row_no"], record["col_no"])
        candidate = {
            "kind": "展商", "ref": record["ex_id"], "name": record["name"],
            "hall": record["hall"], "pos": position,
            "priority": record["priority"], "dwell": config.DWELL_EXHIBITOR_MIN,
            "window_start": config.SHOW_HOURS[day][0],
            "window_end": config.SHOW_HOURS[day][1],
            "day": day, "hard": False, "booth_code": record["booth_code"],
            "code": record["code"], "row_ref": record["row_ref"],
            "record_status": record["status"],
        }
        if record["status"] in ("dup",):
            decided.append(_outcome(candidate, score.SKIPPED,
                                    record["code"] or "Q_DUP_EXHIBITOR",
                                    "同名冲突，未排入"))
            continue
        early = score.early_verdict(candidate)
        if early is not None:
            status, code = early
            decided.append(_outcome(candidate, status, code,
                                    "面积型展位没有网格坐标，未排入"))
            continue
        candidates.append(candidate)

    for session in sessions:
        window = windows.window(session["start_utc"], session["end_utc"])
        start_min = windows.local_minutes(session["start_utc"])
        end_min = windows.local_minutes(session["end_utc"])
        candidate = {
            "kind": "论坛", "ref": session["session_id"], "name": session["title"],
            "hall": session["hall"],
            "pos": geometry.position(session["hall"], 1, 1),
            "priority": FORUM_PRIORITY, "dwell": config.DWELL_FORUM_MIN,
            "window_start": start_min if start_min is not None else config.SHOW_HOURS[1][0],
            "window_end": end_min if end_min is not None else config.SHOW_HOURS[1][1],
            "day": window["day"] or 1, "hard": True, "booth_code": "",
            "code": session.get("code") or session.get("primary_code"),
            "row_ref": session["row_ref"], "record_status": "fixed",
            "outside_hours": (session.get("code")
                              or session.get("primary_code")) == "T_OUTSIDE_HOURS",
            "overlaps_fixed": (session.get("code")
                               or session.get("primary_code")) == "T_OVERLAP",
        }
        if (session.get("code") or session.get("primary_code")) == "T_UNKNOWN_HALL":
            decided.append(_outcome(candidate, score.SKIPPED, "P_NO_POSITION",
                                    "场地不在平面表内"))
            continue
        early = score.early_verdict(candidate)
        if early is not None:
            status, code = early
            decided.append(_outcome(candidate, status, code,
                                    session.get("detail")
                                    or session.get("conflict_detail") or ""))
            continue
        candidates.append(candidate)
    return candidates, decided


def run(exhibitors: list[dict], sessions: list[dict]) -> dict:
    candidates, decided = build_candidates(exhibitors, sessions)
    outcomes = list(decided)
    days: list[dict] = []
    for day in sorted(config.SHOW_DAYS):
        hards = [c for c in candidates if c["hard"] and c["day"] == day]
        softs = sorted([c for c in candidates if not c["hard"] and c["day"] == day],
                       key=score.sort_key)
        result = solver.arrange(
            day, hards, softs,
            prefer=lambda cand: score.preferred_slot(cand["priority"]))
        for candidate in result["rejected_hard"]:
            outcomes.append(_outcome(candidate, score.CONFLICT, "P_OVERLAP_FIXED",
                                     "固定窗放不进当天时间轴"))
        for candidate in result["rejected_soft"]:
            outcomes.append(_outcome(candidate, score.SKIPPED, "P_NO_SLOT",
                                     "当天已排满"))
        for index, row in enumerate(result["rows"]):
            status, code = (score.SCHEDULED, None) if row["hard"] else \
                score.judge(row["arrival"], row["priority"])
            outcome = _outcome(row, status, code, "")
            outcome.update({
                "seq": index + 1,
                "arrival_utc": windows.at_local(day, row["arrival"]).isoformat(),
                "depart_utc": windows.at_local(day, row["depart"]).isoformat(),
                "walk_m": row["walk_m"],
                "in_preferred": status == score.SCHEDULED,
            })
            outcomes.append(outcome)
        days.append({
            "day": day, "rows": result["rows"],
            "walk_m": result["walk_m"], "stops": len(result["rows"]),
            "open": solver.day_open(day), "close": solver.day_close(day),
            "clock": result["clock"], "slack": result["slack"],
            "halls": len({row["hall"] for row in result["rows"]}),
            "fixed": sum(1 for row in result["rows"] if row["hard"]),
        })

    placed = [row for row in outcomes if row["status"] in (score.SCHEDULED,
                                                          score.RESCHEDULED)]
    placed.sort(key=lambda r: (r["plan_day"], r["seq"]))
    rest = [row for row in outcomes if row["status"] not in (score.SCHEDULED,
                                                             score.RESCHEDULED)]
    rest.sort(key=lambda r: (r["plan_day"], r["status"], r["ref"]))
    for index, row in enumerate(rest, 1):
        row["seq"] = 1000 + index
    return {
        "stops": placed + rest,
        "days": days,
        "totals": totals(outcomes, placed),
        "reconciliation": reconciliation(exhibitors, sessions, outcomes, placed, days),
        "route_rows": route_rows(days),
    }


def totals(outcomes: list[dict], placed: list[dict]) -> dict:
    counts = {score.SCHEDULED: 0, score.RESCHEDULED: 0, score.CONFLICT: 0,
              score.SKIPPED: 0}
    for row in outcomes:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    walk = Decimal("0")
    for row in placed:
        walk += row["walk_m"]
    return {
        "candidates": len(outcomes),
        "scheduled": counts[score.SCHEDULED],
        "rescheduled": counts[score.RESCHEDULED],
        "conflict": counts[score.CONFLICT],
        "skipped": counts[score.SKIPPED],
        "walk_total_m": str(geometry._q(walk)),
        "stops": len(placed),
    }


def reconciliation(exhibitors: list[dict], sessions: list[dict], outcomes: list[dict],
                   placed: list[dict], days: list[dict]) -> dict:
    """四账：名录行 / 候选站点 / 议程场次 / 距离。"""
    names = {"ok": 0, "unplaced": 0, "isolated": 0, "dup": 0}
    for record in exhibitors:
        names[record["status"]] = names.get(record["status"], 0) + 1
    catalog_rows = sum(names.values())

    counts = {score.SCHEDULED: 0, score.RESCHEDULED: 0, score.CONFLICT: 0,
              score.SKIPPED: 0}
    for row in outcomes:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    candidate_total = sum(counts.values())

    fixed = sum(1 for s in sessions if not s.get("primary_code"))
    session_conflicts = len(sessions) - fixed

    day_walk = Decimal("0")
    for day in days:
        day_walk += day["walk_m"]
    placed_walk = Decimal("0")
    for row in placed:
        placed_walk += row["walk_m"]

    accounts = [
        {"account": "名录行账",
         "left": catalog_rows,
         "left_label": "名录行数",
         "right": (f"{names['ok']} 可排程 + {names['unplaced']} 无坐标 + "
                   f"{names['isolated']} 隔离 + {names['dup']} 同名冲突"),
         "right_total": names["ok"] + names["unplaced"] + names["isolated"] + names["dup"],
         "balanced": catalog_rows == (names["ok"] + names["unplaced"]
                                      + names["isolated"] + names["dup"])},
        {"account": "候选账",
         "left": candidate_total,
         "left_label": "候选站点",
         "right": (f"{counts[score.SCHEDULED]} 排入 + {counts[score.RESCHEDULED]} 改期 + "
                   f"{counts[score.CONFLICT]} 冲突 + {counts[score.SKIPPED]} 跳过"),
         "right_total": (counts[score.SCHEDULED] + counts[score.RESCHEDULED]
                         + counts[score.CONFLICT] + counts[score.SKIPPED]),
         "balanced": candidate_total == (counts[score.SCHEDULED]
                                         + counts[score.RESCHEDULED]
                                         + counts[score.CONFLICT]
                                         + counts[score.SKIPPED])},
        {"account": "议程场次账",
         "left": len(sessions),
         "left_label": "议程场次",
         "right": f"{fixed} 固定窗 + {session_conflicts} 时间窗隔离",
         "right_total": fixed + session_conflicts,
         "balanced": len(sessions) == fixed + session_conflicts},
        {"account": "距离账",
         "left": str(geometry._q(day_walk)),
         "left_label": "每日合计(米)",
         "right": f"{len(placed)} 个点位分段之和 {geometry._q(placed_walk)} 米",
         "right_total": str(geometry._q(placed_walk)),
         "balanced": geometry._q(day_walk) == geometry._q(placed_walk)},
    ]
    return {
        "accounts": accounts,
        "all_balanced": all(item["balanced"] for item in accounts),
        "note": "候选 = 排入 + 改期 + 冲突 + 跳过；总步行 = 分段之和（米，1 位小数）",
    }


def route_rows(days: list[dict]) -> list[dict]:
    rows = []
    for day in days:
        for index, row in enumerate(day["rows"], 1):
            rows.append({
                "day": day["day"], "seq": index, "kind": row["kind"],
                "ref": row["ref"], "name": row["name"], "hall": row["hall"],
                "arrival": windows.at_local(day["day"], row["arrival"]).isoformat(),
                "depart": windows.at_local(day["day"], row["depart"]).isoformat(),
                "arrival_min": row["arrival"], "depart_min": row["depart"],
                "dwell": row["dwell"], "walk_m": row["walk_m"],
                "walk_min": row["walk_min"],
                "status": score.SCHEDULED if not row["hard"] else score.SCHEDULED,
                "code": "—",
            })
    return rows


def stop_rows(outcomes: list[dict]) -> list[dict]:
    """页面用的行：排入的带时段，未排入的带原因码。"""
    rows = []
    for row in outcomes:
        rows.append({
            "day": row["plan_day"], "seq": row["seq"], "kind": row["kind"],
            "ref": row["ref"], "name": row["name"], "hall": row["hall"],
            "arrival": row["arrival_utc"], "depart": row["depart_utc"],
            "dwell": row["dwell"], "walk_m": row["walk_m"],
            "status": row["status"], "code": row["code"] or "—",
            "note": row["note"],
        })
    return rows


def _outcome(candidate: dict, status: str, code: str | None, note: str) -> dict:
    day = candidate.get("day") or 1
    open_min = config.SHOW_HOURS[day][0]
    start = candidate.get("window_start", open_min)
    arrival = windows.at_local(day, start if start is not None else open_min)
    return {
        "kind": candidate["kind"], "ref": candidate["ref"], "name": candidate["name"],
        "hall": candidate["hall"], "plan_day": day, "seq": 0,
        "priority": int(candidate.get("priority") or 5),
        "hard": bool(candidate.get("hard")),
        "status": status, "code": code, "note": note,
        "dwell": candidate.get("dwell", 0),
        "dwell_min": candidate.get("dwell", 0),
        "walk_m": Decimal("0.0"),
        "arrival_utc": arrival.isoformat(),
        "depart_utc": arrival.isoformat(),
        "in_preferred": False,
    }


def clock_text(minutes: int | None) -> str:
    return windows.hhmm(minutes)


def day_label(day: int) -> str:
    date_value = config.SHOW_DAYS.get(day)
    return f"第 {day} 天 · {date_value.isoformat()}" if date_value else f"第 {day} 天"


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None
