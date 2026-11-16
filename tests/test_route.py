"""M4 排程：候选装配、带窗 2-opt、距离与对账恒等式。"""
from __future__ import annotations

from decimal import Decimal

from app import config
from app.modules.m2_booth import geometry
from app.modules.m4_route import distance, score, service, solver


def stop(ref, hall, row, col, *, priority=3, dwell=20, day=1, hard=False):
    return {
        "kind": "论坛" if hard else "展商", "ref": ref, "name": ref, "hall": hall,
        "pos": geometry.position(hall, row, col), "priority": priority,
        "dwell": dwell, "window_start": config.SHOW_HOURS[day][0],
        "window_end": config.SHOW_HOURS[day][1], "day": day, "hard": hard,
        "booth_code": "", "code": None, "row_ref": ref, "record_status": "ok",
    }


def test_reconciliation_is_balanced(assembled):
    """不变量：候选 = 排入 + 改期 + 冲突 + 跳过。"""
    plan = assembled["plan"]
    totals = plan["totals"]
    assert totals["candidates"] == (totals["scheduled"] + totals["rescheduled"]
                                    + totals["conflict"] + totals["skipped"])
    assert plan["reconciliation"]["all_balanced"] is True
    assert len(plan["reconciliation"]["accounts"]) == 4


def test_candidate_totals_match_expected(assembled, expected):
    totals = assembled["plan"]["totals"]
    assert totals["candidates"] == expected["candidates"]
    assert totals["scheduled"] == expected["scheduled"]
    assert totals["rescheduled"] == expected["rescheduled"]
    assert totals["conflict"] == expected["conflict"]
    assert totals["skipped"] == expected["skipped"]
    assert totals["stops"] == expected["placed_stops"]
    assert totals["walk_total_m"] == expected["walk_total_m"]


def test_two_opt_shortens_without_breaking_windows():
    # 2-opt 只在窗口仍满足且总步行更短时接受
    a = stop("a", "A1", "01", "01")
    b = stop("b", "D1", "14", "20")
    c = stop("c", "A1", "01", "02")
    baseline = distance.route_total([a, b, c])
    result = solver.arrange(1, [], [a, b, c])
    assert result["walk_m"] < baseline
    ok, rows = solver.simulate(1, result["route"])
    assert ok is True
    for row in rows:
        assert row["window_start"] <= row["arrival"]
        assert row["arrival"] + row["dwell"] <= row["window_end"]
    assert [row["ref"] for row in result["route"]] == ["a", "c", "b"]


def test_two_opt_keeps_fixed_window():
    fixed = stop("f", "B1", "01", "02", dwell=35, hard=True)
    fixed["window_start"] = 600
    fixed["window_end"] = 660
    soft = stop("s", "A1", "01", "01")
    result = solver.arrange(1, [fixed], [soft])
    ok, rows = solver.simulate(1, result["route"])
    assert ok is True
    placed = {row["ref"]: row for row in rows}
    assert 600 <= placed["f"]["arrival"]
    assert placed["f"]["arrival"] + 35 <= 660


def test_distance_cross_hall_penalty(expected):
    # 同馆 8 米；跨馆另加 150 米通道折算
    same = geometry.distance("A1", geometry.position("A1", "01", "01"),
                             "A1", geometry.position("A1", "01", "02"))
    cross = geometry.distance("A1", geometry.position("A1", "01", "01"),
                              "B1", geometry.position("B1", "01", "01"))
    assert str(same) == expected["distances"]["same_hall_8m"]
    assert str(cross) == expected["distances"]["cross_hall_230m"]
    # 展馆锚点相距 80 米，加上 150 米通道折算，正好 230 米
    assert cross == Decimal("80") + config.CROSS_HALL_PENALTY_M
    assert cross - same == Decimal("222.0")


def test_distance_quantized_to_one_decimal():
    step = geometry.distance("A1", geometry.position("A1", "01", "01"),
                             "D2", geometry.position("D2", "07", "09"))
    assert isinstance(step, Decimal)
    assert step.as_tuple().exponent == -1
    acc = distance.route_total([
        {"ref": "a", "hall": "A1", "pos": geometry.position("A1", "01", "01")},
        {"ref": "b", "hall": "D2", "pos": geometry.position("D2", "07", "09")},
    ])
    assert acc.as_tuple().exponent == -1


