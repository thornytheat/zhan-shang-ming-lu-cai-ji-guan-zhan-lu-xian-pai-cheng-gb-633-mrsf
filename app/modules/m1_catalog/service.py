"""名录采信服务：选来源、造 fetcher、汇总页级诊断。

对外只暴露三个函数，编排交给 M5 批次服务；本模块不解析展位号，也不判时间窗。
"""
from __future__ import annotations

from ... import config, errors, reasons
from . import paging, reader

SOURCE_LABELS = config.SOURCE_ALIASES


def make_fetcher(settings, *, offline: bool = True):
    """容器内走 http://sample-site/；本地与测试读 sample_site 目录，同一套解析。"""
    if offline:
        return reader.FileFetcher(settings.sample_site_dir, base=settings.sample_site_base)
    return reader.HttpFetcher(settings.sample_site_base, timeout=settings.http_timeout)


def select_sources(source: str | None) -> tuple[dict, ...]:
    if source in (None, "", "all"):
        return config.SOURCES
    selected = tuple(s for s in config.SOURCES if s["id"] == source)
    if not selected:
        raise errors.BadRequest(
            f"未知来源 {source!r}；可选 all / "
            + " / ".join(s["id"] for s in config.SOURCES),
            code="E_BAD_SOURCE")
    return selected


def ingest(settings, *, source: str = "all", fetcher=None) -> paging.Bundle:
    sources = select_sources(source)
    fetcher = fetcher or make_fetcher(settings)
    return paging.crawl_all(fetcher, sources, settings)


def page_rows(bundle: paging.Bundle) -> list[dict]:
    """落库用的页面行；状态只有 ok / truncated / missing / error 四种。"""
    rows = []
    for page in bundle.pages:
        code = None
        for item in bundle.issues:
            if item.get("source_url") == page.url and item["level"] == reasons.ERROR:
                code = item["code"]
                break
        rows.append({
            "url": page.url, "source": page.source, "kind": page.kind,
            "page_no": page.page_no, "status": page.status,
            "declared_charset": page.declared_charset, "encoding": page.encoding,
            "bytes": page.bytes, "sha256": page.sha256,
            "row_count": page.row_count, "code": code,
            "note": page.note,
        })
    return rows


def diagnosis_rows(bundle: paging.Bundle) -> list[dict]:
    """页面诊断表：一页一行，带编码、字节、行数与原因码。"""
    rows = []
    for page in bundle.pages:
        rows.append({
            "url": page.url, "source": SOURCE_LABELS.get(page.source, page.source),
            "kind": page.kind, "page_no": page.page_no, "status": page.status,
            "declared_charset": page.declared_charset or "—",
            "encoding": page.encoding or "—",
            "bytes": page.bytes, "sha256": (page.sha256 or "")[:12],
            "rows": page.row_count, "dup_headers": page.dup_headers,
            "code": " ".join(page.codes) or "—",
        })
    for item in bundle.issues:
        rows.append({
            "url": item.get("source_url") or "—", "source": "分页",
            "kind": "paging", "page_no": 0, "status": item["level"],
            "declared_charset": "—", "encoding": "—", "bytes": 0, "sha256": "",
            "rows": 0, "dup_headers": 0, "code": item["code"],
        })
    return rows


def log_lines(bundle: paging.Bundle) -> list[str]:
    lines = []
    for page in sorted(bundle.pages, key=lambda p: (p.source, p.page_no)):
        lines.append(
            f"{page.source}/p{page.page_no} {page.status:<9} "
            f"{page.encoding or '-':<8} {page.bytes:>6}B rows={page.row_count}")
    for item in bundle.issues:
        lines.append(f"! {item['code']} {item.get('source_url') or ''}")
    return lines


def summary(bundle: paging.Bundle) -> dict:
    return {
        "pages": len(bundle.pages),
        "rows": sum(p.row_count for p in bundle.pages),
        "segments": bundle.segments,
        "issues": len(bundle.issues),
    }
