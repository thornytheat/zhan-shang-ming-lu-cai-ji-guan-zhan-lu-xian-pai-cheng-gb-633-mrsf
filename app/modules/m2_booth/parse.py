"""展位号语法：把门户上的多种写法解析成 馆-排-位。

支持的写法（大小写与全角先归一）：
    A1-3-05   馆-排-位，排/位补两位
    B2-12     馆-位（单排馆，排固定 01）
    A-01      馆-位，单字母馆按别名归到 1 号子馆
    C3        馆+位
    3x3 / 3×3 面积型展位，没有网格坐标
不支持的写法返回 Q_BOOTH_UNPARSABLE，不猜。
"""
from __future__ import annotations

import re

from ... import config
from .normalize import clean_text, to_halfwidth

SPLIT_RE = re.compile(r"[-–—/／\\|]+")
HALL_TOKEN_RE = re.compile(r"^([A-Z])(\d{1,2})?$")
DIGITS_RE = re.compile(r"^\d{1,3}$")


def parse_booth(raw: str | None) -> dict:
    """返回 {kind, hall, row, col, booth_code, code, value}。

    kind: grid（有坐标）/ area（面积型）/ none（解析不了）。
    code 是原因码，grid 与 area 也可能带提示码。
    """
    text = clean_text(raw)
    result = {"kind": "none", "hall": "", "row": "", "col": "",
              "booth_code": "", "code": None, "value": text}
    if not text or text.lower() in config.SENTINELS:
        result["code"] = "Q_BOOTH_EMPTY"
        return result

    flat = to_halfwidth(text).upper().replace(" ", "")
    if re.fullmatch(config.AREA_SPEC_RE, flat, re.IGNORECASE) or re.fullmatch(
            config.AREA_SPEC_RE + r"(双层|单层|光地)?", flat, re.IGNORECASE):
        result["kind"] = "area"
        result["code"] = "Q_BOOTH_AREA_ONLY"
        return result

    parts = [part for part in SPLIT_RE.split(flat) if part]
    if not parts:
        result["code"] = "Q_BOOTH_UNPARSABLE"
        return result

    hall = _hall_of(parts[0])
    if hall is None:
        # C3 这类「字母 + 位」：馆归到该字母的 1 号子馆，数字当位号
        token = parts[0]
        if len(parts) == 1 and token[:1].isalpha() and token[1:].isdigit():
            hall = _hall_of(token[:1])
            if hall is not None:
                result.update(kind="grid", hall=hall, row="01",
                              col=token[1:].zfill(2))
                result["booth_code"] = booth_code(hall, result["row"], result["col"])
                return result
        result["code"] = "Q_BOOTH_UNPARSABLE"
        return result
    rest = flat[len(parts[0]):].lstrip("-–—/／\\|")
    rest_parts = [part for part in SPLIT_RE.split(rest) if part] if rest else []

    if len(rest_parts) >= 2 and DIGITS_RE.match(rest_parts[0]) and DIGITS_RE.match(rest_parts[1]):
        row, col = rest_parts[0], rest_parts[1]
    elif len(rest_parts) == 1 and DIGITS_RE.match(rest_parts[0]):
        row, col = "1", rest_parts[0]
    elif len(rest_parts) == 0:
        # 馆号自带位号：A1 → 位 01；C3 → 馆 C1 位 03
        suffix = parts[0][1:]
        if not suffix:
            result["code"] = "Q_BOOTH_UNPARSABLE"
            return result
        row, col = "1", suffix
    else:
        result["code"] = "Q_BOOTH_UNPARSABLE"
        return result

    result.update(kind="grid", hall=hall, row=row.zfill(2), col=col.zfill(2))
    result["booth_code"] = booth_code(hall, result["row"], result["col"])
    return result


def booth_code(hall: str, row: str, col: str) -> str:
    return f"{hall}-{row}-{col}"


def _hall_of(token: str) -> str | None:
    key = token.lower()
    if key in config.HALL_ALIASES:
        return config.HALL_ALIASES[key]
    return None


def hall_of_raw(value: str | None) -> str | None:
    """展馆列单独处理：允许「A1」「1 号馆 A」「A 馆」几种写法。"""
    text = clean_text(value).upper()
    if not text:
        return None
    match = re.search(r"([A-D])\s*(\d)?", text)
    if match:
        token = match.group(1) + (match.group(2) or "")
        return _hall_of(token)
    match = re.search(r"([1-8])\s*号", text)
    if match:
        index = int(match.group(1)) - 1
        return config.HALL_ORDER[index] if 0 <= index < len(config.HALL_ORDER) else None
    return None


def grammar_payload() -> list[dict]:
    """给 /api/contract 的语法表，样例与结论都是实际解析结果。"""
    samples = ("A1-3-05", "B2-12", "A-01", "C3", "3x3", "Ａ１", "", "E9-1-1")
    rows = []
    for sample in samples:
        parsed = parse_booth(sample)
        rows.append({
            "raw": sample,
            "kind": parsed["kind"],
            "booth_code": parsed["booth_code"] or "—",
            "hall": parsed["hall"] or "—",
            "row": parsed["row"] or "—",
            "col": parsed["col"] or "—",
            "code": parsed["code"] or "OK",
        })
    return rows
