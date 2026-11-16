"""排程判定纯规则：候选状态、首选时段、固定窗优先级。

这些函数只吃普通数据结构，不碰数据库，便于单测钉死边界。
"""
from __future__ import annotations

from ... import config, reasons

SCHEDULED = "SCHEDULED"
RESCHEDULED = "RESCHEDULED"
CONFLICT = "CONFLICT"
SKIPPED = "SKIPPED"

# 候选前置判定顺序：命中前面一条就不再往下判（TRAP-19）
EARLY_ORDER = ("P_NO_POSITION", "P_OUTSIDE_HOURS", "P_OVERLAP_FIXED")


def preferred_slot(priority: int) -> tuple[int, int] | None:
    """优先级 1 → 上午首选；2 → 下午首选；3 及以后不限时段。"""
    if priority <= 1:
        return config.PREFERRED_WINDOW[1]
    if priority == 2:
        return (13 * 60, 15 * 60)
    return None


def in_slot(minutes: int | None, slot: tuple[int, int] | None) -> bool:
    if slot is None:
        return True
    if minutes is None:
        return False
    start, end = slot
    return start <= minutes < end


def judge(arrival_minutes: int | None, priority: int) -> tuple[str, str | None]:
    """已放入的候选是「排入」还是「改期」。"""
    slot = preferred_slot(priority)
    if slot is None or in_slot(arrival_minutes, slot):
        return SCHEDULED, None
    return RESCHEDULED, "P_SHIFTED"


def early_verdict(candidate: dict) -> tuple[str, str] | None:
    """排程前的硬判定；返回 (状态, 原因码) 或 None（可尝试放入）。"""
    codes: list[str] = []
    if candidate.get("pos") is None:
        codes.append("P_NO_POSITION")
    if candidate.get("outside_hours"):
        codes.append("P_OUTSIDE_HOURS")
    if candidate.get("overlaps_fixed"):
        codes.append("P_OVERLAP_FIXED")
    if not codes:
        return None
    code = pick(codes)
    if code == "P_NO_POSITION":
        return SKIPPED, candidate.get("code") or "P_NO_POSITION"
    return CONFLICT, code


def pick(codes: list[str]) -> str:
    """按 EARLY_ORDER 取第一条，输入顺序不影响结果。"""
    ranked = sorted(set(codes),
                    key=lambda code: (EARLY_ORDER.index(code)
                                      if code in EARLY_ORDER else len(EARLY_ORDER), code))
    return ranked[0]


def label(status: str) -> str:
    return config.CLASSIFY_LABELS.get(status, status)


def reason_title(code: str | None) -> str:
    return reasons.label(code) if code else ""


def sort_key(candidate: dict) -> tuple:
    """改期/排入的顺序键：优先级 → 馆序 → 展位号，全部确定。"""
    return (candidate.get("priority", 5), config.HALL_ORDER.index(candidate["hall"])
            if candidate.get("hall") in config.HALL_ORDER else 99,
            candidate.get("booth_code", ""), candidate.get("ref", ""))
