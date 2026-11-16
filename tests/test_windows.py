"""M3 时间窗：时区归一、半开区间、跨零点、重叠判定与规则优先级。"""
from __future__ import annotations

from app.modules.m3_windows import agenda, conflicts, windows


def session(ref, hall, start, end):
    return {"session_id": ref, "title": ref, "hall": hall,
            "start_utc": start, "end_utc": end, "day": windows.day_of(start) or 1,
            "tz_raw": "", "source_url": "u", "row_ref": ref, "note": ""}


def test_timezone_offsets_land_same_bucket():
    # TRAP-11：02:00Z 与 10:00+08:00 是同一时刻，必须落同一个桶
    z_form = windows.parse_time("2026-11-16T02:00:00Z")
    offset_form = windows.parse_time("2026-11-16T10:00:00+08:00")
    naive_form = windows.parse_time("2026-11-16 10:00")
    assert z_form["value"] == offset_form["value"] == naive_form["value"]
    assert windows.day_of(z_form["value"]) == 1
    assert naive_form["code"] == "T_NAIVE_TIME"


def test_mixed_timezone_recorded(assembled):
    codes = [item["code"] for item in assembled["window_issues"]]
    assert "T_TZ_MIXED" in codes


def test_half_open_endpoints_touch_not_overlap():
    # TRAP-23：09:30-10:30 与 10:30-11:15 端点相接，不算冲突
    left = windows.window("2026-11-16T01:30:00+00:00", "2026-11-16T02:30:00+00:00")
    right = windows.window("2026-11-16T02:30:00+00:00", "2026-11-16T03:15:00+00:00")
    assert windows.overlaps(left, right) is False
    assert windows.touches(left, right) is True


def test_one_minute_overlap_detected():
    left = windows.window("2026-11-16T03:00:00+00:00", "2026-11-16T04:00:00+00:00")
    right = windows.window("2026-11-16T02:30:00+00:00", "2026-11-16T03:15:00+00:00")
    assert windows.overlaps(left, right) is True


def test_overlap_pairs_from_sample(assembled):
    pairs = conflicts.overlapping_pairs(assembled["sessions"])
    assert ("F04", "F05") in pairs
    assert ("F06", "F07") in pairs
    assert ("F01", "F05") not in pairs
    assert pairs == sorted(pairs)


def test_cross_midnight_marked_outside_hours(assembled):
    # TRAP-11/23：23:30 → 次日 00:30 跨天，必须显式标记而不是当成重叠
    night = [item for item in assembled["conflicts"] if item["ref"] == "F12"]
    assert night
    assert night[0]["code"] == "T_OUTSIDE_HOURS"
    assert "跨天" in night[0]["detail"]


def test_after_close_marked_outside_hours(assembled):
    late = [item for item in assembled["conflicts"] if item["ref"] == "F11"]
    assert late and late[0]["code"] == "T_OUTSIDE_HOURS"


def test_conflict_count_matches_expected(assembled, expected):
    assert len(assembled["sessions"]) == expected["sessions"]
    assert len(assembled["conflicts"]) == expected["session_conflicts"]
    fixed = conflicts.fixed_sessions(assembled["sessions"])
    assert len(fixed) == expected["fixed_sessions"]


def test_rule_priority_independent_of_input_order():
    # TRAP-19：同一条同时命中越界与重叠，先报哪条与输入顺序无关
    codes = ["T_OVERLAP", "T_OUTSIDE_HOURS"]
    assert conflicts.primary(codes) == "T_OUTSIDE_HOURS"
    assert conflicts.primary(list(reversed(codes))) == "T_OUTSIDE_HOURS"
    assert conflicts.primary(["T_OVERLAP"]) == "T_OVERLAP"
    assert conflicts.primary([]) is None


def test_multiple_rules_on_one_session_keeps_primary():
    sessions = [
        session("X1", "A1", "2026-11-16T01:40:00+00:00", "2026-11-16T02:40:00+00:00"),
        session("X2", "A1", "2026-11-16T02:30:00+00:00", "2026-11-16T03:30:00+00:00"),
        session("X3", "A1", "2026-11-16T04:00:00+00:00", "2026-11-16T10:00:00+00:00"),
    ]
    rows, issues = conflicts.detect(sessions)
    by_ref = {row["ref"]: row for row in rows}
    assert by_ref["X1"]["code"] == "T_OVERLAP"
    assert by_ref["X3"]["code"] == "T_OUTSIDE_HOURS"
    assert len(issues) == len(rows)


def test_inverted_window_rejected():
    good = windows.parse_time("2026-11-16T10:00:00+08:00")
    bad = windows.parse_time("2026-11-16T09:00:00+08:00")
    assert bad["value"] < good["value"]
    ok, why = windows.within_hours(bad["value"], good["value"])
    assert ok is True
    assert windows.window(bad["value"], good["value"])["minutes"] == 60


def test_bad_time_formats_rejected():
    assert windows.parse_time("2026-11-16T24:00:00+08:00")["code"] == "T_BAD_TIME"
    assert windows.parse_time("")["code"] == "T_BAD_TIME"
    assert windows.parse_time("十一月十六日")["code"] == "T_BAD_TIME"


def test_agenda_header_mapping():
    mapping = agenda.map_header(["场次", "论坛主题", "会议室", "开始时间", "结束时间"])
    assert mapping["missing"] == []
    mapping = agenda.map_header(["场次", "主题"])
    assert set(mapping["missing"]) == {"场地", "开始", "结束"}


def test_hhmm_and_day_hours():
    assert windows.hhmm(540) == "09:00"
    assert windows.hhmm(None) == "—"
    assert windows.day_hours(1) == (540, 1050)
    assert windows.day_hours(2) == (540, 960)


def test_close_boundary_minute_inclusive():
    """闭馆那一分钟仍算开放：17:30 结束放行，17:31 越界。"""
    ok, why = windows.within_hours("2026-11-16T16:30:00+08:00",
                                   "2026-11-16T17:30:00+08:00")
    assert (ok, why) == (True, "")
    late, detail = windows.within_hours("2026-11-16T16:30:00+08:00",
                                        "2026-11-16T17:31:00+08:00")
    assert late is False
    assert "闭馆" in detail


def test_overlap_is_symmetric_and_endpoint_free():
    """不变量：重叠判定对称，端点相接两边都不算重叠。"""
    left = windows.window("2026-11-16T10:00:00+08:00", "2026-11-16T10:30:00+08:00")
    right = windows.window("2026-11-16T10:30:00+08:00", "2026-11-16T11:15:00+08:00")
    inside = windows.window("2026-11-16T10:10:00+08:00", "2026-11-16T10:20:00+08:00")
    assert windows.overlaps(left, right) is False
    assert windows.overlaps(right, left) is False
    assert windows.touches(left, right) is True
    assert windows.overlaps(left, inside) is True
    assert windows.overlaps(inside, left) is True
