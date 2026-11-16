"""展位解析服务：把名录页的行变成带坐标的展商记录。

一行可能同时命中多个问题，判定顺序固定（见 config.CLASSIFY_ORDER 同族规则）：
列数 → 名称 → 展位号 → 展馆一致性 → 面积 → 同名冲突。前面的判掉就不再往下走，
所以隔离原因只有一个主码，副码进备注，避免同一行被反复记。
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

from ... import config, reasons
from . import contract, geometry, normalize, parse

# 展商状态：ok 可排程；unplaced 有记录但没坐标；isolated 隔离；dup 同名冲突
STATUS_OK = "ok"
STATUS_UNPLACED = "unplaced"
STATUS_ISOLATED = "isolated"
STATUS_DUP = "dup"
STATUS_LABELS = {
    STATUS_OK: "可排程", STATUS_UNPLACED: "无坐标",
    STATUS_ISOLATED: "隔离", STATUS_DUP: "同名冲突",
}


def issue(code: str, *, stage: str = "booth", subject: str = "",
          field_name: str | None = None, raw_value: str | None = None,
          detail: str | None = None, row_ref: str | None = None,
          source_url: str | None = None) -> dict:
    reason = reasons.get(code)
    return {
        "code": code, "stage": reason.stage if stage is None else stage,
        "level": reason.level, "subject": subject, "field": field_name,
        "raw_value": raw_value, "detail": detail or reason.title,
        "source_url": source_url, "row_ref": row_ref,
    }


def build(pages: list) -> tuple[list[dict], list[dict]]:
    exhibitors: list[dict] = []
    issues: list[dict] = []
    seen_names: dict[str, dict] = {}
    seen_booths: dict[str, dict] = {}

    for page in pages:
        if page.kind not in ("list", "attachment"):
            continue
        if not page.header:
            continue
        mapping = contract.map_header(page.header)
        blocked = _page_blocker(mapping)
        if blocked is not None:
            issues.append(issue(blocked, subject=page.url,
                                detail=f"{reasons.label(blocked)}（表头缺必需列）",
                                source_url=page.url,
                                row_ref=f"{page.source}/p{page.page_no}"))
            continue
        fields = mapping["fields"]
        for index, row in enumerate(page.rows):
            row_ref = f"{page.source}/p{page.page_no}#{index + 2}"
            record, row_issues = _one_row(page, row, fields, row_ref)
            issues.extend(row_issues)
            if record is None:
                continue
            _dedupe(record, seen_names, seen_booths, issues)
            exhibitors.append(record)

    for record in exhibitors:
        if record["booth_code"]:
            seen_booths.setdefault(record["booth_code"], record)
    return exhibitors, issues


def _page_blocker(mapping: dict) -> str | None:
    if "exhibitor_name" in mapping["missing"]:
        return "Q_MISSING_NAME"
    if "booth_raw" in mapping["missing"]:
        return "Q_BOOTH_EMPTY"
    return None


def _one_row(page, row: list[str], fields: dict, row_ref: str) -> tuple:
    issues: list[dict] = []
    width = len(page.header)
    if len(row) != width:
        issues.append(issue(
            "Q_COLUMN_COUNT", subject=page.url, field_name="*",
            raw_value=f"{len(row)} 列", row_ref=row_ref, source_url=page.url,
            detail=f"{reasons.label('Q_COLUMN_COUNT')}：表头 {width} 列，本行 {len(row)} 列"))
        return None, issues

    raw_name = contract.cell(row, fields, "exhibitor_name")
    name = normalize.clean_text(raw_name)
    if normalize.is_sentinel(raw_name):
        issues.append(issue("Q_MISSING_NAME", subject=normalize.clean_text(raw_name) or "（空名称）",
                            field_name="exhibitor_name", raw_value=raw_name,
                            row_ref=row_ref, source_url=page.url))
        return None, issues

    raw_booth = contract.cell(row, fields, "booth_raw")
    parsed = parse.parse_booth(raw_booth)
    if parsed["kind"] == "none":
        code = parsed["code"] or "Q_BOOTH_UNPARSABLE"
        issues.append(issue(code, subject=name, field_name="booth_raw",
                            raw_value=raw_booth, row_ref=row_ref,
                            source_url=page.url))
        return None, issues

    raw_hall = contract.cell(row, fields, "hall_raw")
    hall_from_column = parse.hall_of_raw(raw_hall)
    if parsed["kind"] == "grid" and hall_from_column and hall_from_column != parsed["hall"]:
        issues.append(issue(
            "Q_HALL_MISMATCH", subject=name, field_name="hall_raw",
            raw_value=f"馆列 {raw_hall} / 展位号馆 {parsed['hall']}", row_ref=row_ref,
            source_url=page.url, detail="跨馆写法冲突，以展位号为准但整行隔离"))
        return None, issues

    hall = parsed["hall"]
    area = None
    raw_area = contract.cell(row, fields, "area_sqm")
    if not normalize.is_sentinel(raw_area):
        try:
            area = normalize.parse_decimal(raw_area, field="展位面积")
        except ValueError as exc:
            issues.append(issue("Q_BAD_AREA", subject=name, field_name="area_sqm",
                                raw_value=raw_area, row_ref=row_ref,
                                source_url=page.url, detail=str(exc)))

    priority = 5
    raw_priority = contract.cell(row, fields, "visit_priority")
    note_bits: list[str] = []
    if not normalize.is_sentinel(raw_priority):
        try:
            priority = normalize.parse_int(raw_priority, field="拜访优先级")
        except ValueError:
            note_bits.append("优先级无法解析，按 5 处理")

    status = STATUS_OK
    code = None
    if parsed["kind"] == "area":
        status = STATUS_UNPLACED
        code = "Q_BOOTH_AREA_ONLY"
    position = geometry.position(hall, parsed["row"], parsed["col"])
    x_m = y_m = None
    if status == STATUS_OK:
        if position is None:
            status = STATUS_UNPLACED
            code = "Q_HALL_UNKNOWN"
        else:
            x_m, y_m = position
    if note_bits:
        note_bits.append(contract.cell(row, fields, "note") or "")
    else:
        note_bits.append(contract.cell(row, fields, "note") or "")

    record = {
        "ex_id": ex_id(name, parsed["booth_code"] or raw_booth or name),
        "name": name, "hall": hall,
        "booth_raw": normalize.clean_text(raw_booth),
        "booth_code": parsed["booth_code"],
        "row_no": parsed["row"], "col_no": parsed["col"],
        "x_m": x_m, "y_m": y_m, "area_sqm": area,
        "priority": priority,
        "owner": normalize.clean_text(contract.cell(row, fields, "owner")),
        "status": status, "code": code,
        "source_url": page.url, "row_ref": row_ref,
        "note": " · ".join(bit for bit in note_bits if bit),
    }
    return record, issues


def _dedupe(record: dict, seen_names: dict, seen_booths: dict,
            issues: list[dict]) -> None:
    """同名展商冲突与展位号占用：两条都留证据，取首次出现为主（TRAP-14）。"""
    key = normalize.normalize_name(record["name"])
    first = seen_names.get(key)
    if first is not None:
        same_hall = first["hall"] == record["hall"]
        detail = "同名同馆，保留首次出现" if same_hall else "同名不同馆，保留首次出现"
        record["status"] = STATUS_DUP
        record["code"] = "Q_DUP_EXHIBITOR"
        record["note"] = (record["note"] + " · " if record["note"] else "") + detail
        issues.append(issue(
            "Q_DUP_EXHIBITOR", subject=record["name"], field_name="exhibitor_name",
            raw_value=f"{first['booth_code'] or first['booth_raw']} vs "
                      f"{record['booth_code'] or record['booth_raw']}",
            row_ref=record["row_ref"], source_url=record["source_url"], detail=detail))
        return
    seen_names[key] = record
    code = record["booth_code"]
    if not code:
        return
    holder = seen_booths.get(code)
    if holder is not None and normalize.normalize_name(holder["name"]) != key:
        issues.append(issue(
            "Q_DUP_BOOTH", subject=code, field_name="booth_raw",
            raw_value=f"{holder['name']} / {record['name']}",
            row_ref=record["row_ref"], source_url=record["source_url"],
            detail="同一展位号被两家展商占用，疑似合展"))
        return
    seen_booths[code] = record


def ex_id(name: str, marker: str) -> str:
    """展商编号：名称 + 展位的摘要，重跑不变。"""
    digest = hashlib.sha1(f"{normalize.normalize_name(name)}|{marker}".encode("utf-8"))
    return "E" + digest.hexdigest()[:9]


def summary(exhibitors: list[dict], issues: list[dict]) -> dict:
    counts = {}
    for record in exhibitors:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    return {
        "records": len(exhibitors),
        "ok": counts.get(STATUS_OK, 0),
        "unplaced": counts.get(STATUS_UNPLACED, 0),
        "isolated": counts.get(STATUS_ISOLATED, 0),
        "dup": counts.get(STATUS_DUP, 0),
        "issues": len(issues),
        "halls": len({r["hall"] for r in exhibitors if r["hall"]}),
    }


def rows_for_page(exhibitors: list[dict]) -> list[dict]:
    rows = []
    for record in exhibitors:
        rows.append({
            "ex_id": record["ex_id"], "name": record["name"],
            "hall": record["hall"], "booth": record["booth_code"] or record["booth_raw"],
            "row_no": record["row_no"] or "—", "col_no": record["col_no"] or "—",
            "x_m": record["x_m"], "y_m": record["y_m"],
            "area": record["area_sqm"], "priority": record["priority"],
            "owner": record["owner"], "status": record["status"],
            "status_label": STATUS_LABELS.get(record["status"], record["status"]),
            "code": record["code"] or "—", "note": record["note"],
            "row_ref": record["row_ref"],
        })
    return rows


def decimal_or_none(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
