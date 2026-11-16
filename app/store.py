"""SQLite 存储：批次、页面、展商、论坛、隔离、行程点、阶段计数、合计、运行清单。

约定：
- 距离与面积列一律 TEXT（Decimal 字符串），不落 float；
- 一次运行的所有行在同一个事务里「先删后插」，失败整体回滚，不留半批数据；
- 读数按 batch_id 过滤，页面永远只展示一个批次。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from . import redaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    batch_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    input_digest TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    resume_of TEXT,
    params_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_batches_key ON batches(batch_key, status);
CREATE TABLE IF NOT EXISTS pages (
    batch_id TEXT NOT NULL, url TEXT NOT NULL, source TEXT NOT NULL,
    kind TEXT NOT NULL, page_no INTEGER NOT NULL, status TEXT NOT NULL,
    declared_charset TEXT, encoding TEXT, bytes INTEGER, sha256 TEXT,
    row_count INTEGER NOT NULL DEFAULT 0, code TEXT, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS exhibitors (
    batch_id TEXT NOT NULL, ex_id TEXT NOT NULL, name TEXT NOT NULL,
    hall TEXT NOT NULL, booth_raw TEXT NOT NULL, booth_code TEXT NOT NULL,
    row_no TEXT, col_no TEXT, x_m TEXT, y_m TEXT, area_sqm TEXT,
    priority INTEGER NOT NULL DEFAULT 5, owner TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, code TEXT, source_url TEXT NOT NULL,
    row_ref TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS forums (
    batch_id TEXT NOT NULL, session_id TEXT NOT NULL, title TEXT NOT NULL,
    hall TEXT NOT NULL, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL,
    day INTEGER NOT NULL, tz_raw TEXT NOT NULL DEFAULT '',
    code TEXT, detail TEXT,
    source_url TEXT NOT NULL, row_ref TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS issues (
    batch_id TEXT NOT NULL, code TEXT NOT NULL, stage TEXT NOT NULL,
    level TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
    field TEXT, raw_value TEXT, detail TEXT,
    source_url TEXT, row_ref TEXT
);
CREATE TABLE IF NOT EXISTS stops (
    batch_id TEXT NOT NULL, plan_day INTEGER NOT NULL, seq INTEGER NOT NULL,
    kind TEXT NOT NULL, ref TEXT NOT NULL, name TEXT NOT NULL, hall TEXT NOT NULL,
    arrival_utc TEXT NOT NULL, depart_utc TEXT NOT NULL,
    dwell_min INTEGER NOT NULL, walk_m TEXT NOT NULL,
    status TEXT NOT NULL, code TEXT, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS stages (
    batch_id TEXT NOT NULL, stage TEXT NOT NULL, label TEXT NOT NULL,
    in_count INTEGER NOT NULL, out_count INTEGER NOT NULL,
    quarantine_count INTEGER NOT NULL, status TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS totals (
    batch_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manifests (
    batch_id TEXT PRIMARY KEY, json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS redactions (
    receipt_id TEXT PRIMARY KEY,
    withdrawal_key TEXT NOT NULL UNIQUE,
    subject_ex_id TEXT NOT NULL DEFAULT '',
    subject_name TEXT NOT NULL DEFAULT '',
    subject_label TEXT NOT NULL DEFAULT '',
    subject_booth TEXT NOT NULL DEFAULT '',
    fields_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_redactions_state ON redactions(state, created_at);
CREATE TABLE IF NOT EXISTS redaction_targets (
    receipt_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    status TEXT NOT NULL,
    matched INTEGER NOT NULL DEFAULT 0,
    scrubbed INTEGER NOT NULL DEFAULT 0,
    source_url TEXT NOT NULL DEFAULT '',
    row_refs_json TEXT NOT NULL DEFAULT '[]',
    owner_cleared INTEGER NOT NULL DEFAULT 0,
    note_cleared INTEGER NOT NULL DEFAULT 0,
    cleared_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (receipt_id, batch_id)
);
CREATE TABLE IF NOT EXISTS redaction_events (
    receipt_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    batch_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (receipt_id, seq)
);
"""


