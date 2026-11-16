"""测试固定装置：临时库、固定评估日、离线样例站目录。

约定：
- 期望值全部来自 tests/fixtures/expected.json，不用 random，不读 datetime.now()；
- 评估日固定 2026-11-15；样例站读 sample_site/ 目录，和容器内同一套解析；
- pipeline 夹具每个模块跑一次完整流水线，避免 60 多条用例重复跑批。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config                                    # noqa: E402
from app.main import create_app                           # noqa: E402
from app.store import Store                               # noqa: E402
from app.modules.m1_catalog import service as catalog_service          # noqa: E402
from app.modules.m5_batches import service as batch_service            # noqa: E402

EVAL_DATE = dt.date(2026, 11, 15)


@pytest.fixture(scope="session")
def expected() -> dict:
    path = ROOT / "tests" / "fixtures" / "expected.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def settings(tmp_path) -> config.Settings:
    return config.Settings(db_path=str(tmp_path / "boothplan.sqlite"),
                           eval_date=EVAL_DATE,
                           sample_site_dir=str(ROOT / "sample_site"),
                           sample_site_base="http://sample-site")


@pytest.fixture()
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture()
def fetcher(settings):
    return catalog_service.make_fetcher(settings)


@pytest.fixture()
def bundle(settings, fetcher):
    return catalog_service.ingest(settings, source="all", fetcher=fetcher)


@pytest.fixture(scope="session")
def assembled(tmp_path_factory):
    """整条流水线跑一次：采信 → 展位 → 时间窗 → 排程。"""
    from app.modules.m2_booth import service as booth_service
    from app.modules.m3_windows import service as window_service
    from app.modules.m4_route import service as route_service

    base = tmp_path_factory.mktemp("pipeline")
    settings = config.Settings(db_path=str(base / "boothplan.sqlite"),
                               eval_date=EVAL_DATE,
                               sample_site_dir=str(ROOT / "sample_site"),
                               sample_site_base="http://sample-site")
    store = Store(settings.db_path)
    bundle = catalog_service.ingest(settings, source="all",
                                    fetcher=catalog_service.make_fetcher(settings))
    exhibitors, booth_issues = booth_service.build(bundle.pages)
    sessions, conflicts, window_issues = window_service.build(bundle.pages)
    plan = route_service.run(exhibitors, sessions)
    return {
        "settings": settings,
        "store": store,
        "bundle": bundle,
        "exhibitors": exhibitors,
        "booth_issues": booth_issues,
        "sessions": sessions,
        "conflicts": conflicts,
        "window_issues": window_issues,
        "plan": plan,
    }


@pytest.fixture(scope="session")
def finished(tmp_path_factory):
    """跑完一次真实批次并落库，供批次、导出、端到端用例共用。"""
    base = tmp_path_factory.mktemp("finished")
    settings = config.Settings(db_path=str(base / "boothplan.sqlite"),
                               eval_date=EVAL_DATE,
                               sample_site_dir=str(ROOT / "sample_site"),
                               sample_site_base="http://sample-site")
    store = Store(settings.db_path)
    result = batch_service.run(settings=settings, store=store, source="all")
    return {"settings": settings, "store": store, "result": result,
            "batch": result["batch"]}


@pytest.fixture()
def client(finished) -> TestClient:
    return TestClient(create_app(finished["settings"]))


@pytest.fixture()
def empty_client(settings) -> TestClient:
    return TestClient(create_app(settings))


def stop_of(plan: dict, ref: str) -> dict:
    for row in plan["stops"]:
        if row["ref"] == ref:
            return row
    raise AssertionError(f"排程结果里没有 {ref}")


def issue_codes(issues: list[dict]) -> list[str]:
    return [item["code"] for item in issues]
