"""端到端：样例站 → 采信 → 展位 → 时间窗 → 排程 → 导出，全走 HTTP。"""
from __future__ import annotations

import json
import pathlib
import re

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_full_flow_over_http(empty_client: TestClient):
    """一条完整链路：POST /api/crawl → POST /api/plans → CSV 导出。"""
    crawl = empty_client.post("/api/crawl", json={"source": "all"})
    assert crawl.status_code == 200
    payload = crawl.json()
    assert payload["status"] == "done"
    assert [stage["stage"] for stage in payload["stages"]] == [
        "catalog", "booth", "windows", "route", "export"]
    assert payload["all_balanced"] is True

    plans = empty_client.post("/api/plans", json={"batch_id": payload["batch_id"]})
    assert plans.status_code == 200
    body = plans.json()
    assert body["totals"]["candidates"] == body["totals"]["scheduled"] \
        + body["totals"]["rescheduled"] + body["totals"]["conflict"] \
        + body["totals"]["skipped"]

    csv_response = empty_client.get(
        f"/api/export/itinerary.csv?batch_id={payload['batch_id']}")
    assert csv_response.status_code == 200
    assert csv_response.text.startswith("\ufeff")
    assert "步行(米)" in csv_response.text


def test_seven_pages_render(client: TestClient):
    for path in ("/", "/exhibitors", "/conflicts", "/plan", "/batches", "/export",
                 "/alternate"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "boothplan" in response.text
        assert 'class="grid"' in response.text
        assert "/static/theme.css" in response.text


def test_page_has_column_letters_and_row_numbers(client: TestClient):
    """L5 表格网格：列字母 + 行号 + 可聚焦单元格。"""
    text = client.get("/exhibitors").text
    assert '<span class="cl">A</span>' in text
    assert '<span class="cl">B</span>' in text
    assert '<td class="rn">' not in text  # 表头用 th.rn
    assert '<th class="rn">#' in text
    assert 'tabindex="0"' in text


def test_failure_inputs_return_4xx(empty_client: TestClient, client: TestClient):
    bad_mode = empty_client.post("/api/crawl", json={"mode": "delta"})
    assert bad_mode.status_code == 400
    assert bad_mode.json()["error"] == "E_BAD_MODE"

    bad_source = empty_client.post("/api/crawl", json={"source": "portal_z"})
    assert bad_source.status_code == 400
    assert bad_source.json()["error"] == "E_BAD_SOURCE"

    bad_kind = client.get("/api/export/parquet.csv")
    assert bad_kind.status_code == 400
    assert bad_kind.json()["error"] == "E_BAD_EXPORT_KIND"

    bad_day = client.post("/api/plans", json={"day": 3})
    assert bad_day.status_code == 400

    missing = empty_client.get("/api/batches/nope")
    assert missing.status_code == 404
    no_batch = empty_client.get("/api/export")
    assert no_batch.status_code == 404


def test_reconcile_four_accounts(client: TestClient):
    body = client.get("/api/conflicts").json()
    assert body["summary"]["sessions"] == 24
    plans = client.post("/api/plans", json={}).json()
    accounts = {row["account"]: row for row in plans["reconciliation"]["accounts"]}
    assert set(accounts) == {"名录行账", "候选账", "议程场次账", "距离账"}
    assert all(row["balanced"] for row in accounts.values())
    assert accounts["候选账"]["right_total"] == accounts["候选账"]["left"]


def test_api_contract_lists_grammar_and_reasons(client: TestClient):
    body = client.get("/api/contract").json()
    assert [row["field"] for row in body["columns"]][:2] == [
        "exhibitor_name", "booth_raw"]
    codes = {row["raw"]: row["code"] for row in body["grammar"]}
    assert codes["A1-3-05"] == "OK"
    assert codes["3x3"] == "Q_BOOTH_AREA_ONLY"
    assert body["geometry"]["cross_hall_penalty_m"] == "150"
    assert any(item["code"] == "P_NO_SLOT" for item in body["reasons"])


def test_depth_doc_registers_traps():
    """DEPTH 登记的每条 TRAP 都要在测试里出现过，这里做一次交叉核对。"""
    text = (ROOT / "docs" / "DEPTH.md").read_text(encoding="utf-8")
    traps = sorted(set(re.findall(r"TRAP-\d{2,3}", text)))
    assert len(traps) >= 8
    test_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "tests").glob("test_*.py")))
    for trap in traps:
        assert trap in test_text, trap


def test_module_pages_link_to_batch(client: TestClient):
    batch = client.get("/api/batches").json()
    assert batch["count"] == 1
    row = batch["batches"][0]
    assert row["status"] == "done"
    detail = client.get(f"/api/batches/{row['batch_id']}").json()
    assert detail["watermark"] is None
    assert json.loads(client.get("/api/crawl/%s" % row["batch_id"]).text)[
        "reconciliation"]["all_balanced"] is True
