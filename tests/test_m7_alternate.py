"""M7 备选方案：三套取舍、跨馆上限、换乘、方向化距离与带方向的 2-opt。"""
from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app import config, reasons
from app.modules.m2_booth import geometry
from app.modules.m7_alternate import directed, limits, service


def exhibitor(ref, hall, row="01", col="01", *, priority=1, name=None):
    return {"ex_id": ref, "name": name or ref, "hall": hall, "row_no": row,
            "col_no": col, "priority": priority,
            "booth_code": f"{hall}-{row}-{col}", "code": None,
            "row_ref": ref, "status": "ok"}


def stop(ref, hall, row="01", col="01", *, priority=1, dwell=20, day=1,
         window_start=None, window_end=None, hard=False):
    open_min, close_min = config.SHOW_HOURS[day]
    return {"kind": "论坛" if hard else "展商", "ref": ref, "name": ref,
            "hall": hall, "pos": geometry.position(hall, row, col),
            "priority": priority, "dwell": dwell, "day": day, "hard": hard,
            "window_start": open_min if window_start is None else window_start,
            "window_end": close_min if window_end is None else window_end,
            "booth_code": "", "code": None, "row_ref": ref,
            "record_status": "ok"}


def codes_of(plan: dict) -> list[str]:
    return [row["code"] for row in plan["unplaced"]]


def test_weight_conserved_in_every_plan(assembled):
    """不变量：排入权重 + 未排入权重 = 候选权重合计（三套各自成立）。"""
    result = service.build(assembled["exhibitors"], assembled["sessions"])
    assert result["invariant"]["balanced"] is True
    assert result["invariant"]["total_weight"] > 0
    for plan in result["plans"]:
        totals = plan["totals"]
        assert totals["weight"] + totals["weight_rest"] \
            == result["invariant"]["total_weight"]
    # 三套方案的候选集合相同，权重合计自然只有一个
    assert len({plan["totals"]["weight"] + plan["totals"]["weight_rest"]
                for plan in result["plans"]}) == 1


def test_weight_rule_priority_and_fixed_window():
    assert limits.weight({"priority": 1}) == 5
    assert limits.weight({"priority": 5}) == 1
    assert limits.weight({"priority": None}) == config.DEFAULT_WEIGHT
    # 固定论坛窗不可放弃，权重记 0
    assert limits.weight({"priority": 1, "hard": True}) == config.FIXED_WEIGHT
    assert limits.total_weight([{"priority": 1}, {"priority": 2}]) == 9


def test_hall_cap_reported_not_no_slot():
    """TRAP-27：时间放得下但跨馆到上限时记 M7_HALL_CAP，不混成 P_NO_SLOT。"""
    rows = [exhibitor("e1", "A1", "01", "01"), exhibitor("e2", "A1", "01", "02"),
            exhibitor("e3", "B1", "01", "01"), exhibitor("e4", "C1", "01", "01")]
    tight = service.build(rows, [], cap=1)
    base = service.build(rows, [])
    plan_c = service.plan_by_id(tight, "C")
    plan_a = service.plan_by_id(tight, "A")
    assert codes_of(plan_a) == []
    assert codes_of(plan_c) == ["M7_HALL_CAP"]
    assert "P_NO_SLOT" not in codes_of(plan_c)
    assert plan_c["totals"]["stops"] == 3
    # 放开上限后同一批候选都能排进去，说明是上限挡住的
    assert base["plans"][0]["totals"]["stops"] >= plan_c["totals"]["stops"]
    assert all(day["switches"] <= 1 for day in plan_c["days"])


