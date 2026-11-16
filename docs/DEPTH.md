# 深度登记 boothplan

- 规模：中型（7 个功能模块，页面 7 个，测试见 tests/）
- 核心规则：展位号解析成 馆-排-位 与平面坐标（跨馆加 150 米），论坛时间窗左闭右开，
  固定窗先落位、展商按优先级贪心插入最佳位置，再用带窗 2-opt 降低总步行。
  M7 在同一批候选上按三套取舍各排一次，换乘分钟计入到站，距离按方向逐段相加。
- 使用者与工作流：市场部 5 人 → 样例站名录与议程 → 采信 → 展位解析 → 时间窗冲突 →
  两天路线 → 行程单 CSV。一天在馆里走多少米、哪几家没排进去，都能在页面上复核。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 名录采信 | 触发采集，看每页编码、行数与截断标记 | POST /api/crawl · GET / | `app/modules/m1_catalog/reader.py` `app/modules/m1_catalog/paging.py` `app/modules/m1_catalog/decode.py` | `tests/test_catalog.py` |
| M2 展位解析 | 看展位号归一结果、坐标与解析原因 | GET /api/contract · GET /exhibitors | `app/modules/m2_booth/parse.py` `app/modules/m2_booth/geometry.py` `app/modules/m2_booth/normalize.py` `app/modules/m2_booth/contract.py` | `tests/test_booth.py` |
| M3 时间窗冲突 | 看论坛场次、时区写法与冲突原因 | GET /api/conflicts · GET /conflicts | `app/modules/m3_windows/windows.py` `app/modules/m3_windows/conflicts.py` `app/modules/m3_windows/agenda.py` | `tests/test_windows.py` |
| M4 路线排程 | 重算两天路线，看排入/改期/冲突/跳过 | POST /api/plans · GET /plan | `app/modules/m4_route/solver.py` `app/modules/m4_route/score.py` `app/modules/m4_route/distance.py` | `tests/test_route.py` |
| M5 批次版本 | 查批次、阶段计数、版本链与续跑能力 | GET /api/batches · GET /batches | `app/modules/m5_batches/service.py` `app/modules/m5_batches/history.py` | `tests/test_batches.py` |
| M6 行程导出 | 导出行程单、证据、分段与运行清单 | GET /api/export · GET /export | `app/modules/m6_export/csv_out.py` `app/modules/m6_export/itinerary.py` `app/modules/m6_export/service.py` | `tests/test_export.py` |
| M7 备选方案 | 三套取舍各排一次，切方案看损失与换乘 | GET /api/alternate · GET /alternate | `app/modules/m7_alternate/handler.py` `app/modules/m7_alternate/service.py` `app/modules/m7_alternate/directed.py` `app/modules/m7_alternate/limits.py` | `tests/test_m7_alternate.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 名录第 2 页声明 utf-8，实际 GB18030 字节 | `open()` 直接崩，或整页乱码 | 声明优先 + 回退链，失败记 S_CHARSET_FALLBACK | `app/modules/m1_catalog/decode.py:52` | `tests/test_catalog.py::test_gb18030_page_decoded` |
| TRAP-02 | 导出件首列表头带 U+FEFF | 首列名带隐藏字符，按列名取值为空 | 列名归一先 `lstrip("\ufeff")` | `app/modules/m2_booth/contract.py:18` | `tests/test_booth.py` 与 `tests/test_catalog.py::test_bom_on_first_header_stripped` |
| TRAP-04 | 分段导出重复表头 | 表头当成一条数据，名录多出一行 | 与表头逐格相同即剔除，另计分段数 | `app/modules/m1_catalog/reader.py:171` | `tests/test_catalog.py::test_duplicate_header_removed_and_counted` |
| TRAP-05 | 导出件某行末尾多一个分隔符 | 静默补空或整表错位 | 严格列数校验 + Q_COLUMN_COUNT 隔离 | `app/modules/m2_booth/service.py:96` | `tests/test_booth.py::test_column_count_mismatch_isolated` |
| TRAP-06 | 面积写 `1,024`、展位号写全角 `Ａ１` | `float()` 崩或截断成错值 | 全角转半角 + 去千分位后再 Decimal | `app/modules/m2_booth/normalize.py:56` | `tests/test_booth.py::test_thousands_separator_area` |
| TRAP-07 | 面积写 `N/A`、展位号写 `-` | 当成 0 参与合计，或当成有效展位 | 哨兵集中定义，三态分开 | `app/config.py:99` | `tests/test_booth.py::test_sentinel_area_is_none_not_zero` |
| TRAP-08 | 展位号 `A-01` 的位号带前导零 | `int()` 化后变 1，对不上平面图 | 排/位一律字符串补零 | `app/modules/m2_booth/parse.py:80` | `tests/test_booth.py::test_leading_zero_kept_not_int` |
| TRAP-11 | 议程同一页出现 `Z`、`+08:00`、无偏移 | 同一时刻落进两个时间桶 | 先归一到 UTC 再分桶，写法入证据 | `app/modules/m3_windows/windows.py:26` | `tests/test_windows.py::test_timezone_offsets_land_same_bucket` |
| TRAP-14 | 同名展商出现在两个馆 | 后写覆盖，或整批丢弃 | 两条都留证据，首次出现为主，另记冲突 | `app/modules/m2_booth/service.py:196` | `tests/test_booth.py::test_duplicate_exhibitor_keeps_both_evidence` |
| TRAP-16 | 第 4 页表格未闭合、CSV 末行截断 | 半行当有效数据 | 保留已闭合行，状态标 truncated 并给行数 | `app/modules/m1_catalog/paging.py:74` | `tests/test_catalog.py::test_truncated_table_keeps_closed_rows` |
| TRAP-19 | 一场论坛同时越界又重叠 | 按代码顺序覆盖，结果看书写顺序 | 固定规则优先级，与输入顺序无关 | `app/modules/m3_windows/conflicts.py:17` `app/modules/m4_route/score.py:49` | `tests/test_windows.py::test_rule_priority_independent_of_input_order` |
| TRAP-23 | 09:30-10:30 与 10:30-11:15 相邻场次 | 两端都算，重复计入冲突 | 左闭右开 `[start, end)` | `app/modules/m3_windows/windows.py:112` | `tests/test_windows.py::test_half_open_endpoints_touch_not_overlap` |
| TRAP-27 | 时间放得下，但当天跨馆已到上限 | 报成 P_NO_SLOT（当天排满），提高其他时段也没用 | 上限与时间分开判：先看时间能不能放，再看跨馆次数，超限给 M7_HALL_CAP | `app/modules/m7_alternate/service.py` | `tests/test_m7_alternate.py::test_hall_cap_reported_not_no_slot` |
| TRAP-28 | 二层扶梯单向，A2→B2 比 B2→A2 少 60 米 | 按对称距离算，2-opt 增量公式把反向也算成同样的米数 | 距离函数带方向，逐段相加；全馆两两核验，不相等的对单独列出 | `app/modules/m7_alternate/directed.py` | `tests/test_m7_alternate.py::test_one_way_bridge_detected_as_asymmetric` |

## 对账规则

- 名录行账：名录行数 = 可排程 + 无坐标 + 隔离 + 同名冲突
- 候选账：候选站点 = 排入 + 改期 + 冲突 + 跳过
- 议程场次账：议程场次 = 固定窗 + 时间窗隔离
- 距离账：每日合计（米，1 位小数）= 已排点位分段之和

四条账在 `GET /plan` 的对账表里逐条显示"平 / 差"，`POST /api/crawl` 返回 `all_balanced`。

## 备选方案账（M7）

- 权重账：排入权重 + 未排入权重 = 候选权重合计（三套方案各自成立，`GET /api/alternate` 的
  `invariant.balanced`）。
- 换乘账：方案合计 = 每日换乘分钟之和；跨馆才计，入口与同馆计 0。
- 上限账：C 方案每日跨馆次数 ≤ 上限，超出的候选记 `M7_HALL_CAP` 而不是 `P_NO_SLOT`。
- 方向账：`directed.total` 按顺序逐段相加，反向走单向扶梯会多 60 米，所以 A→B 与 B→A 不相等。

## 流水线义务

| 义务 | 落点 | 证据 |
|---|---|---|
| 阶段化 | catalog → booth → windows → route → export 五个阶段各有计数 | `/` 阶段条、`GET /api/batches/{id}` |
| 幂等重跑 | 批次键 = 来源 + 输入摘要前 16 位，命中已完成批次直接复用 | `tests/test_batches.py::test_idempotent_rerun_adds_no_rows` |
| 坏行隔离 | 坏行进 issues 表，好行继续 | `GET /conflicts`、`/api/export/evidence.csv` |
| 原因码 | 原因码唯一来源在 `app/reasons.py`，每条带级别、阶段、复核建议 | `GET /api/contract` 的 reasons |
| 对账 | 四账，见上 | `POST /api/plans` 的 reconciliation |
| 运行清单 | run_id、输入 sha256、规则版本、参数、阶段耗时 | `GET /api/export/manifest.csv` |
| 原子性 | 一次运行先删后插、单事务；失败只写批次与阶段 | `app/store.py:148` |
| 记忆上限 | 按页读、按页解析，不整站读入 | `app/modules/m1_catalog/paging.py:96` |
| 备选方案 | 同一批候选三套取舍，权重守恒，跨馆次数有上限 | `GET /api/alternate` 的 `invariant` 与 `comparison` |

## 已知不覆盖

- 不做真实网络抓取：`HttpFetcher` 与 `FileFetcher` 同一套解析，但样例站只有 7 页，
  没有反爬、登录、动态渲染的分支。
- 论坛座位/容量、展商预约时段、同馆并行参观未建模：一人一线，不做多人分工。
- 2-opt 只反转区间，不换点跨天；跨天再分配留给人工在 `/plan` 上取舍。
- M7 只比三套固定取舍，不做多目标搜索；单向通道表按平面图人工维护，目前只登记 A2 与 B2 之间这一对。
- 放弃一个站点腾出的时段只按到站最早补位，不试「两个轻点换一个重点」的组合。
- 面积型展位（`3x3`）没有网格坐标，只能进名录不能进行程，未做"按面积估算位置"。
- 节假与闭馆日不建模：展期只有两天，固定在 `app/config.py` 的 `SHOW_DAYS`。
