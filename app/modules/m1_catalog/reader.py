"""读取层：本地目录 / HTTP 两种取页方式，同一套解析。

只负责「拿到字节 + 抠出表格单元格」；编码、页状态、重复表头判定交给 decode 与 paging。
表格用逐字符扫描而不是整段正则在内存里拼：截断页也能保留已闭合的行（TRAP-16）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<t([dh])\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
TBL_OPEN_RE = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
TBL_CLOSE = re.compile(r"</table\s*>", re.IGNORECASE)
NEXT_RE = re.compile(
    r"""<a\b[^>]*\brel\s*=\s*["']next["'][^>]*\bhref\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE)
NEXT_RE_REV = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*\brel\s*=\s*["']next["']""",
    re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
ENTITY = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
    "&nbsp;": " ", "&times;": "×", "&mdash;": "—",
}


@dataclass
class Fetched:
    url: str
    status: str                     # ok | missing
    http_status: int
    raw: bytes
    sha256: str
    source: str = ""
    kind: str = "list"
    page_no: int = 1
    declared_charset: str | None = None
    text: str = ""
    encoding: str = ""
    rows: list[list[str]] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    table_closed: bool = True
    dup_headers: int = 0
    next_link: str | None = None
    note: str = ""
    codes: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return len(self.raw)

    @property
    def row_count(self) -> int:
        return len(self.rows)


class FileFetcher:
    """容器外与测试用：直接读 sample_site 目录，路径当 URL 用。"""

    kind = "file"

    def __init__(self, root: str | Path, base: str = "http://sample-site"):
        self.root = Path(root)
        self.base = base.rstrip("/")

    def get(self, path: str) -> Fetched:
        url = self.base + "/" + path.lstrip("/")
        rel = path.lstrip("/")
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root.resolve())) or not target.is_file():
            return Fetched(url=url, status="missing", http_status=404, raw=b"", sha256="")
        raw = target.read_bytes()
        return Fetched(url=url, status="ok", http_status=200, raw=raw,
                       sha256=hashlib.sha256(raw).hexdigest())


class HttpFetcher:
    """容器内：走 http://sample-site/，与文件读取结果一致。"""

    kind = "http"

    def __init__(self, base: str, timeout: float = 5.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def get(self, path: str) -> Fetched:
        import httpx

        url = self.base + "/" + path.lstrip("/")
        try:
            response = httpx.get(url, timeout=self.timeout)
        except Exception as exc:                       # noqa: BLE001
            return Fetched(url=url, status="missing", http_status=0, raw=b"",
                           sha256="", note=f"请求失败：{type(exc).__name__}")
        raw = response.content
        status = "ok" if response.status_code == 200 else "missing"
        return Fetched(url=url, status=status, http_status=response.status_code,
                       raw=raw, sha256=hashlib.sha256(raw).hexdigest())


def clean_cell(cell: str) -> str:
    cell = BR_RE.sub(" ", cell)
    cell = TAG_RE.sub("", cell)
    for entity, char in ENTITY.items():
        cell = cell.replace(entity, char)
    return re.sub(r"\s+", " ", cell).strip()


def split_tables(text: str) -> list[tuple[str, bool]]:
    """按 <table> … </table> 切段，返回 (段内文本, 是否闭合)。"""
    blocks: list[tuple[str, bool]] = []
    pos = 0
    for open_match in TBL_OPEN_RE.finditer(text):
        if open_match.start() < pos:
            continue
        close_match = TBL_CLOSE.search(text, open_match.end())
        if close_match is None:
            blocks.append((text[open_match.end():], False))
            pos = len(text)
            continue
        blocks.append((text[open_match.end():close_match.start()], True))
        pos = close_match.end()
    return blocks


def table_rows(block: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_match in ROW_RE.finditer(block):
        cells = [clean_cell(m.group(2)) for m in CELL_RE.finditer(row_match.group(1))]
        if cells:
            rows.append(cells)
    return rows


def parse_html(fetched: Fetched) -> Fetched:
    """把已解码文本拆成 header + rows，并标记截断与重复表头。"""
    blocks = split_tables(fetched.text)
    fetched.table_closed = all(closed for _, closed in blocks) if blocks else True
    if not blocks:
        fetched.rows = [[clean_cell(line)] for line in fetched.text.splitlines()
                        if line.strip()][:0]
        fetched.note = "页面不含 table"
    else:
        # 取第一张含表头的表格；名录页只有一张数据表
        merged: list[list[str]] = []
        for block, _closed in blocks:
            merged.extend(table_rows(block))
        if merged:
            fetched.header = merged[0]
            body = merged[1:]
            header_key = [_norm(h) for h in fetched.header]
            kept: list[list[str]] = []
            for row in body:
                if [_norm(c) for c in row] == header_key:
                    fetched.dup_headers += 1
                    continue
                kept.append(row)
            fetched.rows = kept
    match = NEXT_RE.search(fetched.text) or NEXT_RE_REV.search(fetched.text)
    fetched.next_link = match.group(1) if match else None
    return fetched


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()
