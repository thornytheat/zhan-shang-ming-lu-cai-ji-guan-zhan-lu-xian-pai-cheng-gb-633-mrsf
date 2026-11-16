"""撤回清除：回执、永久清除、续跑、并发采集、重放与导出。

关注四件事：
1. 姓名与备注原文在库里、页面、证据里都看不到，展商名、展位、来源、行号、
   原因码与数量仍在；
2. 同一撤回请求重发或续跑只沿用一张回执，不重复生成；
3. 中断后续跑从剩下的批次继续；回放旧名录不恢复原文；
4. 清除期间并发采集落库，也不会把原文写回去。
"""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from app import config, redaction
from app.main import create_app
from app.modules.m1_catalog import service as catalog_service
from app.modules.m2_booth import service as booth_service
from app.modules.m5_batches import service as batch_service
from app.modules.m6_export import service as export_service
from app.modules.m8_redaction import service as redact_service
from app.modules.m4_route import service as route_service
from app.store import Store
from fastapi.testclient import TestClient
import datetime as dt
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = "西门子工业软件"
OWNER = "张岚"


@pytest.fixture()
def finished(tmp_path):
    """每个用例一份独立库：撤回会改写数据，不能共用 session 级夹具。"""
    settings = config.Settings(db_path=str(tmp_path / "boothplan.sqlite"),
                               eval_date=dt.date(2026, 11, 15),
                               sample_site_dir=str(ROOT / "sample_site"),
                               sample_site_base="http://sample-site")
    store = Store(settings.db_path)
    result = batch_service.run(settings=settings, store=store, source="all")
    return {"settings": settings, "store": store, "result": result,
            "batch": result["batch"]}


@pytest.fixture()
def client(finished):
    return TestClient(create_app(finished["settings"]))


def _rows(store, batch_id: str) -> list[dict]:
    return store.load_exhibitors(batch_id)


def _raw_rows(settings) -> list[tuple]:
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute(
            "SELECT name, owner, note FROM exhibitors WHERE owner != '' "
            "OR note NOT IN ('', '已清除')").fetchall()
    finally:
        conn.close()


def _raw_match(settings, needle: str) -> list[tuple]:
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute(
            "SELECT batch_id, name, owner, note FROM exhibitors "
            "WHERE owner LIKE ? OR note LIKE ?", (f"%{needle}%", f"%{needle}%")
        ).fetchall()
    finally:
        conn.close()


def _raw_owner(settings) -> list[tuple]:
    """库里该展商名下还留着原始姓名或备注的行（别的展商共用同一姓名不算）。"""
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute(
            "SELECT batch_id, name, owner, note FROM exhibitors "
            "WHERE name=? AND (owner NOT IN ('', '已清除') "
            "OR note NOT IN ('', '已清除'))", (TARGET,)).fetchall()
    finally:
        conn.close()


def _raw_target(settings, name: str) -> list[tuple]:
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute(
            "SELECT batch_id, name, owner, note FROM exhibitors WHERE name=?",
            (name,)).fetchall()
    finally:
        conn.close()


def test_withdraw_scrubs_owner_and_note_but_keeps_audit_fields(finished):
    """TRAP-31：只清原文，不整批删，也不止遮挡；来源/行号/原因码/数量要留在回执里。"""
    store, batch = finished["store"], finished["batch"]
    before = {row["ex_id"]: row for row in _rows(store, batch["batch_id"])}
    target_id = next(key for key, row in before.items() if row["name"] == TARGET)

    receipt = redact_service.create_and_scrub(store, {"name": TARGET})
    assert receipt["state"] == "done"
    target = next(t for t in receipt["targets"] if t["batch_id"] == batch["batch_id"])
    assert target["matched"] == 1
    assert target["source_url"]
    assert target["row_refs"]

    after = {row["ex_id"]: row for row in _rows(store, batch["batch_id"])}
    assert after[target_id]["owner"] == redaction.CLEARED_MARK
    assert after[target_id]["note"] == ""
    # 展商名、展位、来源、行号仍在：审计靠它们定位
    assert after[target_id]["name"] == TARGET
    assert after[target_id]["booth_code"] == before[target_id]["booth_code"]
    assert after[target_id]["source_url"] == before[target_id]["source_url"]
    assert after[target_id]["row_ref"] == before[target_id]["row_ref"]
    # 其他展商不受影响
    assert sum(1 for row in after.values() if row["owner"] == OWNER) == 3


