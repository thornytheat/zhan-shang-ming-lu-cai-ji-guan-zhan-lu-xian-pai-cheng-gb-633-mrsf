"""撤回清除的共享定义：敏感字段、主体匹配、回执编号。

展商撤回对接人授权后，只清除授权范围内的个人信息（对接人姓名与备注原文），
展商名称、展位、来源地址、行号、原因码与数量一律保留，供旧批次审计。

这个文件不碰数据库，也不做页面排版：
- 主体（subject）如何表示、两条记录是否同一家；
- 同一撤回请求如何得到同一个回执号（续跑不重复生成回执）；
- 哪些字段算敏感字段、页面上怎么称呼它们。
"""
from __future__ import annotations

import hashlib
import re

from .modules.m2_booth import normalize as booth_normalize
from .modules.m2_booth import parse as booth_parse

# 撤回对接人授权后要永久清除的字段：对接人姓名、备注原文。
# 展商名称不在其中——它不是个人信息，路线与证据仍要靠它定位。
SENSITIVE_FIELDS = ("owner", "note")
FIELD_LABELS = {"owner": "对接人", "note": "备注"}
CLEARED_MARK = "已清除"

REASON_CODE = "R_CONSENT_WITHDRAWN"
# 回执编号前缀；同一撤回请求的键固定，编号就固定，中断续跑不会多变一张回执。
RECEIPT_PREFIX = "R"
_KEY_VERSION = "v1"


def clean(value) -> str:
    return booth_normalize.clean_text(value if isinstance(value, str) else "")


def normalize_name(value) -> str:
    """展商名的比较形态；与名录去重键使用同一套归一。"""
    return booth_normalize.normalize_name(value if isinstance(value, str) else "")


def normalize_booth(value) -> str:
    """展位号比较形态：能解析就比归一结果，解析不了就比去空白后的原写法。"""
    text = clean(value)
    if not text:
        return ""
    parsed = booth_parse.parse_booth(text)
    return parsed["booth_code"] or text.upper()


def build_subject(payload: dict) -> dict:
    """从请求体取撤回主体。

    支持三种写法，按需要组合：
    - ex_id：名录里稳定的展商编号；
    - name：展商名称（归一后比较）；
    - name + booth：同名展商分馆出现时用来定位到具体那一家。
    """
    ex_id = clean(payload.get("ex_id"))
    name = clean(payload.get("name") or payload.get("exhibitor_name"))
    booth = clean(payload.get("booth") or payload.get("booth_code")
                  or payload.get("booth_raw"))
    subject = {
        "ex_id": ex_id,
        "name": normalize_name(name) if name else "",
        "label": name,
        "booth": normalize_booth(booth) if booth else "",
    }
    if not subject["ex_id"] and not subject["name"]:
        raise ValueError("撤回清除必须给出 ex_id 或 name（可再带 booth 定位）")
    return subject


def build_fields(raw) -> list[str]:
    """敏感字段清单；默认对接人姓名与备注原文两项，不允许清别的列。"""
    if raw in (None, "", []):
        return list(SENSITIVE_FIELDS)
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,，\s]+", raw.strip()) if part]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("fields 必须是字段名数组")
    fields: list[str] = []
    unknown: list[str] = []
    for item in raw:
        key = str(item).strip()
        if key in SENSITIVE_FIELDS:
            if key not in fields:
                fields.append(key)
        else:
            unknown.append(key)
    if unknown:
        raise ValueError(
            f"未知字段 {', '.join(unknown)}；可清除字段只有 "
            + " / ".join(SENSITIVE_FIELDS))
    if not fields:
        raise ValueError("fields 不能为空")
    return fields


def withdrawal_key(subject: dict, fields: list[str], idempotency_key: str = "") -> str:
    """撤回请求的稳定键：同一主体、同一字段集合永远得到同一个键。

    调用方显式给了 idempotency_key 时以它为准，便于外部系统把重试串起来。
    """
    if idempotency_key:
        parts = [_KEY_VERSION, "idem=" + idempotency_key.strip()]
    else:
        parts = [_KEY_VERSION]
        if subject["ex_id"]:
            parts.append("ex=" + subject["ex_id"])
        if subject["name"]:
            parts.append("name=" + subject["name"])
        if subject["booth"]:
            parts.append("booth=" + subject["booth"])
        parts.append("fields=" + ",".join(sorted(fields)))
    return "|".join(parts)


def receipt_id_for(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return RECEIPT_PREFIX + digest[:12].upper()


def _booth_matches(record: dict, wanted: str) -> bool:
    if not wanted:
        return True
    if record.get("booth_code") == wanted:
        return True
    raw = record.get("booth_raw")
    if not raw:
        return False
    return normalize_booth(raw) == wanted or clean(raw).upper() == wanted


def record_matches(record: dict, subject: dict) -> bool:
    """一行名录是否属于撤回主体。

    给出的条件全部命中才算匹配（AND）：
    - 只给 ex_id：按展商编号；
    - 只给 name：按归一名称（同名分馆会一起命中，页面会提示）；
    - name + booth：名称与展位号同时对上，只清这一家。
    """
    if subject.get("ex_id") and record.get("ex_id") != subject["ex_id"]:
        return False
    if subject.get("name") and normalize_name(record.get("name")) != subject["name"]:
        return False
    if subject.get("booth") and not _booth_matches(record, subject["booth"]):
        return False
    return True


def fields_label(fields) -> str:
    return " + ".join(FIELD_LABELS.get(name, name) for name in fields)
