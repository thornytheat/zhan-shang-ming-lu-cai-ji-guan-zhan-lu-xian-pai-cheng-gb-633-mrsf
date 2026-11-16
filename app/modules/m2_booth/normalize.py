"""字段归一：全角转半角、千分位、单位后缀、空值哨兵、编码型编号保形。

一句话规则：**能算的归一成 Decimal，不能算的保留原字符串**。
展位号、邮编、工号这类带前导零的值永远按字符串处理（TRAP-08）。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ... import config

FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
FULLWIDTH[0x3000] = 0x20
SPACE_RE = re.compile(r"[\s\u00a0\u3000]+")
NUM_SUFFIX_RE = re.compile(
    r"(平方米|平方|平米|㎡|m2|m²|平方尺|个|位)$", re.IGNORECASE)
THOUSANDS_RE = re.compile(r"[,\u3001，\s']")


def to_halfwidth(text: str | None) -> str:
    if not text:
        return ""
    return text.translate(FULLWIDTH)


def clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return SPACE_RE.sub(" ", to_halfwidth(text)).strip()


def is_sentinel(value: str | None) -> bool:
    """空串、连字符、N/A 一类不是 0，也不是有效值。"""
    if value is None:
        return True
    return to_halfwidth(value).strip().lower() in config.SENTINELS


def normalize_name(value: str | None) -> str:
    """同名判定：去空白、全角转半角、括号统一、英文小写。"""
    text = clean_text(value)
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    return text.lower()


def parse_decimal(value: str | None, *, field: str = "数值") -> Decimal:
    """千分位、全角数字、单位后缀一起处理；失败抛 ValueError 带原值（TRAP-06）。"""
    raw = value
    if is_sentinel(value):
        raise ValueError(f"{field} 为空值哨兵：{raw!r}")
    text = THOUSANDS_RE.sub("", clean_text(value))
    text = NUM_SUFFIX_RE.sub("", text)
    text = text.replace("。", ".").replace("．", ".")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} 无法解析为数值：{raw!r}") from None


def parse_int(value: str | None, *, field: str = "整数") -> int:
    number = parse_decimal(value, field=field)
    if number != number.to_integral_value():
        raise ValueError(f"{field} 不是整数：{value!r}")
    return int(number)


def keep_leading_zero(value: str | None, width: int = 2) -> str:
    """编码型字段补零：已够宽就原样，短了就左侧补零，绝不转 int。"""
    text = clean_text(value)
    if not text:
        return ""
    if text.isdigit():
        return text.zfill(width)
    return text


def strip_unit(value: str | None) -> str:
    return NUM_SUFFIX_RE.sub("", clean_text(value))
