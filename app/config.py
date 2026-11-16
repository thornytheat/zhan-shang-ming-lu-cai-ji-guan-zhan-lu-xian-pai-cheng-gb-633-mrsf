"""boothplan 全局配置：展馆平面表、展位号语法、时间窗与排程参数。

这里是唯一来源，其他模块只读：
- 展位号一律解析成 (馆, 排, 位)，排/位统一两位字符串，前导零保留；
- 距离一律 Decimal，单位米，最小刻度 0.1；
- 时间入库一律 UTC，展示按 Asia/Shanghai（+08:00）；
- 论坛时间窗左闭右开 [start, end)，端点相接不算冲突。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc

RULE_VERSION = "boothplan/1.2.0"
DIST_QUANT = Decimal("0.1")
DIST_UNIT = "米"

# 展馆平面：锚点是该馆 1 排 1 位的地面坐标（米），右上为 x/y 正方向。
# 来源：展会平面图 2026 版，8 馆两两相对，中间是中央通道。
HALLS = {
    "A1": {"name": "A1 馆", "x": Decimal("30"), "y": Decimal("40"), "zone": "东翼"},
    "A2": {"name": "A2 馆", "x": Decimal("30"), "y": Decimal("120"), "zone": "东翼"},
    "B1": {"name": "B1 馆", "x": Decimal("110"), "y": Decimal("40"), "zone": "中区"},
    "B2": {"name": "B2 馆", "x": Decimal("110"), "y": Decimal("120"), "zone": "中区"},
    "C1": {"name": "C1 馆", "x": Decimal("190"), "y": Decimal("40"), "zone": "西翼"},
    "C2": {"name": "C2 馆", "x": Decimal("190"), "y": Decimal("120"), "zone": "西翼"},
    "D1": {"name": "D1 馆", "x": Decimal("270"), "y": Decimal("40"), "zone": "序厅"},
    "D2": {"name": "D2 馆", "x": Decimal("270"), "y": Decimal("120"), "zone": "序厅"},
}
HALL_ORDER = tuple(HALLS)
HALL_ALIASES = {
    "a1": "A1", "a2": "A2", "b1": "B1", "b2": "B2",
    "c1": "C1", "c2": "C2", "d1": "D1", "d2": "D2",
    # 单字母写法归到该馆的 1 号子馆，跨馆靠显式列纠正
    "a": "A1", "b": "B1", "c": "C1", "d": "D1",
}
COL_PITCH_M = Decimal("8")     # 同一排相邻展位间距
ROW_PITCH_M = Decimal("6")     # 相邻排间距
CROSS_HALL_PENALTY_M = Decimal("150")   # 跨馆额外折算的通道距离
WALK_M_PER_MIN = Decimal("72")          # 步行 1.2 m/s
DEFAULT_ENTRY = (Decimal("0"), Decimal("0"))   # 北登录厅

DWELL_EXHIBITOR_MIN = 20
DWELL_FORUM_MIN = 35
FORUM_SETTLE_MIN = 5           # 论坛散场缓冲，含在占位里

# ---- M7 备选方案与重排 ----
# 站点权重：优先级 1 最高（5），5 最低（1）；固定论坛窗不可放弃，权重 0。
WEIGHT_BY_PRIORITY = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}
DEFAULT_WEIGHT = 1
FIXED_WEIGHT = 0
TRANSFER_MIN = 4               # 跨馆换乘（安检 + 扶梯），并入到站时刻
MAX_HALL_SWITCHES = 6          # 同一天跨馆次数上限
HALL_SWITCH_RANGE = (1, 12)    # 接口接受的上限区间
# 二层连廊是单向扶梯：A2→B2 直行，B2→A2 要下到一层绕中央通道再上来，多 60 米。
# 所以回程更长，往返距离本来就不相等，路线合计必须按方向相加（TRAP-28）。
ONE_WAY_EXTRA_M = {("B2", "A2"): Decimal("60")}

# 展期与开放时间（本地时刻，分钟数）
SHOW_DAYS = {1: dt.date(2026, 11, 16), 2: dt.date(2026, 11, 17)}
SHOW_HOURS = {1: (9 * 60, 17 * 60 + 30), 2: (9 * 60, 16 * 60)}
# 展商默认首选时段：上午人少先踩点，排不进首选才改期
PREFERRED_WINDOW = {1: (10 * 60, 12 * 60), 2: (10 * 60, 12 * 60)}

STAGES = ("catalog", "booth", "windows", "route", "export")
STAGE_LABELS = {
    "catalog": "名录采信",
    "booth": "展位解析",
    "windows": "时间窗",
    "route": "路线排程",
    "export": "行程导出",
}
STAGE_FLOW = "名录采信 → 展位解析 → 时间窗 → 路线排程 → 行程导出"

# 编码回退链：gb18030 是 gbk/gb2312 的超集，放最后兜底
ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")
CHARSET_ALIASES = {
    "gb2312": "gb18030", "gbk": "gb18030", "gb18030": "gb18030",
    "utf8": "utf-8", "utf-8": "utf-8", "utf-8-sig": "utf-8-sig",
}

# 空值哨兵：集中定义，None 哨兵 / 0 真值 / 空串三态分开
SENTINELS = frozenset(
    {"", "-", "--", "—", "——", "/", "n/a", "na", "null", "none", "nil",
     "未知", "无", "暂无", "待定", "不适用", "未公布", "待确认"}
)

# 展位号语法：馆[ -排 ][ -位 ] / 馆-位 / 面积型 A×B
BOOTH_NUM_RE = r"(\d{1,3})"
AREA_SPEC_RE = r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)"

# 名录页视图：计划 5 页，单页上限防止门户分页参数异常
MAX_PAGES_PER_SOURCE = 8

# 名录列契约（列名 → 语义）。旧名映射用于 schema 演进。
COLUMNS = (
    ("exhibitor_name", "展商名称", "text", "必填，去首尾空格，同名不同馆进冲突表"),
    ("booth_raw", "展位号", "text", "必填，保留原写法，另存归一后的 booth_code"),
    ("hall_raw", "展馆", "text", "可空，与展位号里的馆交叉校验"),
    ("area_sqm", "展位面积(㎡)", "decimal", "可空，Decimal 1 位"),
    ("visit_priority", "拜访优先级", "int", "可空，1 最高；空值按 5 处理但不计入合计"),
    ("owner", "对接人", "text", "可空"),
    ("note", "备注", "text", "可空，含空位说明"),
)
COLUMN_ALIASES = {
    "展商名称": "exhibitor_name", "企业名称": "exhibitor_name",
    "公司名称": "exhibitor_name", "展商": "exhibitor_name",
    "展位号": "booth_raw", "展位编号": "booth_raw", "坐标": "booth_raw",
    "展馆": "hall_raw", "馆号": "hall_raw", "展厅": "hall_raw",
    "展位面积": "area_sqm", "面积(㎡)": "area_sqm", "面积": "area_sqm",
    "面积㎡": "area_sqm",
    "拜访优先级": "visit_priority", "优先级": "visit_priority",
    "对接人": "owner", "负责人": "owner", "联系人": "owner",
    "备注": "note", "说明": "note",
}

# 排程分类优先级：一台候选可能同时命中多条规则，按此固定顺序判定，
# 结果与输入行序无关（TRAP-19）。
CLASSIFY_ORDER = (
    "P_NO_POSITION",     # 面积型/无法解析的展位，进不了网格
    "P_OUTSIDE_HOURS",   # 时间窗不在展期开放时间内
    "P_OVERLAP_FIXED",   # 与同天固定论坛窗重叠
    "P_NO_SLOT",         # 当天已排满，任何时段都放不下
    "P_SHIFTED",         # 能让进，但不在首选时段
    "M7_HALL_CAP",       # M7 备选方案：跨馆次数上限挡住，时间本身放得下
)
CLASSIFY_LABELS = {
    "SCHEDULED": "排入",
    "RESCHEDULED": "改期",
    "CONFLICT": "冲突",
    "SKIPPED": "跳过",
}


@dataclass(frozen=True)
class Settings:
    """运行参数；测试用 replace() 覆盖，不走环境变量分支。"""

    db_path: str = str(PROJECT_ROOT / "var" / "boothplan.sqlite")
    sample_site_base: str = "http://sample-site"
    sample_site_dir: str = str(PROJECT_ROOT / "sample_site")
    eval_date: dt.date = dt.date(2026, 11, 15)
    rule_version: str = RULE_VERSION
    http_timeout: float = 5.0
    export_dir: str = str(PROJECT_ROOT / "var" / "export")

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        db_path = os.getenv("BOOTHPLAN_DB") or base.db_path
        site = os.getenv("SAMPLE_SITE_BASE") or base.sample_site_base
        site_dir = os.getenv("SAMPLE_SITE_DIR") or base.sample_site_dir
        eval_raw = os.getenv("EVAL_DATE") or ""
        eval_date = base.eval_date
        if eval_raw:
            eval_date = dt.date.fromisoformat(eval_raw)
        return replace(cls(), db_path=db_path, sample_site_base=site,
                       sample_site_dir=site_dir, eval_date=eval_date)

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).resolve().parent


# 名录来源：一个门户三段（5 页名录 + 1 页议程 + 1 个导出附件）
SOURCES = (
    {
        "id": "dir",
        "name": "展会名录站",
        "seeds": ("/dir/list_p1.html",),
        "attachment": None,
        "note": "5 页名录，第 2 页 GB18030，第 4 页表格截断",
    },
    {
        "id": "forum",
        "name": "论坛议程站",
        "seeds": ("/forum/agenda.html",),
        "attachment": None,
        "note": "24 场论坛，时区写法混用",
    },
    {
        "id": "export",
        "name": "名录导出件",
        "seeds": (),
        "attachment": "/exhibitors_export.csv",
        "note": "BOM + 列数不齐 + 全角数字",
    },
)
SOURCE_ALIASES = {"all": "全部来源", "dir": "展会名录站",
                  "forum": "论坛议程站", "export": "名录导出件"}
