"""时间窗冲突判定：论坛窗两两比较 + 单场时间窗体检。

同一场次可能同时踩中多条规则（越界、倒挂、重叠），判定顺序固定，
换一下输入行序结果不变（TRAP-19 的同族约束）。
"""
from __future__ import annotations

from ... import config, reasons
from . import windows

# 单场冲突的判定顺序：越靠前越先报，与行序无关
RULE_ORDER = ("T_WINDOW_INVERTED", "T_UNKNOWN_HALL", "T_OUTSIDE_HOURS", "T_OVERLAP")


def primary(code_list: list[str]) -> str | None:
    """多条原因码同时命中时取优先级最高的那条。"""
    if not code_list:
        return None
    ranked = sorted(set(code_list),
                    key=lambda code: (RULE_ORDER.index(code)
                                      if code in RULE_ORDER else len(RULE_ORDER), code))
    return ranked[0]


def issue(code: str, *, subject: str = "", detail: str | None = None,
          raw_value: str | None = None, row_ref: str | None = None,
          source_url: str | None = None) -> dict:
    reason = reasons.get(code)
    return {"code": code, "stage": reason.stage, "level": reason.level,
            "subject": subject, "field": "agenda", "raw_value": raw_value,
            "detail": detail or reason.title, "source_url": source_url,
            "row_ref": row_ref}


def detect(sessions: list[dict]) -> tuple[list[dict], list[dict]]:
    """返回 (冲突行, 原因码行)。冲突行按场馆 + 时间排序，结果稳定。"""
    issues: list[dict] = []
    conflicts: list[dict] = []
    for session in sessions:
        window = windows.window(session["start_utc"], session["end_utc"])
        codes: list[str] = []
        detail_bits: list[str] = []
        if window["end_utc"] <= window["start_utc"]:
            codes.append("T_WINDOW_INVERTED")
        if window["day"] is None:
            codes.append("T_OUTSIDE_HOURS")
            detail_bits.append("不在展期日期内")
        else:
            ok, why = windows.within_hours(window["start_utc"], window["end_utc"])
            if not ok:
                codes.append("T_OUTSIDE_HOURS")
                detail_bits.append(why)
        overlap_with = _overlaps_with(session, sessions)
        for other in overlap_with:
            codes.append("T_OVERLAP")
            detail_bits.append(f"与 {other['session_id']} 重叠")
        code = primary(codes)
        session["primary_code"] = code
        session["code"] = code
        session["conflict_detail"] = "；".join(dict.fromkeys(detail_bits))
        session["detail"] = session["conflict_detail"]
        if code is None:
            continue
        issues.append(issue(code, subject=session["session_id"],
                           detail=session["conflict_detail"] or None,
                           row_ref=session["row_ref"],
                           source_url=session["source_url"]))
        conflicts.append({
            "kind": "论坛",
            "ref": session["session_id"],
            "title": session["title"],
            "hall": session["hall"],
            "start_utc": session["start_utc"],
            "end_utc": session["end_utc"],
            "day": session["day"],
            "code": code,
            "codes": list(dict.fromkeys(codes)),
            "detail": session["conflict_detail"],
            "row_ref": session["row_ref"],
            "source_url": session["source_url"],
        })
    issues.sort(key=lambda item: (item["code"], item.get("row_ref") or ""))
    conflicts.sort(key=lambda item: (item["day"], item["start_utc"], item["ref"]))
    return conflicts, issues


def _overlaps_with(session: dict, sessions: list[dict]) -> list[dict]:
    """同馆同天的重叠场次；端点相接不算（TRAP-23）。"""
    target = windows.window(session["start_utc"], session["end_utc"])
    if target["day"] is None:
        return []
    out = []
    for other in sessions:
        if other is session or other["session_id"] == session["session_id"]:
            continue
        if other["hall"] != session["hall"]:
            continue
        window = windows.window(other["start_utc"], other["end_utc"])
        if window["day"] != target["day"]:
            continue
        if windows.overlaps(target, window):
            out.append(other)
    return out


def overlapping_pairs(sessions: list[dict]) -> list[tuple]:
    """两两重叠对，用于页面上的对照展示与测试断言。"""
    pairs = []
    for i in range(len(sessions)):
        for j in range(i + 1, len(sessions)):
            left, right = sessions[i], sessions[j]
            if left["hall"] != right["hall"]:
                continue
            a = windows.window(left["start_utc"], left["end_utc"])
            b = windows.window(right["start_utc"], right["end_utc"])
            if a["day"] != b["day"]:
                continue
            if windows.overlaps(a, b):
                pairs.append((left["session_id"], right["session_id"]))
    return sorted(pairs)


def fixed_sessions(sessions: list[dict]) -> list[dict]:
    """可用于排程的固定场次：没有主冲突码的才算固定窗。"""
    return [s for s in sessions if not (s.get("code") or s.get("primary_code"))]


def busy_slot(session: dict) -> tuple:
    window = windows.window(session["start_utc"], session["end_utc"])
    return (window["day"], window["start_utc"], window["end_utc"])


def hall_order(hall: str) -> int:
    return config.HALL_ORDER.index(hall) if hall in config.HALL_ORDER else len(config.HALL_ORDER)
