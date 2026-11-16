"""M6 导出：行程单、证据、分段、清单四种产物的一致性。"""
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

import pytest

from app.modules.m6_export import csv_out, itinerary, service
from app.modules.m4_route import distance


def read_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))


def test_itinerary_csv_starts_with_bom(finished):
    payload = service.build(finished["store"], finished["batch"], "itinerary")
    name, text = service.as_csv("itinerary", payload)
    assert name.endswith("-itinerary.csv")
    assert text.startswith("\ufeff")
    rows = read_csv(text)
    assert rows[0][0] == "日期"
    assert rows[0][8] == "步行(米)"


def test_itinerary_rows_match_placed_stops(finished, expected):
    payload = service.build(finished["store"], finished["batch"], "itinerary")
    rows = read_csv(service.as_csv("itinerary", payload)[1])
    assert len(rows) - 1 == expected["placed_stops"]
    assert payload["trip"]["placed"] == expected["placed_stops"]
    assert str(payload["trip"]["walk_total_m"]) == expected["walk_total_m"]


def test_itinerary_slots_sorted_by_day(finished):
    payload = service.build(finished["store"], finished["batch"], "itinerary")
    rows = payload["rows"]
    assert rows == sorted(rows, key=lambda row: (row["day"], row["seq"]))
    for row in rows:
        assert row["slot"].count("–") == 1
        assert row["start_min"] < row["end_min"]


def test_evidence_csv_carries_reason_and_row_ref(finished):
    payload = service.build(finished["store"], finished["batch"], "evidence")
    rows = read_csv(service.as_csv("evidence", payload)[1])
    header = rows[0]
    assert header[0] == "原因码"
    assert "行号" in header
    codes = {row[0] for row in rows[1:]}
    assert "Q_DUP_EXHIBITOR" in codes
    assert "T_OVERLAP" in codes
    refs = [row[7] for row in rows[1:] if row[7]]
    assert any("#" in ref for ref in refs)


def test_segment_csv_sum_equals_total(finished):
    payload = service.build(finished["store"], finished["batch"], "segment")
    rows = read_csv(service.as_csv("segment", payload)[1])
    acc = Decimal("0")
    for row in rows[1:]:
        acc += Decimal(row[5])
    assert acc == Decimal(payload["trip"]["walk_total_m"])


def test_unplaced_csv_lists_every_skipped(finished):
    payload = service.build(finished["store"], finished["batch"], "unplaced")
    rows = read_csv(service.as_csv("unplaced", payload)[1])
    statuses = {row[5] for row in rows[1:]}
    assert "跳过" in statuses
    assert "冲突" in statuses
    assert len(rows) - 1 == len(payload["unplaced"])


def test_manifest_export_fields(finished):
    payload = service.build(finished["store"], finished["batch"], "manifest")
    name, text = service.as_csv("manifest", payload)
    assert name.endswith("-manifest.json")
    doc = json.loads(text)
    assert doc["run_id"] == finished["batch"]["run_id"]
    assert len(doc["input_digest"]) == 64
    assert "counters" in doc and "stages" in doc
    assert doc["rule_version"]


def test_unknown_export_kind_raises(finished):
    payload = service.build(finished["store"], finished["batch"], "itinerary")
    with pytest.raises(ValueError):
        service.as_csv("parquet", payload)


def test_decimal_serialized_as_string(finished):
    """大步长与合计都不走 float，导出的字符串保留一位小数。"""
    payload = service.build(finished["store"], finished["batch"], "segment")
    rows = read_csv(service.as_csv("segment", payload)[1])
    for row in rows[1:]:
        assert len(row[5].split(".")[-1]) == 1
    assert isinstance(payload["trip"]["walk_total_m"], Decimal)


def test_day_groups_cover_all_rows(finished):
    payload = service.build(finished["store"], finished["batch"], "itinerary")
    groups = itinerary.day_groups(payload["rows"])
    assert [group["day"] for group in groups] == [1, 2]
    total = sum(len(group["rows"]) for group in groups)
    assert total == len(payload["rows"])
    for group in groups:
        assert group["first"] != "—" and group["last"] != "—"


def test_summary_and_distribution(finished):
    payload = service.build(finished["store"], finished["batch"], "itinerary")
    summary = service.summary(payload)
    assert summary["placed"] == payload["trip"]["placed"]
    assert summary["unplaced"] == len(payload["unplaced"])
    codes = {row["code"] for row in summary["distribution"]}
    assert "Q_COLUMN_COUNT" in codes


def test_segment_positions_resolved(finished):
    """分段明细要给每段标出起终点，不能全是空行。"""
    store = finished["store"]
    payload = service.build(store, finished["batch"], "segment")
    segments = payload["segments"]
    assert segments
    assert segments[0]["from"] == "入口"
    assert all(segment["hall"] for segment in segments)
    again = distance.segment_rows([])
    assert again == []
