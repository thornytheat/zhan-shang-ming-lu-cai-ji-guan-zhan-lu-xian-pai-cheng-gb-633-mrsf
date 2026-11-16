"""M2 入口：列契约与展位语法表，以及展商网格页。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import config, errors, reasons
from ...web import Ctx, get_ctx, render
from . import contract, parse, service

router = APIRouter(tags=["M2 展位解析"])


@router.get("/api/contract")
def api_contract(ctx: Ctx = Depends(get_ctx)) -> dict:
    return {
        "rule_version": ctx.settings.rule_version,
        "columns": contract.column_contract(),
        "required": list(contract.REQUIRED),
        "evolution": contract.evolution_table(),
        "grammar": parse.grammar_payload(),
        "halls": [
            {"id": key, "name": hall["name"], "zone": hall["zone"],
             "x": str(hall["x"]), "y": str(hall["y"])}
            for key, hall in config.HALLS.items()
        ],
        "geometry": {
            "col_pitch_m": str(config.COL_PITCH_M),
            "row_pitch_m": str(config.ROW_PITCH_M),
            "cross_hall_penalty_m": str(config.CROSS_HALL_PENALTY_M),
            "walk_m_per_min": str(config.WALK_M_PER_MIN),
            "distance_unit": config.DIST_UNIT,
            "decimals": "0.1",
        },
        "reasons": reasons.catalog(),
    }


@router.get("/exhibitors")
def page_exhibitors(request: Request, batch_id: str | None = None,
                    hall: str = "", status: str = "",
                    ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    records: list[dict] = []
    issues: list[dict] = []
    summary = {"records": 0, "ok": 0, "unplaced": 0, "dup": 0}
    if batch is not None:
        bid = batch["batch_id"]
        records = ctx.store.load_exhibitors(bid)
        issues = [i for i in ctx.store.load_issues(bid) if i["stage"] == "booth"]
        summary = service.summary(records, issues)
    rows = service.rows_for_page(records)
    if hall:
        rows = [r for r in rows if r["hall"] == hall]
    if status:
        rows = [r for r in rows if r["status"] == status]
    return render(request, "exhibitors.html", batch=batch, page="booth",
                  rows=rows, summary=summary, issues=issues, hall=hall, status=status,
                  status_options=[(k, v) for k, v in service.STATUS_LABELS.items()],
                  grammar=parse.grammar_payload(),
                  grid_cols=["馆", "展商", "展位号", "排", "位", "x(m)", "y(m)",
                              "面积(㎡)", "优先级", "对接人", "状态"],
                  page_title="展商网格")


@router.get("/api/exhibitors")
def api_exhibitors(batch_id: str | None = None, hall: str = "",
                   ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        raise errors.NotFound("还没有完成的批次", code="E_NO_BATCH")
    records = ctx.store.load_exhibitors(batch["batch_id"])
    if hall:
        records = [r for r in records if r["hall"] == hall]
    return {"batch_id": batch["batch_id"], "count": len(records),
            "rows": service.rows_for_page(records)}
