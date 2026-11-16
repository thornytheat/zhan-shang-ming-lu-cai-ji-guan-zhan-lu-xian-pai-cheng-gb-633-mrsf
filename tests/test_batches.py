"""M5 批次：阶段记录、幂等重跑、运行清单、版本与陈旧提交拒绝。"""
from __future__ import annotations

import json

import pytest

from app import config, errors
from app.modules.m5_batches import history, service
from app.store import Store


def test_run_reaches_done_with_five_stages(finished):
    result = finished["result"]
    assert result["status"] == "done"
    stages = [row["stage"] for row in result["stages"]]
    assert stages == list(config.STAGES)
    assert all(row["status"] == "ok" for row in result["stages"])


def test_stage_counts_add_up(finished):
    result = finished["result"]
    by_stage = {row["stage"]: row for row in result["stages"]}
    assert by_stage["catalog"]["out_count"] > 0
    assert by_stage["booth"]["in_count"] >= by_stage["booth"]["out_count"]
    assert by_stage["route"]["quarantine_count"] == (
        result["totals"]["conflict"] + result["totals"]["skipped"])


def test_manifest_has_run_id_and_digest(finished):
    store = finished["store"]
    doc = store.load_manifest(finished["batch"]["batch_id"])
    assert doc["run_id"] == finished["batch"]["run_id"]
    assert len(doc["input_digest"]) == 64
    assert doc["rule_version"] == config.RULE_VERSION
    assert doc["window_rule"].startswith("论坛时间窗左闭右开")
    assert len(doc["inputs"]) == len(store.load_pages(finished["batch"]["batch_id"]))


def test_idempotent_rerun_adds_no_rows(settings, store, fetcher):
    """不变量：同一份输入重跑命中批次键，不新增批次也不新增行程点。"""
    first = service.run(settings=settings, store=store, source="all", fetcher=fetcher)
    assert first["status"] == "done"
    before = store.count_rows(first["batch"]["batch_id"])
    second = service.run(settings=settings, store=store, source="all", fetcher=fetcher)
    assert second["idempotent_replay"] is True
    assert second["batch"]["batch_id"] == first["batch"]["batch_id"]
    assert len(store.list_batches()) == 1
    after = store.count_rows(first["batch"]["batch_id"])
    assert before == after


def test_different_source_gets_different_key(settings, store, fetcher):
    full = service.run(settings=settings, store=store, source="all", fetcher=fetcher)
    part = service.run(settings=settings, store=store, source="dir", fetcher=fetcher)
    assert full["batch"]["batch_key"] != part["batch"]["batch_key"]
    assert len(store.list_batches()) == 2


def test_stale_version_rejected(finished):
    # 旧版本提交不能覆盖新结果
    store = finished["store"]
    batch_id = finished["batch"]["batch_id"]
    with pytest.raises(errors.Conflict) as exc:
        service.guard(store, batch_id, 0)
    assert exc.value.code == "B_STALE_REJECTED"
    assert exc.value.status_code == 409


def test_resume_plan_after_failure(settings, store):
    batch = {"batch_id": history.new_batch_id(), "batch_key": "dir:abc",
             "run_id": "BP-test", "source": "dir", "status": "failed",
             "created_at": history.now_iso(), "input_digest": "a" * 64,
             "version": 1, "params": {"source": "dir"}}
    stages = [
        history.stage("catalog", in_count=7, out_count=74, quarantine_count=2),
        history.stage("booth", in_count=0, out_count=0, quarantine_count=0,
                    status="failed", note="解析中断"),
    ]
    store.save_attempt(batch=batch, pages=[], stages=stages, note="解析中断")
    plan = service.plan(store, batch["batch_id"])
    assert plan["can_resume"] is True
    assert plan["completed"] == ["catalog"]
    assert plan["watermark"] == "booth"


def test_watermark_is_monotonic():
    assert history.monotonic(None, "booth") is True
    assert history.monotonic("booth", "route") is True
    assert history.monotonic("route", "booth") is False
    stages = [history.stage("catalog", in_count=1, out_count=1, quarantine_count=0),
              history.stage("booth", in_count=1, out_count=1, quarantine_count=0)]
    assert history.watermark(stages) == "windows"


def test_history_rows_and_versions(finished):
    store = finished["store"]
    rows = history.rows(store)
    assert len(rows) == 1
    assert rows[0]["status_label"] == "完成"
    chain = history.versions(store, rows[0]["batch_key"])
    assert [item["version"] for item in chain] == [1]
    detail = history.detail(store, rows[0]["batch_id"])
    assert detail["counts"]["exhibitors"] == 44
    # 五个阶段都 ok 之后没有待跑阶段，水位为空
    assert detail["watermark"] is None


def test_stage_matrix_pads_missing_stages(settings):
    store = Store(settings.db_path)
    matrix = history.stage_matrix(
        [history.stage("catalog", in_count=1, out_count=1, quarantine_count=0)])
    assert len(matrix) == len(config.STAGES)
    assert [row["status"] for row in matrix][1:] == ["skipped"] * 4


def test_totals_rows_are_labelled(finished):
    totals = finished["store"].load_totals(finished["batch"]["batch_id"])
    rows = history.totals_rows(totals)
    labels = [row["label"] for row in rows]
    assert "候选站点" in labels
    assert "总步行(米)" in labels
    assert len(rows) == len([key for key in totals if not key.endswith("_json")])


def test_reconcile_json_is_stored(finished):
    totals = finished["store"].load_totals(finished["batch"]["batch_id"])
    reconcile = json.loads(totals["reconcile_json"])
    assert reconcile["all_balanced"] is True
    days = json.loads(totals["days_json"])
    assert [day["day"] for day in days] == [1, 2]


def test_plan_round_trip_equals_stored(finished):
    """不变量：把落库的展商与场次读回来重算，必须与批次里的行程逐字一致。"""
    from app.modules.m4_route import service as route_service

    store = finished["store"]
    bid = finished["batch"]["batch_id"]
    again = route_service.run(store.load_exhibitors(bid), store.load_forums(bid))
    stored = sorted(((row["plan_day"], row["seq"], row["ref"], row["status"],
                      str(row["walk_m"])) for row in store.load_stops(bid)),
                    key=lambda item: (item[0], item[1], item[2]))
    fresh = sorted(((row["plan_day"], row["seq"], row["ref"], row["status"],
                     str(row["walk_m"])) for row in again["stops"]),
                   key=lambda item: (item[0], item[1], item[2]))
    assert len(stored) == len(fresh)
    assert stored == fresh
    assert again["totals"]["walk_total_m"] == finished["result"]["totals"]["walk_total_m"]
