"""时间窗：解析时刻、归一到 UTC、按展期分桶、半开区间比较。

三条硬规则：
1. 带偏移的按偏移解释，不带的按 +08:00 解释，两者先归一到 UTC 再比；
2. 时间窗左闭右开 [start, end)，端点相接不算冲突（TRAP-23）；
3. 结束不晚于开始直接隔离，不靠倒挂推断跨零点。
"""
from __future__ import annotations

import datetime as dt
import re

from ... import config

ISO_Z_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:?\d{2})?$")
SLASH_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$")
BAD_MINUTE_RE = re.compile(r"\b(24|2[5-9]):\d{2}\b")


def parse_time(raw: str | None) -> dict:
    """返回 {ok, value(utc), tz_raw, naive, code}。"""
    text = (raw or "").strip()
    out = {"ok": False, "value": None, "tz_raw": "", "naive": False, "code": None,
           "raw": text}
    if not text:
        out["code"] = "T_BAD_TIME"
        return out
    flat = text.replace("　", " ").strip()
    if BAD_MINUTE_RE.search(flat):
        out["code"] = "T_BAD_TIME"
        return out
    match = ISO_Z_RE.match(flat)
    if match:
        date_part, clock_part, offset = match.groups()
        if len(clock_part) == 5:
            clock_part += ":00"
        try:
            local = dt.datetime.fromisoformat(f"{date_part}T{clock_part}")
        except ValueError:
            out["code"] = "T_BAD_TIME"
            return out
        out["tz_raw"] = offset or ""
        if offset:
            out["value"] = _apply_offset(local, offset)
        else:
            out["naive"] = True
            out["value"] = local.replace(tzinfo=config.CHINA_TZ).astimezone(config.UTC)
            out["code"] = "T_NAIVE_TIME"
        out["ok"] = True
        return out
    match = SLASH_RE.match(flat)
    if match:
        year, month, day, hour, minute, second = match.groups()
        try:
            local = dt.datetime(int(year), int(month), int(day), int(hour),
                                int(minute), int(second or 0))
        except ValueError:
            out["code"] = "T_BAD_TIME"
            return out
        out["naive"] = True
        out["tz_raw"] = ""
        out["value"] = local.replace(tzinfo=config.CHINA_TZ).astimezone(config.UTC)
        out["code"] = "T_NAIVE_TIME"
        out["ok"] = True
        return out
    out["code"] = "T_BAD_TIME"
    return out


def _apply_offset(local: dt.datetime, offset: str) -> dt.datetime:
    if offset.upper() == "Z":
        return local.replace(tzinfo=config.UTC)
    sign = 1 if offset[0] == "+" else -1
    body = offset[1:].replace(":", "")
    hours, minutes = int(body[:2]), int(body[2:4] or 0)
    tz = dt.timezone(sign * dt.timedelta(hours=hours, minutes=minutes))
    return local.replace(tzinfo=tz).astimezone(config.UTC)


def to_local(value: dt.datetime | str | None) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.UTC)
    return value.astimezone(config.CHINA_TZ)


def local_minutes(value: dt.datetime | str | None) -> int | None:
    local = to_local(value)
    if local is None:
        return None
    return local.hour * 60 + local.minute


def day_of(value: dt.datetime | str | None) -> int | None:
    local = to_local(value)
    if local is None:
        return None
    for day, date_value in config.SHOW_DAYS.items():
        if local.date() == date_value:
            return day
    return None


def window(start_utc, end_utc) -> dict:
    """构造一个时间窗；返回分钟数便于排程比较。"""
    start = start_utc if isinstance(start_utc, dt.datetime) else dt.datetime.fromisoformat(start_utc)
    end = end_utc if isinstance(end_utc, dt.datetime) else dt.datetime.fromisoformat(end_utc)
    return {
        "start_utc": start.astimezone(config.UTC),
        "end_utc": end.astimezone(config.UTC),
        "day": day_of(start),
        "minutes": int((end - start).total_seconds() // 60),
    }


def overlaps(a: dict, b: dict) -> bool:
    """半开区间相交：端点相接返回 False（TRAP-23）。"""
    return a["start_utc"] < b["end_utc"] and b["start_utc"] < a["end_utc"]


def touches(a: dict, b: dict) -> bool:
    return a["end_utc"] == b["start_utc"] or b["end_utc"] == a["start_utc"]


def within_hours(start_utc, end_utc) -> tuple[bool, str]:
    """时间窗必须整体落在展期开放时间内，且两端同一天。"""
    day = day_of(start_utc)
    if day is None:
        return False, "开始时间不在展期"
    end_day = day_of(end_utc)
    if end_day != day:
        return False, f"跨天（第 {day} 天 → 第 {end_day} 天）"
    open_min, close_min = config.SHOW_HOURS[day]
    start_min = local_minutes(start_utc)
    end_min = local_minutes(end_utc)
    if start_min < open_min:
        return False, f"{open_min // 60:02d}:{open_min % 60:02d} 前未开放"
    if end_min > close_min:
        return False, f"{close_min // 60:02d}:{close_min % 60:02d} 后已闭馆"
    return True, ""


def hhmm(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def at_local(day: int, minutes: int) -> dt.datetime:
    """某天本地第 N 分钟 → UTC 时刻。"""
    date_value = config.SHOW_DAYS[day]
    local = dt.datetime(date_value.year, date_value.month, date_value.day,
                        minutes // 60, minutes % 60, tzinfo=config.CHINA_TZ)
    return local.astimezone(config.UTC)


def day_hours(day: int) -> tuple[int, int]:
    return config.SHOW_HOURS[day]


def preferred(day: int) -> tuple[int, int]:
    return config.PREFERRED_WINDOW[day]