def test_no_owner_name_anywhere_after_withdraw(finished):
    store, batch = finished["store"], finished["batch"]
    redact_service.create_and_scrub(store, {"name": TARGET})
    settings = finished["settings"]
    assert not _raw_owner(settings)
    for row in _rows(store, batch["batch_id"]):
        if row["name"] == TARGET:
            assert row["owner"] == redaction.CLEARED_MARK


def test_receipt_has_no_original_values(finished):
    store = finished["store"]
    receipt = redact_service.create_and_scrub(store, {"name": TARGET})
    blob = json.dumps(receipt, ensure_ascii=False)
    assert OWNER not in blob
    summary = receipt["summary"]
    assert summary["matched_rows"] >= 1
    assert summary["scrubbed_rows"] >= 1
    assert summary["owner_cleared"] == 1


def test_same_withdrawal_reuses_receipt_and_is_idempotent(finished):
    """TRAP-32：同一撤回请求重发只沿用一张回执，中断续跑也不新增。"""
    store = finished["store"]
    first = redact_service.create_and_scrub(store, {"name": TARGET})
    second = redact_service.create_and_scrub(store, {"name": TARGET})
    assert first["receipt_id"] == second["receipt_id"]
    assert first["withdrawal_key"] == second["withdrawal_key"]
    assert len(redact_service.receipts(store)) == 1
    events = second["events"]
    assert [event["action"] for event in events].count("created") == 1


def test_resume_after_interruption_does_not_duplicate_receipt(finished):
    """模拟起了两张命中同一批次的回执后中断：续跑沿用回执，不新建。"""
    store = finished["store"]
    receipt = redact_service.create_and_scrub(store, {"name": TARGET},
                                              max_batches=1)
    # 人为制造一个还没清的批次：给回执补一条 pending target
    batch = finished["batch"]
    with store.immediate() as conn:
        conn.execute(
            "INSERT INTO redaction_targets (receipt_id, batch_id, status, matched) "
            "VALUES (?,?,?,?) ON CONFLICT(receipt_id, batch_id) DO UPDATE SET "
            "status='pending'", (receipt["receipt_id"], batch["batch_id"], "pending", 1))
        conn.execute("UPDATE redactions SET state='partial', completed_at=NULL "
                     "WHERE receipt_id=?", (receipt["receipt_id"],))
    assert store.pending_redaction_targets(receipt["receipt_id"]) == [batch["batch_id"]]

    resumed = redact_service.resume(store, receipt["receipt_id"])
    assert resumed["receipt_id"] == receipt["receipt_id"]
    assert resumed["state"] == "done"
    assert len(redact_service.receipts(store)) == 1
    # 续跑把上次没清的批次清掉，第二次续跑什么都不做
    again = redact_service.resume(store, receipt["receipt_id"])
    assert again["state"] == "done"
    actions = [event["action"] for event in again["events"]]
    assert actions.count("created") == 1


def test_partial_withdrawal_resumes_across_batches(finished):
    """两批命中、一次只清一批：回执停在 partial，续跑清完，回执仍是同一张。"""
    settings, store = finished["settings"], finished["store"]
    # 第二个批次：只抓名录来源，批次键与 all 不同
    second = batch_service.run(settings=settings, store=store, source="dir")
    assert second["status"] == "done"
    assert second["batch"]["batch_id"] != finished["batch"]["batch_id"]

    receipt = redact_service.create_and_scrub(store, {"name": TARGET},
                                              max_batches=1)
    assert receipt["state"] == "partial"
    assert receipt["summary"]["batches"] == 2
    assert receipt["summary"]["pending_batches"] == 1

    resumed = redact_service.resume(store, receipt["receipt_id"])
    assert resumed["state"] == "done"
    assert resumed["receipt_id"] == receipt["receipt_id"]
    assert len(redact_service.receipts(store)) == 1
    for batch_id in (finished["batch"]["batch_id"], second["batch"]["batch_id"]):
        rows = [row for row in _rows(store, batch_id) if row["name"] == TARGET]
        assert rows and rows[0]["owner"] == redaction.CLEARED_MARK
    assert not _raw_owner(settings)


def test_replaying_old_batch_does_not_restore_owner(finished):
    """重放旧名录（同输入重跑）后，已撤回字段仍是清除标记。"""
    settings = finished["settings"]
    store = finished["store"]
    redact_service.create_and_scrub(store, {"name": TARGET})
    refetched = catalog_service.make_fetcher(settings)
    replay = batch_service.run(settings=settings, store=store, source="all",
                               fetcher=refetched)
    assert replay["status"] == "done"
    assert replay["idempotent_replay"] is True
    for row in _rows(store, replay["batch"]["batch_id"]):
        if row["name"] == TARGET:
            assert row["owner"] == redaction.CLEARED_MARK
    assert not _raw_owner(settings)


