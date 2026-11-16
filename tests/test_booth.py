"""M2 展位解析：展位号语法、全角与千分位、前导零、同名冲突、列数隔离。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m2_booth import contract, geometry, normalize, parse


def by_name(records, name):
    return [record for record in records if record["name"] == name]


def codes_of(issues, code):
    return [item for item in issues if item["code"] == code]


def test_booth_grammar_parses_all_forms(expected):
    for raw, want in expected["booth_codes"].items():
        parsed = parse.parse_booth(raw)
        assert parsed["hall"] == want["hall"], raw
        assert parsed["row"] == want["row"], raw
        assert parsed["col"] == want["col"], raw


def test_leading_zero_kept_not_int(assembled):
    # TRAP-08：A-01 的位号是字符串 "01"；naive int() 会变成 1 并对不上展位
    record = by_name(assembled["exhibitors"], "明纬电源")[0]
    assert record["col_no"] == "01"
    assert record["booth_code"] == "A1-01-01"
    assert int(record["col_no"]) != record["col_no"]


def test_fullwidth_booth_normalized(assembled):
    # TRAP-06：全角 Ａ１-2-01 归一后落到 A1 馆 2 排 1 位
    record = by_name(assembled["exhibitors"], "高创传动")[0]
    assert record["booth_code"] == "A1-02-01"
    assert record["x_m"] == Decimal("30")
    assert record["y_m"] == Decimal("46")


def test_thousands_separator_area(assembled):
    # TRAP-06：面积写 1,024 也要归一成 1024
    record = by_name(assembled["exhibitors"], "费斯托")[0]
    assert record["area_sqm"] == Decimal("1024")
    assert normalize.parse_decimal("1,200.5") == Decimal("1200.5")


def test_sentinel_area_is_none_not_zero(assembled):
    # TRAP-07：面积写 N/A 是哨兵，不能当 0 参与合计
    record = by_name(assembled["exhibitors"], "长飞光纤")[0]
    assert record["area_sqm"] is None
    assert record["status"] == "ok"


def test_area_only_booth_unplaced(assembled):
    record = by_name(assembled["exhibitors"], "精工传动")[0]
    assert record["status"] == "unplaced"
    assert record["code"] == "Q_BOOTH_AREA_ONLY"
    assert record["booth_code"] == ""


def test_hall_mismatch_isolated(assembled):
    # 展位号写 A1，馆列写 D2：整行隔离，不猜哪边对
    items = codes_of(assembled["booth_issues"], "Q_HALL_MISMATCH")
    assert len(items) == 1
    assert "三垦电气" in items[0]["subject"]
    assert by_name(assembled["exhibitors"], "三垦电气") == []


def test_duplicate_exhibitor_keeps_both_evidence(assembled, expected):
    # TRAP-14：同名不同馆，两条都留证据，首次出现为主
    items = codes_of(assembled["booth_issues"], "Q_DUP_EXHIBITOR")
    assert len(items) == expected["exhibitor_dup"]
    dups = [record for record in assembled["exhibitors"] if record["status"] == "dup"]
    assert len(dups) == 2
    assert all(record["code"] == "Q_DUP_EXHIBITOR" for record in dups)
    names = {record["name"] for record in dups}
    assert names == {"欧姆龙自动化", "汇川技术"}


def test_column_count_mismatch_isolated(assembled):
    # TRAP-05：列数与表头不一致的行进隔离区，带原值
    items = codes_of(assembled["booth_issues"], "Q_COLUMN_COUNT")
    assert len(items) == 2
    assert all("列" in item["detail"] for item in items)


def test_blank_name_isolated(assembled):
    items = codes_of(assembled["booth_issues"], "Q_MISSING_NAME")
    assert len(items) == 1


def test_empty_booth_isolated(assembled):
    items = codes_of(assembled["booth_issues"], "Q_BOOTH_EMPTY")
    assert len(items) == 1
    assert items[0]["raw_value"] == "-"


def test_unparsable_booth_reported(assembled):
    items = codes_of(assembled["booth_issues"], "Q_BOOTH_UNPARSABLE")
    assert len(items) == 1
    assert items[0]["raw_value"] == "E9-1-1"


def test_bad_area_reported(assembled):
    items = codes_of(assembled["booth_issues"], "Q_BAD_AREA")
    assert len(items) == 1
    assert "约" in items[0]["raw_value"]


def test_coordinate_from_booth(expected):
    want = expected["booth_codes"]["A1-3-05"]
    position = geometry.position("A1", "03", "05")
    assert position == (Decimal(want["x"]), Decimal(want["y"]))
    assert geometry.position("Z9", "01", "01") is None


def test_summary_and_counts(assembled, expected):
    from app.modules.m2_booth import service

    stats = service.summary(assembled["exhibitors"], assembled["booth_issues"])
    assert stats["records"] == expected["exhibitors"]
    assert stats["ok"] == expected["exhibitor_ok"]
    assert stats["unplaced"] == expected["exhibitor_unplaced"]
    assert stats["dup"] == expected["exhibitor_dup"]
    assert stats["halls"] == 8


def test_contract_alias_and_order_independence():
    """列序换了、列名改了，仍然按语义对上（契约不认列位置）。"""
    mapping = contract.map_header(["企业名称", "展位编号", "馆号", "备注"])
    assert mapping["fields"]["exhibitor_name"] == 0
    assert mapping["fields"]["booth_raw"] == 1
    assert mapping["fields"]["hall_raw"] == 2
    assert mapping["missing"] == []
    reordered = contract.map_header(["备注", "展位号", "展商名称"])
    assert reordered["fields"]["exhibitor_name"] == 2
    assert contract.map_header(["随便", "别的"])["missing"] == ["exhibitor_name", "booth_raw"]


def test_duplicate_booth_warned():
    """同一展位号被两家占用：两条都保留，只提示（TRAP-14 同族）。"""
    from app.modules.m1_catalog import reader
    from app.modules.m2_booth import service as booth_service

    page = reader.Fetched(url="http://sample-site/x", status="ok", http_status=200,
                          raw=b"", sha256="0" * 64, source="dir", kind="list",
                          page_no=1)
    page.header = ["展商名称", "展位号", "展馆"]
    page.rows = [["甲电子", "A1-2-02", "A1"], ["乙电子", "A1-2-02", "A1"]]
    records, issues = booth_service.build([page])
    assert len(records) == 2
    assert all(record["status"] == "ok" for record in records)
    items = codes_of(issues, "Q_DUP_BOOTH")
    assert len(items) == 1
    assert items[0]["subject"] == "A1-02-02"
    assert "甲电子" in items[0]["raw_value"] and "乙电子" in items[0]["raw_value"]


def test_hall_column_accepts_numbered_form():
    """展馆列写「1 号馆」也要认；只按字母匹配会把这类行判成未知馆。"""
    assert parse.hall_of_raw("1 号馆") == "A1"
    assert parse.hall_of_raw("2 号馆") == "A2"
    assert parse.hall_of_raw("8 号馆") == "D2"
    assert parse.hall_of_raw("9 号馆") is None
    assert parse.hall_of_raw("A 馆") == "A1"
    assert parse.hall_of_raw("") is None
