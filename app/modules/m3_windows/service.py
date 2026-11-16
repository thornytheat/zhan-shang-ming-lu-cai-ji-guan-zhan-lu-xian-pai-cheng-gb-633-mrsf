"""时间窗服务：从议程页取场次，做冲突判定与页表拼装。

本模块只判时间，不看展位；交叉判定（论坛窗 × 展位可达）在 M4 排程里。
"""
from __future__ import annotations

from . import agenda, conflicts, windows


def build(pages: list) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (场次, 冲突行, 原因码行)。"""
    sessions: list[dict] = []
    issues: list[dict] = []
    for page in pages:
        if page.kind != "agenda":
            continue
        page_sessions, page_issues = agenda.sessions_from_page(page)
        sessions.extend(page_sessions)
        issues.extend(page_issues)
    conflicts_rows, conflict_issues = conflicts.detect(sessions)
    issues.extend(conflict_issues)
    return sessions, conflicts_rows, issues


def summary(sessions: list[dict], conflicts_rows: list[dict]) -> dict:
    days = sorted({s["day"] for s in sessions if s["day"]})
    return {
        "sessions": len(sessions),
        "conflicts": len(conflicts_rows),
        "days": len(days),
        "halls": len({s["hall"] for s in sessions}),
        "fixed": len(conflicts.fixed_sessions(sessions)),
    }


def session_rows(sessions: list[dict]) -> list[dict]:
    rows = []
    for session in sessions:
        window = windows.window(session["start_utc"], session["end_utc"])
        rows.append({
            "session_id": session["session_id"], "title": session["title"],
            "hall": session["hall"], "day": session["day"],
            "start": session["start_utc"], "end": session["end_utc"],
            "start_local": windows.hhmm(windows.local_minutes(session["start_utc"])),
            "end_local": windows.hhmm(windows.local_minutes(session["end_utc"])),
            "minutes": window["minutes"], "tz": session["tz_raw"],
            "code": session.get("code") or session.get("primary_code") or "—",
            "detail": session.get("detail")
            or session.get("conflict_detail") or "—",
            "row_ref": session["row_ref"],
        })
    return rows


def conflict_rows(conflicts_rows: list[dict]) -> list[dict]:
    rows = []
    for item in conflicts_rows:
        start_min = windows.local_minutes(item["start_utc"])
        end_min = windows.local_minutes(item["end_utc"])
        rows.append({
            "kind": item["kind"], "ref": item["ref"], "title": item["title"],
            "hall": item["hall"], "day": item["day"],
            "span": f"{windows.hhmm(start_min)}–{windows.hhmm(end_min)}",
            "code": item["code"], "codes": " ".join(item["codes"]),
            "detail": item["detail"] or "—", "row_ref": item["row_ref"],
            "source_url": item["source_url"],
        })
    return rows


def distribution(issues: list[dict]) -> list[dict]:
    """原因码分布：给隔离区页面的第一张表。"""
    buckets: dict[str, dict] = {}
    for item in issues:
        bucket = buckets.setdefault(item["code"], {
            "code": item["code"], "level": item["level"], "count": 0})
        bucket["count"] += 1
    return sorted(buckets.values(), key=lambda row: (-row["count"], row["code"]))
