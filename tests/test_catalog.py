"""M1 采信：编码回退、BOM、重复表头、截断页、翻页断点。"""
from __future__ import annotations


def dir_pages(bundle):
    return [page for page in bundle.pages if page.source == "dir"]


def test_page_count_matches_expected(bundle, expected):
    assert len(dir_pages(bundle)) == expected["dir_pages"]


def test_gb18030_page_decoded(bundle):
    # TRAP-01：声明 utf-8 实际 GB18030，回退链解出来并记一条 reason
    page = dir_pages(bundle)[1]
    assert page.encoding == "gb18030"
    assert "S_CHARSET_FALLBACK" in page.codes
    assert "罗克韦尔自动化" in page.text


def test_bom_on_first_header_stripped(bundle):
    # TRAP-02：导出件首列表头带 U+FEFF，剥离后才能对上列契约
    from app.modules.m2_booth import contract

    export = [page for page in bundle.pages if page.kind == "attachment"][0]
    assert export.encoding == "utf-8-sig"
    assert export.header[0] == "展商名称"
    assert "S_BOM_STRIPPED" in export.codes
    # 表头单元格若仍带 U+FEFF，列名归一必须先剥掉，否则整列对不上契约
    assert contract.canonical_key("\ufeff展商名称") == "exhibitor_name"
    assert contract.map_header(["\ufeff展商名称", "展位号"])["bom_removed"] is True


def test_duplicate_header_removed_and_counted(bundle):
    # TRAP-04：分段导出重复表头不算数据行，但要计分段数
    page = dir_pages(bundle)[2]
    assert page.dup_headers == 1
    assert page.row_count == 9
    assert bundle.segments == 1


def test_truncated_table_keeps_closed_rows(bundle):
    # TRAP-16：表格未闭合时保留已闭合行，状态标 truncated
    page = dir_pages(bundle)[3]
    assert page.table_closed is False
    assert page.status == "truncated"
    assert page.row_count == 6
    assert "S_PAGE_TRUNCATED" in page.codes


def test_truncated_csv_last_row_flagged(bundle):
    # TRAP-16：CSV 末行字段数不足且无换行，按截断标记
    export = [page for page in bundle.pages if page.kind == "attachment"][0]
    assert export.table_closed is False
    assert "S_PAGE_TRUNCATED" in export.codes


def test_paging_stops_when_next_link_missing(bundle):
    # 末页页脚写了「下一页」但没有链接：记断点，不按页号猜
    codes = [item["code"] for item in bundle.issues]
    assert "S_PAGING_NO_NEXT" in codes
    hint = [item for item in bundle.issues if item["code"] == "S_PAGING_NO_NEXT"][0]
    assert hint["subject"].endswith("/dir/list_p5.html")


def test_missing_page_reported(settings):
    from app import config
    from app.modules.m1_catalog import paging, service

    fetcher = service.make_fetcher(settings)
    source = {"id": "dir", "seeds": ("/dir/list_p9.html",)}
    pages, issues, _segments = paging.crawl_source(fetcher, source, settings)
    assert pages[0].status == "missing"
    assert issues[0]["code"] == "S_PAGE_MISSING"
    assert config.ENCODING_CHAIN[0] == "utf-8-sig"


def test_agenda_page_marked_as_agenda(bundle):
    kinds = {page.kind for page in bundle.pages}
    assert "agenda" in kinds
    agenda = [page for page in bundle.pages if page.kind == "agenda"][0]
    assert agenda.row_count == 24


def test_input_digest_parts_stable(settings, fetcher):
    """同一目录读两次，输入摘要逐字相同（幂等的前提）。"""
    from app.modules.m1_catalog import service

    first = service.ingest(settings, fetcher=fetcher)
    second = service.ingest(settings, fetcher=fetcher)
    assert first.input_digest_parts == second.input_digest_parts
    assert len(first.input_digest_parts) == len(first.pages)