def test_replay_finishes_a_pending_scrub(finished):
    """回执建了但批次还没清完时直接重放：补清该批次，不新增回执也不恢复原文。"""
    store, batch = finished["store"], finished["batch"]
    subject = redaction.build_subject({"name": TARGET})
    fields = redaction.build_fields(None)
    key = redaction.withdrawal_key(subject, fields)
    receipt = store.create_redaction(
        withdrawal_key=key, receipt_id=redaction.receipt_id_for(key),
        subject=subject, fields=fields)
    assert receipt["summary"]["pending_batches"] == 1
    replay = batch_service.run(settings=finished["settings"], store=store,
                               source="all")
    assert replay["idempotent_replay"] is True
    after = store.get_redaction(receipt["receipt_id"])
    assert after["state"] == "done"
    assert after["targets"][0]["status"] == "scrubbed"
    assert not _raw_owner(finished["settings"])
    assert len(redact_service.receipts(store)) == 1


def test_crawl_during_withdrawal_writes_marker_not_text(finished):
    """回执已建、批次还没清完时，并发采集落库也必须直接写清除标记。"""
    settings = finished["settings"]
    store = finished["store"]
    receipt = redact_service.create_and_scrub(store, {"name": TARGET})
    # 把回执退回 pending，模拟「登记了但还没清完」
    with store.immediate() as conn:
        conn.execute("UPDATE redaction_targets SET status='pending' "
                     "WHERE receipt_id=?", (receipt["receipt_id"],))
        conn.execute("UPDATE redactions SET state='partial', completed_at=NULL "
                     "WHERE receipt_id=?", (receipt["receipt_id"],))
    fetched = catalog_service.make_fetcher(settings)
    bundle = catalog_service.ingest(settings, source="all", fetcher=fetched)
    exhibitors, _ = booth_service.build(bundle.pages)
    # source 全量重放会命中批次键直接复用，这里按新批次写入，模拟采集与清除并发
    new_batch = {
        "batch_id": "B-CONCURRENT-1", "batch_key": "all:concurrent",
        "run_id": "R-CONCURRENT-1", "source": "all", "status": "done",
        "created_at": "2026-11-15T00:00:00+00:00",
        "input_digest": "b" * 64, "version": 1,
    }
    store.save_run(
        batch=new_batch, pages=catalog_service.page_rows(bundle),
        exhibitors=exhibitors, forums=[], issues=[], stops=[], stages=[],
        totals={}, manifest={})
    target_rows = [row for row in _rows(store, new_batch["batch_id"])
                   if row["name"] == TARGET]
    assert target_rows and target_rows[0]["owner"] == redaction.CLEARED_MARK
    assert not _raw_owner(settings)
    # 采集落库也补登了该回执的审计 target，来源与行号齐全
    after = store.get_redaction(receipt["receipt_id"])
    assert any(target["batch_id"] == new_batch["batch_id"]
               and target["status"] == "scrubbed"
               and target["row_refs"] for target in after["targets"])
    # 原批次仍是 pending，所以回执停在 partial；续跑只清原批次，回执不新增
    assert after["state"] == "partial"
    assert after["summary"]["pending_batches"] == 1
    done = redact_service.resume(store, receipt["receipt_id"])
    assert done["state"] == "done"
    assert done["receipt_id"] == receipt["receipt_id"]
    assert len(redact_service.receipts(store)) == 1


def test_concurrent_writer_cannot_restore_text(finished, settings):
    """持写锁的清除事务与采集写事务互斥：先拿锁的一方决定结果。"""
    store = finished["store"]
    redact_service.create_and_scrub(store, {"name": TARGET})
    batch_id = finished["batch"]["batch_id"]
    fetched = catalog_service.make_fetcher(settings)
    bundle = catalog_service.ingest(settings, source="all", fetcher=fetched)
    exhibitors, _ = booth_service.build(bundle.pages)
    pages = catalog_service.page_rows(bundle)
    outcome: dict = {}

    def write():
        try:
            store.save_run(
                batch={"batch_id": batch_id, "batch_key": "x", "run_id": "r",
                       "source": "all", "status": "done",
                       "created_at": "2026-11-15T00:00:00+00:00",
                       "input_digest": "a" * 64, "version": 1},
                pages=pages, exhibitors=exhibitors, forums=[], issues=[],
                stops=[], stages=[], totals={}, manifest={})
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=write)
    thread.start()
    thread.join()
    assert outcome.get("ok") is True
    # 无论哪个顺序，落库后都没有原文
    assert not _raw_owner(settings)


