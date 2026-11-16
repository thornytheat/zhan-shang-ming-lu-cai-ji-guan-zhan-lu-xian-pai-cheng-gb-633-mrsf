"""原因码唯一来源：跨模块共享，页面 / 导出 / 测试都从这里取文案。

一个原因码 = 级别 + 所属阶段 + 中文结论 + 复核建议。级别只有三档：
info（可忽略的记录）、warn（需人工看一眼）、error（已隔离，不能进行程单）。
"""
from __future__ import annotations

from typing import NamedTuple

INFO = "info"
WARN = "warn"
ERROR = "error"
LEVEL_ORDER = {ERROR: 0, WARN: 1, INFO: 2}
LEVEL_LABELS = {ERROR: "隔离", WARN: "提示", INFO: "记录"}


class Reason(NamedTuple):
    code: str
    level: str
    stage: str
    title: str
    hint: str


def _r(code, level, stage, title, hint):
    return Reason(code, level, stage, title, hint)


REASONS = {
    # ---- M1 名录采信 ----
    "S_CHARSET_FALLBACK": _r(
        "S_CHARSET_FALLBACK", INFO, "catalog",
        "声明编码与实际字节不符，已按回退链解码", "记录最终编码，导出时按 UTF-8 写出"),
    "S_BOM_STRIPPED": _r(
        "S_BOM_STRIPPED", INFO, "catalog",
        "首列名带 BOM，已剥离", "BOM 只出现在首列，剥离后列名按契约匹配"),
    "S_DECODE_FAILED": _r(
        "S_DECODE_FAILED", ERROR, "catalog",
        "回退链内所有编码都无法解码", "取原始字节确认编码后补进回退链"),
    "S_PAGE_MISSING": _r(
        "S_PAGE_MISSING", ERROR, "catalog",
        "名录页取不到（404 或不可读）", "确认样例站目录结构与页号"),
    "S_PAGE_TRUNCATED": _r(
        "S_PAGE_TRUNCATED", ERROR, "catalog",
        "表格未闭合，末行按隔离处理", "该页声明条数与实际行数不符，需整页重抓"),
    "S_PAGING_NO_NEXT": _r(
        "S_PAGING_NO_NEXT", WARN, "catalog",
        "页脚声明还有下一页但缺 next 链接", "按已知页号上限向后补抓，记录断点"),
    "S_PAGING_LIMIT": _r(
        "S_PAGING_LIMIT", WARN, "catalog",
        "达到单来源翻页上限", "确认是否漏页，必要时提高上限再跑"),
    "S_DUP_HEADER": _r(
        "S_DUP_HEADER", INFO, "catalog",
        "分段导出重复表头，已剔除", "重复表头不计入数据行，分段数单独记账"),
    "S_NO_TABLE": _r(
        "S_NO_TABLE", INFO, "catalog",
        "页面不含名录表格，跳过", "首页与导航页属正常跳过"),
    # ---- M2 展位解析 ----
    "Q_COLUMN_COUNT": _r(
        "Q_COLUMN_COUNT", ERROR, "booth",
        "列数与表头不一致", "末尾多一个分隔符或行被截断，需回源核对"),
    "Q_MISSING_NAME": _r(
        "Q_MISSING_NAME", ERROR, "booth",
        "展商名称为空值哨兵", "名称是主键的一部分，不能猜"),
    "Q_BOOTH_EMPTY": _r(
        "Q_BOOTH_EMPTY", ERROR, "booth",
        "展位号为空值哨兵", "空展位不能排进路线"),
    "Q_BOOTH_UNPARSABLE": _r(
        "Q_BOOTH_UNPARSABLE", ERROR, "booth",
        "展位号无法解析成 馆-排-位", "检查全角字符、分隔符与馆号写法"),
    "Q_BOOTH_AREA_ONLY": _r(
        "Q_BOOTH_AREA_ONLY", WARN, "booth",
        "面积型展位（如 3x3）没有网格坐标", "保留在名录，排程时按无坐标跳过"),
    "Q_HALL_MISMATCH": _r(
        "Q_HALL_MISMATCH", ERROR, "booth",
        "展馆列与展位号里的馆号不一致", "以展位号为准并记原值，需回源确认"),
    "Q_HALL_UNKNOWN": _r(
        "Q_HALL_UNKNOWN", ERROR, "booth",
        "馆号不在 8 馆平面表内", "新增馆需先补平面坐标"),
    "Q_BAD_AREA": _r(
        "Q_BAD_AREA", ERROR, "booth",
        "展位面积无法归一为平方数", "检查千分位、全角数字与单位后缀"),
    "Q_DUP_EXHIBITOR": _r(
        "Q_DUP_EXHIBITOR", ERROR, "booth",
        "同名展商出现在不同馆，取值冲突", "两条都留证据，取首次出现为主，人工确认"),
    "Q_DUP_BOOTH": _r(
        "Q_DUP_BOOTH", WARN, "booth",
        "同一个展位号被两家展商占用", "疑似合展或录入串行，需回源"),
    # ---- M3 时间窗 ----
    "T_BAD_TIME": _r(
        "T_BAD_TIME", ERROR, "windows",
        "时间无法解析为时刻", "检查中文日期、缺月份与 24:00 写法"),
    "T_NAIVE_TIME": _r(
        "T_NAIVE_TIME", INFO, "windows",
        "时间缺偏移，按 +08:00 解释", "同列出现多种写法时先归一 UTC 再分桶"),
    "T_TZ_MIXED": _r(
        "T_TZ_MIXED", INFO, "windows",
        "同一页出现 Z 与 +08:00 两种写法", "归一后落同一时间桶，写入证据"),
    "T_WINDOW_INVERTED": _r(
        "T_WINDOW_INVERTED", ERROR, "windows",
        "结束时间不晚于开始时间", "跨零点需显式写次日，不能靠倒挂推断"),
    "T_OVERLAP": _r(
        "T_OVERLAP", ERROR, "windows",
        "两场论坛时间窗重叠（左闭右开）", "端点相接不算重叠，重叠 1 分钟即隔离"),
    "T_OUTSIDE_HOURS": _r(
        "T_OUTSIDE_HOURS", WARN, "windows",
        "时间窗超出展期开放时间", "跨零点场次与闭馆后场次在此登记"),
    "T_UNKNOWN_HALL": _r(
        "T_UNKNOWN_HALL", ERROR, "windows",
        "论坛场地不在 8 馆平面表内", "补馆号或改到已知馆"),
    # ---- M4 路线排程 ----
    "P_NO_POSITION": _r(
        "P_NO_POSITION", ERROR, "route",
        "候选没有网格坐标，无法排入路线", "面积型或解析失败的展位在此跳过"),
    "P_OUTSIDE_HOURS": _r(
        "P_OUTSIDE_HOURS", ERROR, "route",
        "候选时间窗不在展期开放时间内", "固定论坛窗越界时只能冲突，不能挪"),
    "P_OVERLAP_FIXED": _r(
        "P_OVERLAP_FIXED", ERROR, "route",
        "与同天固定论坛窗重叠", "固定窗优先，展商改期或跳开"),
    "P_NO_SLOT": _r(
        "P_NO_SLOT", WARN, "route",
        "当天已排满，任何时段都放不下", "降低候选数或提高单日上限"),
    "P_SHIFTED": _r(
        "P_SHIFTED", WARN, "route",
        "首选时段放不下，已改到当天其他时段", "首选时段是上午 10:00-12:00"),
    # ---- M5 批次 ----
    "B_ABORTED": _r(
        "B_ABORTED", ERROR, "route",
        "批次在阶段中断", "用续跑从断点继续，已采信页面不重抓"),
    "B_RESUME_NO_STATE": _r(
        "B_RESUME_NO_STATE", WARN, "route",
        "没有可用断点，按全新批次重跑", "首次失败过早时会遇到"),
    "B_IDEMPOTENT_REPLAY": _r(
        "B_IDEMPOTENT_REPLAY", INFO, "route",
        "输入摘要命中已完成批次，复用结果", "重跑不新增行，行程单不变"),
    "B_STALE_REJECTED": _r(
        "B_STALE_REJECTED", WARN, "route",
        "提交的批次版本早于当前版本，已拒绝", "版本号只能前进，陈旧提交不覆盖新结果"),
    # ---- M6 导出 ----
    "X_EMPTY_SELECTION": _r(
        "X_EMPTY_SELECTION", WARN, "export",
        "导出选择为空", "先跑一次采集与排程再导出"),
    "X_UNPLACED_ROWS": _r(
        "X_UNPLACED_ROWS", WARN, "export",
        "行程单未包含跳过的候选", "跳过明细在冲突页与证据 CSV 内"),
    # ---- M7 备选方案 ----
    "M7_ALT_LOSS": _r(
        "M7_ALT_LOSS", INFO, "alternate",
        "备选方案与主方案取舍不同，损失已逐项列出", "看对比表的权重差额，再决定换哪条路线"),
    "M7_HALL_CAP": _r(
        "M7_HALL_CAP", WARN, "alternate",
        "当天跨馆次数已达上限，时间放得下也不排", "提高上限，或改到其他日期"),
    "M7_ASYMMETRY": _r(
        "M7_ASYMMETRY", WARN, "alternate",
        "一对馆的往返距离不相等（单向通道），已按方向计算", "反向多出来的米数在核验表里"),
    "M7_TRANSFER": _r(
        "M7_TRANSFER", INFO, "alternate",
        "跨馆换乘时间已计入到站时刻", "换乘分钟与步行分钟分开记"),
    "M7_NO_UNPLACED": _r(
        "M7_NO_UNPLACED", INFO, "alternate",
        "主方案没有可争取的候选，备选与主方案一致", "候选全部排入或被前置规则隔离"),
}


def get(code: str) -> Reason:
    return REASONS.get(code, Reason(code, WARN, "unknown", code, "未登记的原因码"))


def level_of(code: str) -> str:
    return get(code).level


def label(code: str) -> str:
    return get(code).title


def stage_of(code: str) -> str:
    return get(code).stage


def catalog() -> list[dict]:
    """给 /api/contract 与页面用的原因码目录（按阶段、级别排序）。"""
    rows = sorted(REASONS.values(), key=lambda r: (r.stage, LEVEL_ORDER[r.level], r.code))
    return [
        {"code": r.code, "level": r.level, "stage": r.stage,
         "title": r.title, "hint": r.hint, "level_label": LEVEL_LABELS[r.level]}
        for r in rows
    ]


def is_error(code: str) -> bool:
    return level_of(code) == ERROR