def test_walk_total_equals_segment_sum(assembled):
    """不变量：总步行 = 每日分段之和（Decimal 1 位，不容忍尾差）。"""
    plan = assembled["plan"]
    placed = [row for row in plan["stops"]
              if row["status"] in ("SCHEDULED", "RESCHEDULED")]
    placed.sort(key=lambda row: (row["plan_day"], row["seq"]))
    acc = Decimal("0")
    for row in placed:
        acc += row["walk_m"]
    assert geometry._q(acc) == Decimal(plan["totals"]["walk_total_m"])
    day_sum = sum(Decimal(str(day["walk_m"])) for day in plan["days"])
    assert day_sum == Decimal(plan["totals"]["walk_total_m"])


def test_day_split_follows_hall_parity(assembled):
    plan = assembled["plan"]
    for row in plan["stops"]:
        if row["kind"] != "展商" or row["status"] not in ("SCHEDULED", "RESCHEDULED"):
            continue
        expected_day = 1 if row["hall"].endswith("1") else 2
        assert row["plan_day"] == expected_day


def test_no_position_candidate_skipped(assembled):
    area = [row for row in assembled["plan"]["stops"]
            if row["code"] == "Q_BOOTH_AREA_ONLY"]
    assert len(area) == 1
    assert area[0]["status"] == "SKIPPED"
    assert area[0]["walk_m"] == Decimal("0.0")


def test_fixed_session_conflict_classified(assembled):
    kinds = {(row["status"], row["code"]) for row in assembled["plan"]["stops"]
             if row["kind"] == "论坛"}
    assert ("CONFLICT", "P_OVERLAP_FIXED") in kinds
    assert ("CONFLICT", "P_OUTSIDE_HOURS") in kinds


def test_classify_priority_independent_of_order():
    # TRAP-19：多规则同时命中，判定顺序固定，与输入顺序无关
    assert score.pick(["P_OVERLAP_FIXED", "P_NO_POSITION"]) == "P_NO_POSITION"
    assert score.pick(["P_NO_POSITION", "P_OVERLAP_FIXED"]) == "P_NO_POSITION"
    assert score.pick(["P_OUTSIDE_HOURS", "P_OVERLAP_FIXED"]) == "P_OUTSIDE_HOURS"
    verdict = score.early_verdict({"pos": None, "outside_hours": True,
                                   "overlaps_fixed": True, "code": None})
    assert verdict == ("SKIPPED", "P_NO_POSITION")


def test_preferred_slot_and_judge():
    assert score.preferred_slot(1) == config.PREFERRED_WINDOW[1]
    assert score.preferred_slot(2) == (13 * 60, 15 * 60)
    assert score.preferred_slot(3) is None
    assert score.judge(630, 1) == ("SCHEDULED", None)
    assert score.judge(900, 1) == ("RESCHEDULED", "P_SHIFTED")
    assert score.judge(900, 5) == ("SCHEDULED", None)


def test_greedy_order_is_deterministic(assembled):
    """同一输入算两次，路线与总步行逐字相同。"""
    again = service.run(assembled["exhibitors"], assembled["sessions"])
    first = [(row["ref"], row["plan_day"], row["status"], str(row["walk_m"]))
             for row in assembled["plan"]["stops"]]
    second = [(row["ref"], row["plan_day"], row["status"], str(row["walk_m"]))
              for row in again["stops"]]
    assert first == second


def test_preferred_slot_end_is_exclusive():
    """首选时段右端开区间：719 分仍是排入，720 分改期。"""
    assert score.judge(600, 1) == ("SCHEDULED", None)
    assert score.judge(719, 1) == ("SCHEDULED", None)
    assert score.judge(720, 1) == ("RESCHEDULED", "P_SHIFTED")
    assert score.judge(899, 2) == ("SCHEDULED", None)
    assert score.judge(900, 2) == ("RESCHEDULED", "P_SHIFTED")