def test_reader_hides_fields_before_batch_scrub_completes(finished):
    """回执已建但目标批次还是 pending 时，读路径也不能把原文交出去。"""
    store, batch = finished["store"], finished["batch"]
    subject = redaction.build_subject({"name": TARGET})
    fields = redaction.build_fields(None)
    key = redaction.withdrawal_key(subject, fields)
    receipt = store.create_redaction(
        withdrawal_key=key, receipt_id=redaction.receipt_id_for(key),
        subject=subject, fields=fields)
    assert receipt["summary"]["pending_batches"] == 1
    assert store.get_batch(batch["batch_id"]) is not None
    rows = [row for row in _rows(store, batch["batch_id"]) if row["name"] == TARGET]
    assert rows and rows[0]["owner"] == redaction.CLEARED_MARK
    # 库里的原文还没被批量清除，但读取层已经挡住
    assert _raw_owner(finished["settings"])
    # 续跑后才真正从库里清掉
    done = redact_service.resume(store, receipt["receipt_id"])
    assert done["state"] == "done"
    assert not _raw_owner(finished["settings"])


def test_scrub_waits_for_concurrent_writer(finished):
    """写锁串行化：清除与采集落库不会交错成「先读规则、后写原文」。"""
    store, batch = finished["store"], finished["batch"]
    subject = redaction.build_subject({"name": TARGET})
    fields = redaction.build_fields(None)
    key = redaction.withdrawal_key(subject, fields)
    receipt = store.create_redaction(
        withdrawal_key=key, receipt_id=redaction.receipt_id_for(key),
        subject=subject, fields=fields)
    ready, release = threading.Event(), threading.Event()
    results: dict = {}

    def hold_write():
        with store.immediate() as conn:
            conn.execute("UPDATE batches SET note='lock' WHERE batch_id=?",
                         (batch["batch_id"],))
            ready.set()
            release.wait(5)

    def scrub():
        results["receipt"] = store.scrub_target(receipt["receipt_id"],
                                                batch["batch_id"])

    holder = threading.Thread(target=hold_write)
    holder.start()
    assert ready.wait(5)
    scrubber = threading.Thread(target=scrub)
    scrubber.start()
    # 清除在排队等写锁；放锁之后才落地
    scrubber.join(0.2)
    assert scrubber.is_alive()
    release.set()
    holder.join(5)
    scrubber.join(5)
    assert not scrubber.is_alive()
    assert results["receipt"]["targets"][0]["status"] == "scrubbed"