class Store:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            _migrate(conn)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        # secure_delete：UPDATE/DELETE 释放的旧页内容写零，避免清除后的原文
        # 还残留在数据库文件的空闲页里（永久清除，不是逻辑遮挡）。
        conn.execute("PRAGMA secure_delete=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def immediate(self):
        """显式写事务：先拿写锁再读规则，避免并发采集与清除互相覆盖。

        sqlite3 默认的延迟事务是「先读后写」，读到的撤回规则可能已经过期；
        这里用 BEGIN IMMEDIATE 先把写锁拿到，再读规则、再写名录行。
        """
        conn = sqlite3.connect(self.path, timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            conn.close()

    # ---------- 批次 ----------
    def upsert_batch(self, batch: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO batches (batch_id, batch_key, run_id, source, status,
                        created_at, finished_at, input_digest, version, resume_of,
                        params_json, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     status=excluded.status, finished_at=excluded.finished_at,
                     note=excluded.note, params_json=excluded.params_json""",
                (batch["batch_id"], batch["batch_key"], batch["run_id"], batch["source"],
                 batch["status"], batch["created_at"], batch.get("finished_at"),
                 batch["input_digest"], batch.get("version", 1), batch.get("resume_of"),
                 json.dumps(batch.get("params", {}), ensure_ascii=False),
                 batch.get("note", "")),
            )

    def find_done_by_key(self, batch_key: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE batch_key=? AND status='done' "
                "ORDER BY created_at LIMIT 1", (batch_key,)).fetchone()
        return dict(row) if row else None

    def get_batch(self, batch_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE batch_id=?",
                               (batch_id,)).fetchone()
        return dict(row) if row else None

    def latest_batch(self, status: str = "done") -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE status=? ORDER BY created_at DESC, rowid DESC "
                "LIMIT 1", (status,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM batches ORDER BY created_at DESC, rowid DESC LIMIT 1"
                ).fetchone()
        return dict(row) if row else None

    def list_batches(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM batches ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def next_version(self, batch_key: str) -> int:
        """同一批次键的下一版本号；用于陈旧提交拒绝。"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM batches WHERE batch_key=?",
                (batch_key,)).fetchone()
        return int(row["v"] or 0) + 1

    # ---------- 运行结果落库（单事务替换） ----------
    def save_run(self, *, batch: dict, pages: list[dict], exhibitors: list[dict],
                 forums: list[dict], issues: list[dict], stops: list[dict],
                 stages: list[dict], totals: dict, manifest: dict) -> None:
        bid = batch["batch_id"]
        with self.immediate() as conn:
            # 写锁已拿到：先读撤回规则再落行，清除与并发采集不会互相覆盖。
            # 只要回执存在（进行中或已完成），写回的名录就不能恢复原文。
            exhibitors, policy_hits = _scrub_exhibitors(conn, bid, exhibitors)
            for table in ("pages", "exhibitors", "forums", "issues", "stops",
                          "stages", "totals"):
                conn.execute(f"DELETE FROM {table} WHERE batch_id=?", (bid,))
            for p in pages:
                conn.execute(
                    """INSERT INTO pages (batch_id,url,source,kind,page_no,status,
                        declared_charset,encoding,bytes,sha256,row_count,code,note)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bid, p["url"], p["source"], p["kind"], p["page_no"], p["status"],
                     p.get("declared_charset"), p.get("encoding"), p.get("bytes"),
                     p.get("sha256"), p.get("row_count", 0), p.get("code"),
                     p.get("note", "")))
            for e in exhibitors:
                conn.execute(
                    """INSERT INTO exhibitors (batch_id,ex_id,name,hall,booth_raw,
                        booth_code,row_no,col_no,x_m,y_m,area_sqm,priority,owner,status,
                        code,source_url,row_ref,note)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bid, e["ex_id"], e["name"], e["hall"], e["booth_raw"],
                     e["booth_code"], e.get("row_no"), e.get("col_no"),
                     _s(e.get("x_m")), _s(e.get("y_m")), _s(e.get("area_sqm")),
                     e.get("priority", 5), e.get("owner", ""), e["status"],
                     e.get("code"), e["source_url"], e["row_ref"], e.get("note", "")))
            for f in forums:
                conn.execute(
                    """INSERT INTO forums (batch_id,session_id,title,hall,start_utc,
                        end_utc,day,tz_raw,code,detail,source_url,row_ref,note)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bid, f["session_id"], f["title"], f["hall"], f["start_utc"],
                     f["end_utc"], f["day"], f.get("tz_raw", ""), f.get("code"),
                     f.get("detail"), f["source_url"], f["row_ref"],
                     f.get("note", "")))
            for q in issues:
                conn.execute(
                    """INSERT INTO issues (batch_id,code,stage,level,subject,field,
                        raw_value,detail,source_url,row_ref) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (bid, q["code"], q["stage"], q["level"], q.get("subject", ""),
                     q.get("field"), q.get("raw_value"), q.get("detail"),
                     q.get("source_url"), q.get("row_ref")))
            for s in stops:
                conn.execute(
                    """INSERT INTO stops (batch_id,plan_day,seq,kind,ref,name,hall,
                        arrival_utc,depart_utc,dwell_min,walk_m,status,code,note)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bid, s["plan_day"], s["seq"], s["kind"], s["ref"], s["name"],
                     s["hall"], s["arrival_utc"], s["depart_utc"],
                     s.get("dwell_min", s.get("dwell", 0)),
                     str(s["walk_m"]), s["status"], s.get("code"), s.get("note", "")))
            for s in stages:
                conn.execute(
                    """INSERT INTO stages (batch_id,stage,label,in_count,out_count,
                        quarantine_count,status,elapsed_ms,note)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (bid, s["stage"], s["label"], s["in_count"], s["out_count"],
                     s["quarantine_count"], s["status"], s["elapsed_ms"],
                     s.get("note", "")))
            for key, value in totals.items():
                conn.execute("INSERT INTO totals (batch_id,key,value) VALUES (?,?,?)",
                             (bid, key, str(value)))
            conn.execute(
                "INSERT INTO manifests (batch_id,json) VALUES (?,?) "
                "ON CONFLICT(batch_id) DO UPDATE SET json=excluded.json",
                (bid, json.dumps(manifest, ensure_ascii=False)))
            conn.execute(
                """INSERT INTO batches (batch_id,batch_key,run_id,source,status,
                        created_at,finished_at,input_digest,version,resume_of,
                        params_json,note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     status=excluded.status, finished_at=excluded.finished_at,
                     note=excluded.note, input_digest=excluded.input_digest""",
                (batch["batch_id"], batch["batch_key"], batch["run_id"], batch["source"],
                 batch["status"], batch["created_at"], batch.get("finished_at"),
                 batch["input_digest"], batch.get("version", 1), batch.get("resume_of"),
                 json.dumps(batch.get("params", {}), ensure_ascii=False),
                 batch.get("note", "")))
            _record_audit(conn, policy_hits)

    def save_attempt(self, *, batch: dict, pages: list[dict], stages: list[dict],
                     note: str = "") -> None:
        """失败尝试：只写批次、页面与阶段，不清空其他批次的任何数据。"""
        bid = batch["batch_id"]
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO batches (batch_id,batch_key,run_id,source,status,
                        created_at,finished_at,input_digest,version,resume_of,
                        params_json,note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     status=excluded.status, finished_at=excluded.finished_at,
                     note=excluded.note""",
                (batch["batch_id"], batch["batch_key"], batch["run_id"], batch["source"],
                 batch["status"], batch["created_at"], batch.get("finished_at"),
                 batch["input_digest"], batch.get("version", 1), batch.get("resume_of"),
                 json.dumps(batch.get("params", {}), ensure_ascii=False),
                 note or batch.get("note", "")))
            conn.execute("DELETE FROM pages WHERE batch_id=?", (bid,))
            conn.execute("DELETE FROM stages WHERE batch_id=?", (bid,))
            for p in pages:
                conn.execute(
                    """INSERT INTO pages (batch_id,url,source,kind,page_no,status,
                        declared_charset,encoding,bytes,sha256,row_count,code,note)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bid, p["url"], p["source"], p["kind"], p["page_no"], p["status"],
                     p.get("declared_charset"), p.get("encoding"), p.get("bytes"),
                     p.get("sha256"), p.get("row_count", 0), p.get("code"),
                     p.get("note", "")))
            for s in stages:
                conn.execute(
                    """INSERT INTO stages (batch_id,stage,label,in_count,out_count,
                        quarantine_count,status,elapsed_ms,note)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (bid, s["stage"], s["label"], s["in_count"], s["out_count"],
                     s["quarantine_count"], s["status"], s["elapsed_ms"],
                     s.get("note", "")))

    # ---------- 读取 ----------
    def load_pages(self, batch_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM pages WHERE batch_id=? ORDER BY source, page_no, url",
            (batch_id,))

    def load_exhibitors(self, batch_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM exhibitors WHERE batch_id=? "
                "ORDER BY hall, row_no, col_no, ex_id", (batch_id,)).fetchall()]
            # 先读行再读规则：读行期间新建的回执也能套上，缩小「读到原文」的窗口。
            policies = _policies(conn)
        for r in rows:
            for key in ("x_m", "y_m", "area_sqm"):
                r[key] = None if r[key] is None else Decimal(r[key])
        # 读取路径也套一次撤回规则：回执刚建好、批量清除还没跑完时，
        # 页面与导出同样看不到原文。清除完成后库里已无原文，这一步是幂等的。
        return _apply_policies(rows, policies)

    def load_forums(self, batch_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM forums WHERE batch_id=? ORDER BY start_utc, session_id",
            (batch_id,))

    def load_issues(self, batch_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM issues WHERE batch_id=? ORDER BY stage, code, row_ref",
            (batch_id,))

    def load_stops(self, batch_id: str, plan_day: int | None = None) -> list[dict]:
        if plan_day is None:
            rows = self._all(
                "SELECT * FROM stops WHERE batch_id=? ORDER BY plan_day, seq", (batch_id,))
        else:
            rows = self._all(
                "SELECT * FROM stops WHERE batch_id=? AND plan_day=? ORDER BY seq",
                (batch_id, plan_day))
        for r in rows:
            r["walk_m"] = Decimal(r["walk_m"])
        return rows

    def load_stages(self, batch_id: str) -> list[dict]:
        return self._all("SELECT * FROM stages WHERE batch_id=? ORDER BY rowid", (batch_id,))

    def load_totals(self, batch_id: str) -> dict:
        rows = self._all("SELECT key, value FROM totals WHERE batch_id=?", (batch_id,))
        return {r["key"]: r["value"] for r in rows}

    def load_manifest(self, batch_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT json FROM manifests WHERE batch_id=?",
                               (batch_id,)).fetchone()
        return json.loads(row["json"]) if row else None

    def count_rows(self, batch_id: str) -> dict:
        with self.connect() as conn:
            out = {}
            for table in ("pages", "exhibitors", "forums", "issues", "stops"):
                out[table] = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE batch_id=?", (batch_id,)
                ).fetchone()["n"]
        return out

    # ---------- 撤回清除回执 ----------
    # 回执是清除流程的台账，不是缓存：同一撤回键只有一张回执，中断后续跑沿用
    # 同一个 receipt_id；每个受影响的批次一行 target，记来源、行号、数量与
    # 字段命中情况。原值在回执里一个字都不留。
    def create_redaction(self, *, withdrawal_key: str, receipt_id: str,
                         subject: dict, fields: list[str], note: str = "") -> dict:
        now = _now_iso()
        with self.immediate() as conn:
            existing = conn.execute(
                "SELECT * FROM redactions WHERE withdrawal_key=?",
                (withdrawal_key,)).fetchone()
            if existing is not None:
                return self._redaction_payload(conn, dict(existing))
            conn.execute(
                """INSERT INTO redactions (receipt_id, withdrawal_key, subject_ex_id,
                        subject_name, subject_label, subject_booth, fields_json,
                        state, created_at, updated_at, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (receipt_id, withdrawal_key, subject.get("ex_id", ""),
                 subject.get("name", ""), subject.get("label", ""),
                 subject.get("booth", ""),
                 json.dumps(fields, ensure_ascii=False), "pending", now, now, note))
            batches = conn.execute(
                "SELECT batch_id FROM batches ORDER BY created_at, rowid").fetchall()
            scanned = 0
            for batch in batches:
                scanned += 1
                hit = _scan_batch(conn, batch["batch_id"], subject)
                if hit["matched"]:
                    conn.execute(
                        """INSERT INTO redaction_targets (receipt_id, batch_id, status,
                                matched, scrubbed, source_url, row_refs_json)
                           VALUES (?,?,?,?,?,?,?)""",
                        (receipt_id, batch["batch_id"], "pending", hit["matched"], 0,
                         hit["source_url"],
                         json.dumps(hit["row_refs"], ensure_ascii=False)))
            self._event(conn, receipt_id, "created",
                        detail=f"扫描 {scanned} 个批次，命中 "
                               f"{self._count_targets(conn, receipt_id)} 个")
            if self._count_targets(conn, receipt_id, status="pending") == 0:
                conn.execute(
                    "UPDATE redactions SET state='done', updated_at=?, completed_at=? "
                    "WHERE receipt_id=?", (now, now, receipt_id))
            row = conn.execute("SELECT * FROM redactions WHERE receipt_id=?",
                               (receipt_id,)).fetchone()
            return self._redaction_payload(conn, dict(row))

    def get_redaction(self, receipt_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM redactions WHERE receipt_id=?",
                               (receipt_id,)).fetchone()
            return self._redaction_payload(conn, dict(row)) if row else None

    def find_redaction(self, withdrawal_key: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM redactions WHERE withdrawal_key=?",
                               (withdrawal_key,)).fetchone()
            return self._redaction_payload(conn, dict(row)) if row else None

    def list_redactions(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM redactions ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,)).fetchall()
            return [self._redaction_payload(conn, dict(row)) for row in rows]

    def pending_redaction_targets(self, receipt_id: str,
                                  limit: int | None = None) -> list[str]:
        with self.connect() as conn:
            sql = ("SELECT batch_id FROM redaction_targets WHERE receipt_id=? "
                   "AND status='pending' ORDER BY rowid")
            args: tuple = (receipt_id,)
            if limit is not None:
                sql += " LIMIT ?"
                args = (receipt_id, int(limit))
            return [row["batch_id"] for row in conn.execute(sql, args).fetchall()]

    def scrub_pending_for_batch(self, batch_id: str) -> list[str]:
        """把某批次上还没清的 pending target 就地清掉，返回涉及的回执号。

        用于幂等重放：命中旧批次时不新增行，但如果回执建了还没清完，
        重放正好把这一批补清，而不是把原文留在库里等人工续跑。
        """
        with self.connect() as conn:
            receipts = [row["receipt_id"] for row in conn.execute(
                "SELECT receipt_id FROM redaction_targets WHERE batch_id=? "
                "AND status='pending' ORDER BY rowid", (batch_id,)).fetchall()]
        for receipt_id in receipts:
            self.scrub_target(receipt_id, batch_id)
        return receipts

    def refresh_redaction_targets(self, receipt_id: str) -> dict | None:
        """补扫回执建立之后新增的批次；同一撤回请求重发时也走这里。

        已有 target 的批次不重扫，所以续跑不会把已清除的批次又算一遍命中。
        """
        now = _now_iso()
        with self.immediate() as conn:
            receipt = conn.execute("SELECT * FROM redactions WHERE receipt_id=?",
                                   (receipt_id,)).fetchone()
            if receipt is None:
                return None
            subject = _subject_of(dict(receipt))
            known = {row["batch_id"] for row in conn.execute(
                "SELECT batch_id FROM redaction_targets WHERE receipt_id=?",
                (receipt_id,)).fetchall()}
            added = 0
            for batch in conn.execute(
                    "SELECT batch_id FROM batches ORDER BY created_at, rowid").fetchall():
                if batch["batch_id"] in known:
                    continue
                hit = _scan_batch(conn, batch["batch_id"], subject)
                if not hit["matched"]:
                    continue
                conn.execute(
                    """INSERT INTO redaction_targets (receipt_id, batch_id, status,
                            matched, scrubbed, source_url, row_refs_json)
                       VALUES (?,?,?,?,?,?,?)""",
                    (receipt_id, batch["batch_id"], "pending", hit["matched"], 0,
                     hit["source_url"],
                     json.dumps(hit["row_refs"], ensure_ascii=False)))
                added += 1
            if added:
                pending = self._count_targets(conn, receipt_id, status="pending")
                state = "done" if pending == 0 else "partial"
                conn.execute(
                    "UPDATE redactions SET state=?, updated_at=?, completed_at=? "
                    "WHERE receipt_id=?",
                    (state, now, now if state == "done" else None, receipt_id))
                self._event(conn, receipt_id, "rescanned",
                            detail=f"补扫到 {added} 个新命中批次")
            row = conn.execute("SELECT * FROM redactions WHERE receipt_id=?",
                               (receipt_id,)).fetchone()
            return self._redaction_payload(conn, dict(row))

    def scrub_target(self, receipt_id: str, batch_id: str) -> dict:
        """清除一个批次里该主体的姓名与备注原文；单事务、可重复执行。"""
        now = _now_iso()
        with self.immediate() as conn:
            receipt = conn.execute("SELECT * FROM redactions WHERE receipt_id=?",
                                   (receipt_id,)).fetchone()
            if receipt is None:
                return {}
            target = conn.execute(
                "SELECT * FROM redaction_targets WHERE receipt_id=? AND batch_id=?",
                (receipt_id, batch_id)).fetchone()
            if target is None or target["status"] != "pending":
                return self._redaction_payload(conn, dict(receipt))
            subject = _subject_of(dict(receipt))
            fields = json.loads(receipt["fields_json"])
            rows = conn.execute(
                "SELECT rowid, ex_id, name, booth_raw, booth_code, owner, note, "
                "source_url, row_ref FROM exhibitors WHERE batch_id=?",
                (batch_id,)).fetchall()
            owner_cleared = note_cleared = scrubbed = matched = 0
            refs: list[str] = []
            source_url = target["source_url"] or ""
            for row in rows:
                record = dict(row)
                if not redaction.record_matches(record, subject):
                    continue
                matched += 1
                if record.get("row_ref"):
                    refs.append(record["row_ref"])
                if record.get("source_url"):
                    source_url = source_url or record["source_url"]
                changed = False
                for field in fields:
                    value = record.get(field) or ""
                    if value and value != redaction.CLEARED_MARK:
                        conn.execute(
                            f"UPDATE exhibitors SET {field}=? WHERE batch_id=? AND rowid=?",
                            (redaction.CLEARED_MARK, batch_id, row["rowid"]))
                        changed = True
                        if field == "owner":
                            owner_cleared += 1
                        elif field == "note":
                            note_cleared += 1
                if changed:
                    scrubbed += 1
            conn.execute(
                """UPDATE redaction_targets SET status='scrubbed', matched=?,
                        scrubbed=?, source_url=?, row_refs_json=?,
                        owner_cleared=?, note_cleared=?, cleared_at=?
                   WHERE receipt_id=? AND batch_id=?""",
                (matched, scrubbed, source_url,
                 json.dumps(refs, ensure_ascii=False), owner_cleared, note_cleared,
                 now, receipt_id, batch_id))
            self._event(conn, receipt_id, "scrubbed", batch_id=batch_id,
                        detail=f"命中 {matched} 行，清除 {scrubbed} 行")
            pending = self._count_targets(conn, receipt_id, status="pending")
            state = "done" if pending == 0 else "partial"
            conn.execute(
                "UPDATE redactions SET state=?, updated_at=?, completed_at=? "
                "WHERE receipt_id=?",
                (state, now, now if state == "done" else None, receipt_id))
            row = conn.execute("SELECT * FROM redactions WHERE receipt_id=?",
                               (receipt_id,)).fetchone()
            payload = self._redaction_payload(conn, dict(row))
        if state == "done":
            # 事务外 VACUUM：把释放的旧行与空闲页写零，原文不再以任何形式
            # 留在数据库文件里（TRAP-31 的「永久」部分）。
            self._vacuum()
        return payload

    def _vacuum(self) -> None:
        """尽力回收；已有读者占着库时跳过，secure_delete 已把释放页写零。"""
        try:
            with self.connect() as conn:
                conn.execute("VACUUM")
        except sqlite3.OperationalError:
            pass

    def mark_redaction_events(self, receipt_id: str, action: str, detail: str = "",
                              batch_id: str = "") -> None:
        with self.connect() as conn:
            self._event(conn, receipt_id, action, batch_id=batch_id, detail=detail)

    def _event(self, conn, receipt_id: str, action: str, *, batch_id: str = "",
               detail: str = "") -> None:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM redaction_events "
            "WHERE receipt_id=?", (receipt_id,)).fetchone()["n"]
        conn.execute(
            "INSERT INTO redaction_events (receipt_id, seq, at, action, batch_id, "
            "detail) VALUES (?,?,?,?,?,?)",
            (receipt_id, seq, _now_iso(), action, batch_id, detail))

    def _count_targets(self, conn, receipt_id: str, status: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM redaction_targets WHERE receipt_id=?"
        args: tuple = (receipt_id,)
        if status is not None:
            sql += " AND status=?"
            args = (receipt_id, status)
        return conn.execute(sql, args).fetchone()["n"]

    def _redaction_payload(self, conn, receipt: dict) -> dict:
        targets = [dict(row) for row in conn.execute(
            "SELECT t.*, (SELECT status FROM batches b WHERE b.batch_id=t.batch_id) "
            "AS batch_status, (SELECT batch_key FROM batches b "
            "WHERE b.batch_id=t.batch_id) AS batch_key "
            "FROM redaction_targets t WHERE t.receipt_id=? ORDER BY t.rowid",
            (receipt["receipt_id"],)).fetchall()]
        for target in targets:
            target["row_refs"] = json.loads(target.pop("row_refs_json") or "[]")
        events = [dict(row) for row in conn.execute(
            "SELECT * FROM redaction_events WHERE receipt_id=? ORDER BY seq",
            (receipt["receipt_id"],)).fetchall()]
        fields = json.loads(receipt["fields_json"])
        summary = {
            "batches": len(targets),
            "pending_batches": sum(1 for t in targets if t["status"] == "pending"),
            "matched_rows": sum(t["matched"] for t in targets),
            "scrubbed_rows": sum(t["scrubbed"] for t in targets),
            "owner_cleared": sum(t["owner_cleared"] for t in targets),
            "note_cleared": sum(t["note_cleared"] for t in targets),
        }
        return {
            "receipt_id": receipt["receipt_id"],
            "withdrawal_key": receipt["withdrawal_key"],
            "subject": {
                "ex_id": receipt["subject_ex_id"],
                "name": receipt["subject_label"] or receipt["subject_name"],
                "normalized_name": receipt["subject_name"],
                "booth": receipt["subject_booth"],
            },
            "fields": fields,
            "fields_label": redaction.fields_label(fields),
            "state": receipt["state"],
            "state_label": REDACTION_STATES.get(receipt["state"], receipt["state"]),
            "created_at": receipt["created_at"],
            "updated_at": receipt["updated_at"],
            "completed_at": receipt["completed_at"],
            "note": receipt["note"],
            "reason_code": redaction.REASON_CODE,
            "summary": summary,
            "targets": targets,
            "events": events,
        }

    def _all(self, sql: str, args: tuple) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _s(value):
    return None if value is None else str(value)


REDACTION_STATES = {"pending": "待清除", "partial": "清除中", "done": "已清除"}


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _subject_of(receipt: dict) -> dict:
    return {
        "ex_id": receipt.get("subject_ex_id", ""),
        "name": receipt.get("subject_name", ""),
        "label": receipt.get("subject_label", "") or receipt.get("subject_name", ""),
        "booth": receipt.get("subject_booth", ""),
    }


def _migrate(conn) -> None:
    """老库补列：SCHEMA 只建新表，已有库要按需加列，避免升级后读不到。"""
    wanted = {"redactions": {"subject_label": "TEXT NOT NULL DEFAULT ''"}}
    for table, columns in wanted.items():
        existing = {row["name"] for row in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _policies(conn) -> list[dict]:
    """所有撤回回执（进行中与已完成）→ 一组清除规则。

    规则只留主体与字段，不含原文。只要回执存在，写库与读库都必须套用，
    这样并发采集回写原文、重放旧名录都不会恢复已撤回内容。
    """
    rows = conn.execute(
        "SELECT receipt_id, subject_ex_id, subject_name, subject_label, "
        "subject_booth, fields_json, state FROM redactions ORDER BY rowid").fetchall()
    return [{"receipt_id": row["receipt_id"],
             "subject": _subject_of(dict(row)),
             "fields": json.loads(row["fields_json"]),
             "state": row["state"]} for row in rows]

def _apply_policies(rows: list[dict], policies: list[dict]) -> list[dict]:
    """按回执把姓名与备注改成清除标记；其余列一个不动。"""
    for policy in policies:
        for record in rows:
            if not redaction.record_matches(record, policy["subject"]):
                continue
            for field in policy["fields"]:
                if record.get(field):
                    record[field] = redaction.CLEARED_MARK
    return rows


def _scrub_exhibitors(conn, batch_id: str,
                      exhibitors: list[dict]) -> tuple[list[dict], list[dict]]:
    """落库前按回执清除，并把命中情况登记成该回执的 target（并发采集也留痕）。

    返回 (清除后的名录行, 需要登记的回执命中)。写库是同事务的，
    所以不会出现「先读规则、后写原文」的窗口。
    """
    policies = _policies(conn)
    if not policies:
        return exhibitors, []
    now = _now_iso()
    audit: list[dict] = []
    for policy in policies:
        matched = scrubbed = owner_cleared = note_cleared = 0
        refs: list[str] = []
        source_url = ""
        for record in exhibitors:
            if not redaction.record_matches(record, policy["subject"]):
                continue
            matched += 1
            if record.get("row_ref"):
                refs.append(record["row_ref"])
            if record.get("source_url"):
                source_url = source_url or record["source_url"]
            changed = False
            for field in policy["fields"]:
                if record.get(field):
                    record[field] = redaction.CLEARED_MARK
                    changed = True
                    if field == "owner":
                        owner_cleared += 1
                    elif field == "note":
                        note_cleared += 1
            if changed:
                scrubbed += 1
        if matched:
            audit.append({"receipt_id": policy["receipt_id"], "batch_id": batch_id,
                          "matched": matched, "scrubbed": scrubbed,
                          "source_url": source_url, "row_refs": refs,
                          "owner_cleared": owner_cleared,
                          "note_cleared": note_cleared, "at": now})
    return exhibitors, audit


def _record_audit(conn, audit: list[dict]) -> None:
    """采集落库时已被规则清除的批次，也补一张 target，审计时数量与来源齐全。"""
    touched: set[str] = set()
    for item in audit:
        conn.execute(
            """INSERT INTO redaction_targets (receipt_id, batch_id, status, matched,
                    scrubbed, source_url, row_refs_json, owner_cleared, note_cleared,
                    cleared_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(receipt_id, batch_id) DO UPDATE SET
                 status='scrubbed', matched=excluded.matched,
                 scrubbed=MAX(redaction_targets.scrubbed, excluded.scrubbed),
                 source_url=excluded.source_url,
                 row_refs_json=excluded.row_refs_json,
                 owner_cleared=MAX(redaction_targets.owner_cleared,
                                   excluded.owner_cleared),
                 note_cleared=MAX(redaction_targets.note_cleared,
                                  excluded.note_cleared),
                 cleared_at=excluded.cleared_at, note=excluded.note""",
            (item["receipt_id"], item["batch_id"], "scrubbed", item["matched"],
             item["scrubbed"], item["source_url"],
             json.dumps(item["row_refs"], ensure_ascii=False),
             item["owner_cleared"], item["note_cleared"], item["at"],
             "采集落库时按清除回执直接清除"))
        touched.add(item["receipt_id"])
    # 并发采集可能把最后一个 pending 批次直接清掉；回执状态要跟着收敛到 done，
    # 否则页面会一直显示「清除中」却没有任何可续跑的批次。
    for receipt_id in touched:
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM redaction_targets "
            "WHERE receipt_id=? AND status='pending'", (receipt_id,)).fetchone()["n"]
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM redaction_targets WHERE receipt_id=?",
            (receipt_id,)).fetchone()["n"]
        if total and pending == 0:
            conn.execute(
                "UPDATE redactions SET state='done', updated_at=?, completed_at=? "
                "WHERE receipt_id=? AND state!='done'",
                (_now_iso(), _now_iso(), receipt_id))
        elif pending:
            conn.execute(
                "UPDATE redactions SET state='partial', updated_at=? "
                "WHERE receipt_id=? AND state!='done'",
                (_now_iso(), receipt_id))


def _scan_batch(conn, batch_id: str, subject: dict) -> dict:
    """只看名录行是否属于撤回主体，记来源、行号与数量；不读也不写原文。"""
    rows = conn.execute(
        "SELECT ex_id, name, booth_raw, booth_code, source_url, row_ref "
        "FROM exhibitors WHERE batch_id=?", (batch_id,)).fetchall()
    refs: list[str] = []
    source_url = ""
    matched = 0
    for row in rows:
        record = dict(row)
        if not redaction.record_matches(record, subject):
            continue
        matched += 1
        if record.get("row_ref"):
            refs.append(record["row_ref"])
        if record.get("source_url"):
            source_url = source_url or record["source_url"]
    return {"matched": matched, "row_refs": refs, "source_url": source_url}
