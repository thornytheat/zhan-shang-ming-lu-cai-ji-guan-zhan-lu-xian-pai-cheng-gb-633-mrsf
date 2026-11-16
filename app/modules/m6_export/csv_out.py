"""CSV 导出：行程单、隔离证据、分段明细。

三条约定：
- 带 BOM 输出，Excel 打开不乱码；解析端会剥掉 BOM，回读一致；
- 金额/距离按字符串写，不经过 float；
- 证据表带行号与原因码，人工复核不必回源码目录。
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

ITINERARY_HEADER = ("日期", "序号", "时段", "类型", "编号", "名称", "展馆",
                    "停留(分)", "步行(米)", "状态", "原因码", "备注")
EVIDENCE_HEADER = ("原因码", "级别", "阶段", "对象", "字段", "原值", "说明",
                   "行号", "来源")
SEGMENT_HEADER = ("日期", "序号", "起点", "终点", "展馆", "距离(米)", "步行(分)")


def _writer() -> tuple[io.StringIO, object]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    return buffer, writer


def itinerary_csv(rows: list[dict]) -> str:
    buffer, writer = _writer()
    writer.writerow(ITINERARY_HEADER)
    for row in rows:
        writer.writerow((
            row["day_label"], row["seq"], row["slot"], row["kind"], row["ref"],
            row["name"], row["hall"], row["dwell"], _num(row["walk_m"]),
            row["status_label"], row["code"], row["note"],
        ))
    return "\ufeff" + buffer.getvalue()


def evidence_csv(issues: list[dict]) -> str:
    buffer, writer = _writer()
    writer.writerow(EVIDENCE_HEADER)
    for item in issues:
        writer.writerow((
            item["code"], item["level"], item["stage"], item.get("subject") or "",
            item.get("field") or "", item.get("raw_value") or "",
            item.get("detail") or "", item.get("row_ref") or "",
            item.get("source_url") or "",
        ))
    return "\ufeff" + buffer.getvalue()


def segment_csv(days: list[dict]) -> str:
    buffer, writer = _writer()
    writer.writerow(SEGMENT_HEADER)
    for day in days:
        for index, segment in enumerate(day.get("segments", []), 1):
            writer.writerow((day.get("day"), index, segment["from"],
                             segment["ref"], segment["hall"],
                             _num(segment["walk_m"]), segment["walk_min"]))
    return "\ufeff" + buffer.getvalue()


def unplaced_csv(rows: list[dict]) -> str:
    buffer, writer = _writer()
    writer.writerow(("日期", "编号", "名称", "类型", "展馆", "状态", "原因码", "备注"))
    for row in rows:
        writer.writerow((row["day"], row["ref"], row["name"], row["kind"],
                         row["hall"], row["status_label"], row["code"], row["note"]))
    return "\ufeff" + buffer.getvalue()


def _num(value) -> str:
    if isinstance(value, Decimal):
        return f"{value:.1f}"
    if value is None:
        return "0.0"
    return str(value)
