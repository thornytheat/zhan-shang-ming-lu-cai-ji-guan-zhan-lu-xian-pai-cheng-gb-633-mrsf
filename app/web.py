"""模板渲染与共享视图上下文：过滤器、导航、当前批次选择。

页面只读 store，不做业务计算；格式统一在这里，避免每页各写一套。
L5 表格网格的列字母也在这里生成，模板只负责排布。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config, reasons

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

NAV = (
    ("/", "名录采信", "catalog"),
    ("/exhibitors", "展商网格", "booth"),
    ("/conflicts", "冲突隔离", "windows"),
    ("/plan", "路线排程", "route"),
    ("/batches", "批次版本", "batches"),
    ("/export", "行程单", "export"),
    ("/alternate", "备选方案", "alternate"),
)
STATUS_LABELS = {
    "done": "完成", "failed": "失败", "running": "运行中",
    "ok": "通过", "warn": "提示", "error": "隔离",
    "SCHEDULED": "排入", "RESCHEDULED": "改期",
    "CONFLICT": "冲突", "SKIPPED": "跳过",
}
STOP_LABELS = config.CLASSIFY_LABELS


@dataclass
class Ctx:
    settings: config.Settings
    store: object

    def current_batch(self, batch_id: str | None = None) -> dict | None:
        if batch_id:
            return self.store.get_batch(batch_id)
        return self.store.latest_batch()


def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def setup(app) -> None:
    t = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env = t.env
    env.filters["meter"] = fmt_meter
    env.filters["sqm"] = fmt_sqm
    env.filters["clock"] = fmt_clock
    env.filters["span"] = fmt_span
    env.filters["status"] = lambda s: STATUS_LABELS.get(s, s or "—")
    env.filters["level"] = lambda s: reasons.LEVEL_LABELS.get(s, s)
    env.filters["colletter"] = col_letter
    env.filters["short"] = lambda s, n=22: shorten(s, n)
    env.globals.update(
        nav=NAV,
        stage_flow=config.STAGE_FLOW,
        stage_labels=config.STAGE_LABELS,
        stage_keys=config.STAGES,
        reason_catalog=reasons.catalog(),
        reasons=reasons.REASONS,
        halls=config.HALLS,
        hall_order=config.HALL_ORDER,
        show_days=config.SHOW_DAYS,
        classify_order=config.CLASSIFY_ORDER,
        classify_labels=config.CLASSIFY_LABELS,
        dist_unit=config.DIST_UNIT,
        rule_version=config.RULE_VERSION,
        generated_at=now_local().strftime("%Y-%m-%d %H:%M:%S"),
        timezone_label="Asia/Shanghai (+08:00)",
    )
    app.state.templates = t


def render(request: Request, name: str, **context):
    ctx: Ctx = request.app.state.ctx
    batch = context.pop("batch", None)
    if batch is None:
        batch = ctx.current_batch(context.get("batch_id"))
    stage_rows = context.pop("stages", None)
    if stage_rows is None and batch is not None:
        stage_rows = ctx.store.load_stages(batch["batch_id"])
    base = {
        "request": request,
        "stages": stage_rows or [],
        "ctx": ctx,
        "settings": ctx.settings,
        "batch": batch,
        "rule_version": ctx.settings.rule_version,
        "eval_date": ctx.settings.eval_date.isoformat(),
    }
    base.update(context)
    return request.app.state.templates.TemplateResponse(
        request=request, name=name, context=base)


def now_local() -> dt.datetime:
    return dt.datetime.now(config.CHINA_TZ)


def to_local(value) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.UTC)
    return value.astimezone(config.CHINA_TZ)


def col_letter(index: int) -> str:
    """0 → A，25 → Z，26 → AA；表格网格的列字母。"""
    index = int(index)
    out = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def fmt_meter(value) -> str:
    if value is None or value == "":
        return "—"
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return f"{value:.1f}"


def fmt_sqm(value) -> str:
    text = fmt_meter(value)
    return text if text == "—" else f"{text}"


def fmt_clock(value) -> str:
    local = to_local(value)
    return local.strftime("%H:%M") if local else "—"


def fmt_span(start, end) -> str:
    left, right = fmt_clock(start), fmt_clock(end)
    if left == "—" or right == "—":
        return "—"
    return f"{left}–{right}"


def shorten(text: str | None, limit: int = 22) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