def test_transfer_minutes_fold_into_arrival():
    """加深点：换乘分钟并入到站时刻，只在跨馆时计。"""
    a1 = geometry.position("A1", "01", "01")
    a2 = geometry.position("A1", "01", "02")
    b1 = geometry.position("B1", "01", "01")
    same = directed.leg("A1", a1, "A1", a2)
    cross = directed.leg("A1", a1, "B1", b1)
    entry = directed.leg("", None, "A1", a1)
    assert same["transfer_min"] == 0
    assert entry["transfer_min"] == 0
    assert cross["transfer_min"] == config.TRANSFER_MIN
    assert cross["minutes"] == cross["walk_min"] + config.TRANSFER_MIN
    ok, rows = directed.simulate(1, [stop("s1", "A1"), stop("s2", "B1")])
    assert ok is True
    assert rows[0]["transfer_min"] == 0
    assert rows[1]["arrival"] == (rows[0]["depart"] + rows[1]["walk_min"]
                                 + config.TRANSFER_MIN)


def test_one_way_bridge_detected_as_asymmetric():
    """TRAP-28：单向扶梯让 A2→B2 与 B2→A2 相差 60 米，必须检出。"""
    a2 = geometry.position("A2", "01", "01")
    b2 = geometry.position("B2", "01", "01")
    forward = directed.walk_m("A2", a2, "B2", b2)
    back = directed.walk_m("B2", b2, "A2", a2)
    assert back - forward == config.ONE_WAY_EXTRA_M[("B2", "A2")]
    pair = directed.pair("A2", "B2")
    assert pair["ok"] is False
    assert pair["delta_m"] == Decimal("-60.0")
    audit = directed.symmetry_audit()
    assert audit["checked"] == 28
    assert audit["asymmetric"] == 1
    assert audit["ok"] is False
    row = audit["rows"][0]
    assert (row["from"], row["to"]) == ("A2", "B2")
    assert row["delta_m"] == "-60.0"


def test_directional_total_is_not_symmetric():
    """方向化合计：换顺序后只有那一段变，不能拿对称表反推。"""
    a2 = stop("a2", "A2")
    b2 = stop("b2", "B2")
    forward = directed.walk_m("A2", a2["pos"], "B2", b2["pos"])
    back = directed.walk_m("B2", b2["pos"], "A2", a2["pos"])
    assert back == forward + Decimal("60.0")
    assert directed.total([a2, b2]) != directed.total([b2, a2])


def test_two_opt_shortens_and_keeps_windows():
    route = [stop("a", "A1", "01", "01"), stop("d", "D1", "09", "12"),
             stop("c", "A1", "01", "02")]
    better, moves = directed.two_opt(1, route)
    assert moves >= 1
    assert directed.total(better) < directed.total(route)
    ok, rows = directed.simulate(1, better)
    assert ok is True
    for row in rows:
        assert row["window_start"] <= row["arrival"]
        assert row["arrival"] + row["dwell"] <= row["window_end"]


def test_two_opt_respects_hall_cap():
    """反转区间只省米、却让跨馆次数超限的位置不能收。"""
    route = [stop("a", "A1"), stop("b", "B1"), stop("c", "A1")]
    capped, _moves = directed.two_opt(1, route, cap=1)
    assert directed.switches(capped) <= 1
    loose, _moves = directed.two_opt(1, route, cap=None)
    assert directed.total(loose) <= directed.total(route)


def test_plan_c_respects_daily_hall_cap(assembled):
    result = service.build(assembled["exhibitors"], assembled["sessions"], cap=2)
    plan_c = service.plan_by_id(result, "C")
    assert plan_c["cap"] == 2
    for day in plan_c["days"]:
        assert day["switches"] <= 2
    assert any(code == "M7_HALL_CAP" for code in codes_of(plan_c))


def test_plan_b_weight_not_lower_than_a(assembled):
    result = service.build(assembled["exhibitors"], assembled["sessions"])
    plan_a, plan_b = service.plan_by_id(result, "A"), service.plan_by_id(result, "B")
    assert plan_b["totals"]["weight"] >= plan_a["totals"]["weight"]
    assert plan_b["totals"]["stops"] >= 0
    assert "M7_ALT_LOSS" in plan_b["codes"]
    assert "M7_ALT_LOSS" not in plan_a["codes"]


