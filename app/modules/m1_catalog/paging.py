"""翻页与页级诊断：把来源种子展开成页面清单。

三个约束：
1. 只按 rel=next 翻页，页脚说"还有下一页"但缺链接时记录断点，不猜页号（S_PAGING_NO_NEXT）；
2. 单来源页数上限 MAX_PAGES_PER_SOURCE，到达上限记 S_PAGING_LIMIT；
3. 表格未闭合的页面照常保留已闭合行，状态标 truncated 并记 S_PAGE_TRUNCATED（TRAP-16）。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from ... import config, reasons
from . import decode, reader

FOOTER_NEXT_HINTS = ("下一页", "下页", "next page", "&raquo;")


@dataclass
class Bundle:
    pages: list[reader.Fetched] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    sources: tuple = ()
    segments: int = 0

    def by_kind(self, *kinds: str) -> list[reader.Fetched]:
        return [p for p in self.pages if p.kind in kinds]

    @property
    def input_digest_parts(self) -> list[str]:
        return [f"{p.url}|{p.sha256}" for p in self.pages if p.sha256]


def issue(code: str, *, subject: str = "", detail: str | None = None,
          field_name: str | None = None, raw_value: str | None = None,
          row_ref: str | None = None) -> dict:
    reason = reasons.get(code)
    return {
        "code": code, "stage": reason.stage, "level": reason.level,
        "subject": subject, "field": field_name, "raw_value": raw_value,
        "detail": detail or reason.title, "source_url": subject, "row_ref": row_ref,
    }


def normalize_path(href: str, current: str) -> str:
    """相对链接补全成站内绝对路径；目录页链接都相对于当前页。"""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return href
    base = current.rsplit("/", 1)[0]
    while href.startswith("../"):
        href = href[3:]
        base = base.rsplit("/", 1)[0] if "/" in base else ""
    return f"{base}/{href}".replace("//", "/")


def fetch_page(fetcher, path: str, *, source: str, kind: str, page_no: int) -> tuple:
    fetched = fetcher.get(path)
    fetched.source = source
    fetched.kind = kind
    fetched.page_no = page_no
    codes: list[str] = []
    if fetched.status != "ok":
        fetched.note = f"HTTP {fetched.http_status}"
        fetched.codes = ["S_PAGE_MISSING"]
        return fetched, fetched.codes

    declared = decode.declare(fetched.raw)
    fetched.declared_charset = declared
    info = decode.decode(fetched.raw, declared)
    fetched.text = info["text"]
    fetched.encoding = info["encoding"]
    codes.extend(info["codes"])
    if "S_DECODE_FAILED" in codes:
        fetched.status = "error"
        fetched.codes = _dedupe(codes)
        return fetched, fetched.codes

    if kind == "attachment":
        fetched.header, fetched.rows, truncated = parse_csv(fetched.text)
        fetched.table_closed = not truncated
        if truncated:
            codes.append("S_PAGE_TRUNCATED")
        if decode.has_bom(fetched.raw) or (
                fetched.header and fetched.header[0].startswith("\ufeff")):
            codes.append("S_BOM_STRIPPED")
        fetched.codes = _dedupe(codes)
        return fetched, fetched.codes

    reader.parse_html(fetched)
    if not fetched.rows and not fetched.header:
        codes.append("S_NO_TABLE")
    if not fetched.table_closed:
        fetched.status = "truncated"
        codes.append("S_PAGE_TRUNCATED")
    if fetched.dup_headers:
        codes.append("S_DUP_HEADER")
    fetched.codes = _dedupe(codes)
    return fetched, fetched.codes


def parse_csv(text: str) -> tuple[list[str], list[list[str]], bool]:
    """按 csv 模块流式解析，引号内换行算同一行（TRAP-03 同族处理）。

    返回 (header, rows, truncated)。末行字段数少于表头即视为截断。
    """
    stream = io.StringIO(text, newline="")
    parsed = list(csv.reader(stream))
    if not parsed:
        return [], [], False
    header = [cell.strip() for cell in parsed[0]]
    rows: list[list[str]] = []
    truncated = False
    body = parsed[1:]
    for index, row in enumerate(body):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) < len(header) and index == len(body) - 1 and not text.endswith("\n"):
            truncated = True
        rows.append([cell.strip() for cell in row])
    return header, rows, truncated


def crawl_source(fetcher, source: dict, settings) -> tuple[list, list, int]:
    pages: list = []
    issues: list = []
    segments = 0
    if source.get("attachment"):
        page, codes = fetch_page(fetcher, source["attachment"], source=source["id"],
                                 kind="attachment", page_no=1)
        pages.append(page)
        issues.extend(_codes_to_issues(page, codes))
        return pages, issues, segments

    kind = "agenda" if source["id"] == "forum" else "list"
    seen: set[str] = set()
    path = source["seeds"][0]
    page_no = 1
    while path and page_no <= config.MAX_PAGES_PER_SOURCE:
        if path in seen:
            issues.append(issue("S_PAGING_LIMIT", subject=path,
                                detail="next 指回已访问页面，停止翻页"))
            break
        seen.add(path)
        page, codes = fetch_page(fetcher, path, source=source["id"], kind=kind,
                                 page_no=page_no)
        pages.append(page)
        issues.extend(_codes_to_issues(page, codes))
        if page.status in ("missing", "error"):
            # 表格截断仍可继续翻页；取不到页才停
            break
        segments += page.dup_headers
        hints_next = any(hint in page.text for hint in FOOTER_NEXT_HINTS)
        if page.next_link:
            path = normalize_path(page.next_link, path)
            page_no += 1
            continue
        if hints_next:
            issues.append(issue("S_PAGING_NO_NEXT", subject=page.url,
                                detail=f"第 {page.page_no} 页页脚还有下一页，缺 next 链接"))
        break
    else:
        if page_no > config.MAX_PAGES_PER_SOURCE:
            issues.append(issue("S_PAGING_LIMIT", subject=source["id"],
                                detail=f"达到单来源上限 {config.MAX_PAGES_PER_SOURCE} 页"))
    return pages, issues, segments


def crawl_all(fetcher, sources: tuple, settings) -> Bundle:
    bundle = Bundle(sources=tuple(s["id"] for s in sources))
    for source in sources:
        pages, issues, segments = crawl_source(fetcher, source, settings)
        bundle.pages.extend(pages)
        bundle.issues.extend(issues)
        bundle.segments += segments
    return bundle


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _codes_to_issues(page: reader.Fetched, codes: list[str]) -> list[dict]:
    rows = []
    for code in dict.fromkeys(codes):
        rows.append(issue(code, subject=page.url,
                          detail=f"第 {page.page_no} 页 · {reasons.label(code)}"))
    return rows
