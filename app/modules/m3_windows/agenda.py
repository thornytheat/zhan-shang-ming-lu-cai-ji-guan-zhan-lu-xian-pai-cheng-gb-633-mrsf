"""论坛议程解析：场次 / 主题 / 场地 / 开始 / 结束。

议程页的时区写法不统一（Z、+08:00、不带偏移），解析后统一进 UTC，
并记录本行用了哪种写法，页面上能看到换算依据（TRAP-11）。
"""
from __future__ import annotations

import re

from . import windows

SESSION_COLUMNS = ("场次", "主题", "场地", "开始", "结束")
COLUMN_ALIASES = {
    "场次": "session_id", "编号": "session_id", "论坛编号": "session_id",
    "主题": "title", "论坛主题": "title", "名称": "title",
    "场地": "hall", "会议室": "hall", "会场": "hall",
    "开始": "start_raw", "开始时间": "start_raw",
    "结束": "end_raw", "结束时间": "end_raw",
}
HALL_HINT_RE = re.compile(r"([A-D])\s*(\d)?\s*(?:馆|会议室|会场)?")


def map_header(header: list[str]) -> dict:
    fields: dict[str, int] = {}
    for index, cell in enumerate(header or []):
        key = COLUMN_ALIASES.get(re.sub(r"\s+", "", cell or ""))
        if key and key not in fields:
            fields[key] = index
    return {"fields": fields,
            "missing": [name for name in SESSION_COLUMNS
                        if COLUMN_ALIASES[name] not in fields]}


def hall_of(value: str | None) -> str | None:
    from ..m2_booth import parse as booth_parse

    return booth_parse.hall_of_raw(value)


def sessions_from_page(page) -> tuple[list[dict], list[dict]]:
    sessions: list[dict] = []
    issues: list[dict] = []
    mapping = map_header(page.header)
    if mapping["missing"]:
        issues.append(_issue("T_BAD_TIME", page,
                             detail="议程表头缺列：" + "、".join(mapping["missing"])))
        return sessions, issues
    fields = mapping["fields"]
    tz_kinds: set[str] = set()
    for index, row in enumerate(page.rows):
        row_ref = f"{page.source}/p{page.page_no}#{index + 2}"
        if len(row) != len(page.header):
            issues.append(_issue("T_BAD_TIME", page, row_ref=row_ref,
                                 raw_value=f"{len(row)} 列",
                                 detail="议程行列数与表头不一致"))
            continue
        session_id = _cell(row, fields, "session_id")
        title = _cell(row, fields, "title")
        if not session_id:
            issues.append(_issue("T_BAD_TIME", page, row_ref=row_ref,
                                 detail="场次号为空"))
            continue
        start = windows.parse_time(_cell(row, fields, "start_raw"))
        end = windows.parse_time(_cell(row, fields, "end_raw"))
        for parsed, label in ((start, "开始"), (end, "结束")):
            if not parsed["ok"]:
                issues.append(_issue(parsed["code"] or "T_BAD_TIME", page,
                                     row_ref=row_ref, raw_value=parsed["raw"],
                                     detail=f"{label}时间无法解析"))
        if not start["ok"] or not end["ok"]:
            continue
        if start["naive"] or end["naive"]:
            tz_kinds.add("naive")
        if start["tz_raw"].upper() == "Z" or end["tz_raw"].upper() == "Z":
            tz_kinds.add("Z")
        if start["tz_raw"].startswith(("+", "-")) or end["tz_raw"].startswith(("+", "-")):
            tz_kinds.add("offset")
        if end["value"] <= start["value"]:
            issues.append(_issue("T_WINDOW_INVERTED", page, row_ref=row_ref,
                                 raw_value=f"{_cell(row, fields, 'start_raw')} → "
                                           f"{_cell(row, fields, 'end_raw')}",
                                 detail="结束不晚于开始"))
            continue
        hall = hall_of(_cell(row, fields, "hall"))
        window = windows.window(start["value"], end["value"])
        if hall is None:
            issues.append(_issue("T_UNKNOWN_HALL", page, row_ref=row_ref,
                                 raw_value=_cell(row, fields, "hall"),
                                 detail="场地不在 8 馆平面表内"))
            continue
        ok_hours, why = windows.within_hours(window["start_utc"], window["end_utc"])
        if not ok_hours:
            issues.append(_issue("T_OUTSIDE_HOURS", page, row_ref=row_ref,
                                 raw_value=f"{_cell(row, fields, 'start_raw')} → "
                                           f"{_cell(row, fields, 'end_raw')}",
                                 detail=why))
        sessions.append({
            "session_id": session_id, "title": title, "hall": hall,
            "start_utc": window["start_utc"].isoformat(),
            "end_utc": window["end_utc"].isoformat(),
            "day": window["day"] or 0,
            "tz_raw": f"{start['tz_raw'] or '本地'}/{end['tz_raw'] or '本地'}",
            "source_url": page.url, "row_ref": row_ref,
            "note": "" if ok_hours else why,
        })
    if len(tz_kinds) > 1:
        issues.append(_issue("T_TZ_MIXED", page,
                             detail="同一页出现 " + "/".join(sorted(tz_kinds)) + " 写法"))
    elif "naive" in tz_kinds:
        issues.append(_issue("T_NAIVE_TIME", page, detail="时间缺偏移，按 +08:00 解释"))
    return sessions, issues


def _cell(row: list[str], fields: dict, name: str) -> str:
    index = fields.get(name)
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def _issue(code: str, page, *, detail: str, row_ref: str | None = None,
           raw_value: str | None = None) -> dict:
    from ... import reasons

    reason = reasons.get(code)
    return {"code": code, "stage": reason.stage, "level": reason.level,
            "subject": page.url, "field": "agenda", "raw_value": raw_value,
            "detail": detail, "source_url": page.url, "row_ref": row_ref}


def rows_for_page(sessions: list[dict]) -> list[dict]:
    rows = []
    for session in sessions:
        rows.append({
            "session_id": session["session_id"], "title": session["title"],
            "hall": session["hall"],
            "start": session["start_utc"], "end": session["end_utc"],
            "day": session["day"], "tz": session["tz_raw"],
            "row_ref": session["row_ref"], "note": session["note"],
        })
    return rows