def test_drop_order_is_deterministic():
    stops = [stop("z", "A1", priority=5), stop("y", "A1", priority=1),
             stop("x", "A1", priority=3)]
    first = [row["ref"] for row in limits.drop_order(stops)]
    second = [row["ref"] for row in limits.drop_order(list(reversed(stops)))]
    assert first == second == ["z", "x", "y"]
    # 换馆次数超上限时才裁：z(A1) → y(B1) → x(C1) 两次跨馆，上限 1 只放弃最轻的 z
    mixed = [stop("z", "A1", priority=5), stop("y", "B1", priority=1),
             stop("x", "C1", priority=3)]
    kept, dropped = limits.enforce(mixed, 1)
    assert [row["ref"] for row in kept] == ["y", "x"]
    assert [row["ref"] for row in dropped] == ["z"]


def test_compare_deltas_use_totals(assembled):
    result = service.build(assembled["exhibitors"], assembled["sessions"])
    base, other = result["plans"][0], result["plans"][2]
    row = limits.compare(base, other)
    assert row["plan"] == other["id"]
    assert row["delta_stops"] == other["totals"]["stops"] - base["totals"]["stops"]
    assert row["delta_weight"] == other["totals"]["weight"] - base["totals"]["weight"]
    assert row["delta_switches"] == other["totals"]["switches"] - base["totals"]["switches"]
    assert Decimal(row["delta_walk_m"]) == (Decimal(other["totals"]["walk_m"])
                                            - Decimal(base["totals"]["walk_m"]))


def test_plan_rows_have_slot_and_label(assembled):
    result = service.build(assembled["exhibitors"], assembled["sessions"])
    rows = service.plan_rows(result["plans"][0])
    assert rows
    for row in rows:
        assert len(row["slot"]) == 11 and "–" in row["slot"]
        assert row["status_label"] in config.CLASSIFY_LABELS.values()
    day1 = service.plan_rows(result["plans"][0], 1)
    assert all(row["day"] == 1 for row in day1)


def test_alternate_api_returns_three_plans(client: TestClient):
    body = client.get("/api/alternate").json()
    assert [plan["id"] for plan in body["plans"]] == ["A", "B", "C"]
    assert body["plan"] == "A"
    assert body["invariant"]["balanced"] is True
    assert body["symmetry"]["checked"] == 28
    assert body["stops"] and body["comparison"]
    chosen = client.get("/api/alternate?plan=C&cap=3").json()
    assert chosen["plan"] == "C"
    assert chosen["cap"] == 3
    for day in chosen["plans"][2]["days"]:
        assert day["switches"] <= 3


def test_alternate_page_renders_controls(client: TestClient):
    text = client.get("/alternate").text
    assert 'id="grid-alternate"' in text
    assert 'id="grid-compare"' in text
    assert 'id="grid-symmetry"' in text
    assert 'action="/alternate"' in text
    assert 'class="tab is-on" href="/alternate"' in text
    one_day = client.get("/alternate?day=1&plan=B&cap=2")
    assert one_day.status_code == 200
    assert "B 减站换权重" in one_day.text


def test_bad_alternate_inputs_return_4xx(client: TestClient):
    for query, code in (("cap=99", "E_BAD_CAP"), ("cap=x", "E_BAD_CAP"),
                        ("day=3", "E_BAD_DAY"), ("plan=Q", "E_BAD_PLAN")):
        response = client.get(f"/api/alternate?{query}")
        assert response.status_code == 400, query
        assert response.json()["error"] == code, query


def test_alternate_without_batch_returns_404(empty_client: TestClient):
    response = empty_client.get("/api/alternate")
    assert response.status_code == 404
    assert response.json()["error"] == "E_NO_BATCH"
    page = empty_client.get("/alternate")
    assert page.status_code == 200
    assert "还没有可对比的批次" in page.text


def test_m7_codes_are_registered(assembled):
    result = service.build(assembled["exhibitors"], assembled["sessions"])
    used = {row["code"] for row in result["codes"]}
    assert {"M7_ALT_LOSS", "M7_TRANSFER", "M7_ASYMMETRY"} <= used
    for code in used:
        assert code in reasons.REASONS, code
        assert reasons.stage_of(code) in ("alternate", "route", "booth", "windows")
    assert reasons.level_of("M7_HALL_CAP") == reasons.WARN
