"""编码探测与回退链。

顺序固定：声明编码（先按别名归一）→ 回退链 utf-8-sig / utf-8 / gb18030。
声明与实际不符时仍然解出来，但要留一条 S_CHARSET_FALLBACK 记录；
全部失败才隔离，并带上页面地址，避免"乱码列名"往下走。
"""
from __future__ import annotations

import re

from ... import config, reasons

CHARSET_RE = re.compile(
    r"""charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE)
BOM_UTF8 = b"\xef\xbb\xbf"


def declare(raw: bytes) -> str | None:
    """从 meta / xml 声明里取编码名，取不到返回 None。"""
    head = raw[:2048].decode("latin-1", errors="replace")
    match = CHARSET_RE.search(head)
    if not match:
        return None
    return canonical(match.group(1))


def canonical(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().lower()
    return config.CHARSET_ALIASES.get(key, key)


def has_bom(raw: bytes) -> bool:
    return raw.startswith(BOM_UTF8)


def strip_bom(text: str) -> str:
    return text.lstrip("\ufeff")


def decode(raw: bytes, declared: str | None = None) -> dict:
    """返回 {text, encoding, codes, mismatched}。

    codes 是本次解码需要登记的原因码；mismatched 表示声明与实际不一致。
    """
    declared = canonical(declared)
    codes: list[str] = []
    order: list[str] = []
    if declared:
        order.append(declared)
    for name in config.ENCODING_CHAIN:
        if name not in order:
            order.append(name)
    if has_bom(raw) and "utf-8-sig" not in order:
        order.insert(0, "utf-8-sig")

    for name in order:
        try:
            text = raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
        if text.startswith("\ufeff"):
            text = strip_bom(text)
            codes.append("S_BOM_STRIPPED")
        if declared and name != declared:
            codes.append("S_CHARSET_FALLBACK")
        return {
            "text": text,
            "encoding": name,
            "codes": _dedupe(codes),
            "mismatched": bool(declared and name != declared),
        }
    text = raw.decode("utf-8", errors="replace")
    return {
        "text": text,
        "encoding": "utf-8/replace",
        "codes": ["S_DECODE_FAILED"],
        "mismatched": True,
    }


def reason_rows(codes: list[str], *, url: str) -> list[dict]:
    rows = []
    for code in codes:
        reason = reasons.get(code)
        rows.append({
            "code": code, "stage": reason.stage, "level": reason.level,
            "subject": url, "detail": reason.title,
        })
    return rows


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