def test_original_bytes_do_not_survive_in_database_file(finished):
    """永久清除：库文件里不再出现该展商独有的姓名与备注原文的字节。"""
    settings, store = finished["settings"], finished["store"]
    batch_id = finished["batch"]["batch_id"]
    unique_owner = "独有对接人甲"
    with store.immediate() as conn:
        conn.execute(
            """INSERT INTO exhibitors (batch_id,ex_id,name,hall,booth_raw,booth_code,
                    row_no,col_no,x_m,y_m,area_sqm,priority,owner,status,code,
                    source_url,row_ref,note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (batch_id, "E-TMP-UNIQUE", "独有展商甲", "A1", "A1-9-09", "A1-09-09",
             "09", "09", "0", "0", "18.0", 3, unique_owner, "ok", None,
             "http://sample-site/dir/list_p1.html", "dir/p1#99", "独有备注乙"))
    assert unique_owner.encode("utf-8") in open(settings.db_path, "rb").read()
    assert "独有备注乙".encode("utf-8") in open(settings.db_path, "rb").read()

    redact_service.create_and_scrub(store, {"name": "独有展商甲"})
    blob = open(settings.db_path, "rb").read()
    assert unique_owner.encode("utf-8") not in blob
    assert "独有备注乙".encode("utf-8") not in blob
    # 展商名与定位仍在，审计能对回原行
    assert "独有展商甲".encode("utf-8") in blob


def test_withdrawal_before_any_batch_still_blocks_later_crawl(tmp_path):
    """先登记撤回、再采集：新批次写入时直接被清除并补登审计 target。"""
    settings = config.Settings(db_path=str(tmp_path / "boothplan.sqlite"),
                               eval_date=dt.date(2026, 11, 15),
                               sample_site_dir=str(ROOT / "sample_site"),
                               sample_site_base="http://sample-site")
    store = Store(settings.db_path)
    receipt = redact_service.create_and_scrub(store, {"name": TARGET})
    assert receipt["state"] == "done"
    assert receipt["targets"] == []

    result = batch_service.run(settings=settings, store=store, source="all")
    assert result["status"] == "done"
    rows = [row for row in _rows(store, result["batch"]["batch_id"])
            if row["name"] == TARGET]
    assert rows and rows[0]["owner"] == redaction.CLEARED_MARK
    after = store.get_redaction(receipt["receipt_id"])
    assert after["summary"]["batches"] == 1
    assert after["targets"][0]["status"] == "scrubbed"
    assert after["targets"][0]["row_refs"]
    assert not _raw_owner(settings)


def test_withdrawal_with_no_match_is_recorded_but_empty(finished):
    store = finished["store"]
    receipt = redact_service.create_and_scrub(store, {"name": "不存在的展商"})
    assert receipt["state"] == "done"
    assert receipt["targets"] == []
    assert receipt["summary"]["matched_rows"] == 0
    assert len(redact_service.receipts(store)) == 1


def test_other_exhibitors_and_route_unaffected(finished):
    store, batch = finished["store"], finished["batch"]
    before = route_service.run(store.load_exhibitors(batch["batch_id"]),
                               store.load_forums(batch["batch_id"]))
    redact_service.create_and_scrub(store, {"name": TARGET})
    after = route_service.run(store.load_exhibitors(batch["batch_id"]),
                              store.load_forums(batch["batch_id"]))
    assert [row["ref"] for row in before["stops"]] == \
        [row["ref"] for row in after["stops"]]
    assert before["totals"] == after["totals"]


def test_withdraw_only_matching_booth_for_duplicate_names(finished):
    store, batch = finished["store"], finished["batch"]
    # 汇川技术 同名两馆；只清 B1 那个
    receipt = redact_service.create_and_scrub(
        store, {"name": "汇川技术", "booth": "B1-4-01"})
    assert receipt["state"] == "done"
    rows = [row for row in _rows(store, batch["batch_id"])
            if row["name"] == "汇川技术"]
    by_booth = {row["booth_code"]: row["owner"] for row in rows}
    assert by_booth["B1-04-01"] == redaction.CLEARED_MARK
    assert by_booth["A1-04-02"] == "王覃"


def test_export_and_page_hide_original_and_show_receipt(finished, client):
    store, batch = finished["store"], finished["batch"]
    receipt = redact_service.create_and_scrub(store, {"name": TARGET})

    evidence = client.get(f"/api/export/evidence.csv?batch_id={batch['batch_id']}")
    assert evidence.status_code == 200
    assert OWNER not in evidence.text
    assert "R_CONSENT_WITHDRAWN" in evidence.text
    assert receipt["receipt_id"] in evidence.text

    receipt_csv = client.get(f"/api/export/receipt.csv?batch_id={batch['batch_id']}")
    assert receipt_csv.status_code == 200
    assert "清除字段" in receipt_csv.text
    assert OWNER not in receipt_csv.text
    assert "dir/p1#2" in receipt_csv.text

    page = client.get("/redactions")
    assert page.status_code == 200
    assert receipt["receipt_id"] in page.text
    assert OWNER not in page.text
    assert "已清除" in page.text


def test_api_contract_and_page_for_receipt(finished, client):
    store = finished["store"]
    receipt = redact_service.create_and_scrub(store, {"name": TARGET})
    detail = client.get(f"/api/redactions/{receipt['receipt_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["state"] == "done"
    assert body["retained"] == ["来源", "行号", "原因码", "数量"]
    assert all(target["matched"] >= 1 for target in body["targets"])
    listing = client.get("/api/redactions").json()
    assert listing["count"] == 1
    assert listing["receipts"][0]["receipt_id"] == receipt["receipt_id"]


def test_withdraw_over_http(finished, client):
    body = client.post("/api/redactions", json={"name": TARGET}).json()
    assert body["state"] == "done"
    again = client.post("/api/redactions", json={"name": TARGET}).json()
    assert again["receipt_id"] == body["receipt_id"]
    missing = client.get("/api/redactions/R-NOPE")
    assert missing.status_code == 404
    bad = client.post("/api/redactions", json={"name": TARGET, "fields": ["name"]})
    assert bad.status_code == 400


def test_validation_rejects_unknown_fields_and_empty_subject(finished):
    with pytest.raises(Exception):
        redaction.build_subject({})
    with pytest.raises(Exception):
        redaction.build_fields(["exhibitor_name"])
