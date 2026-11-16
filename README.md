# boothplan 展商名录采集与观展路线排程

给展会观众（本次是南方工业自动化展 · 市场部 5 人）用的排路线工具：把门户上的
参展商名录与同期论坛议程采下来，解析展位号到平面坐标，判时间窗冲突，排出两天参观顺序，
导出可打印的行程单。

## 模块

| 模块 | 动作 | 入口 | 规则 |
|---|---|---|---|
| M1 名录采信 | 抓 5 页名录 + 议程 + 导出件，逐页诊断 | `POST /api/crawl` `GET /` | 编码回退链、重复表头、截断页、翻页断点 |
| M2 展位解析 | 展位号 → 馆/排/位 + 平面坐标 | `GET /api/contract` `GET /exhibitors` | 全角与千分位归一、前导零保形、同名冲突 |
| M3 时间窗冲突 | 论坛窗两两比较 | `GET /api/conflicts` `GET /conflicts` | 左闭右开、时区归一、规则优先级 |
| M4 路线排程 | 两天行程 | `POST /api/plans` `GET /plan` | 固定窗先落位 + 优先级贪心 + 带窗 2-opt |
| M5 批次版本 | 批次、阶段、版本链、续跑 | `GET /api/batches` `GET /batches` | 批次键幂等、版本递增、陈旧提交拒绝 |
| M6 行程导出 | 行程单 / 证据 / 分段 / 清单 | `GET /api/export` `GET /export` | Decimal 序列化、证据带行号与原因码 |
| M7 备选方案 | 三套取舍各排一次，切方案看损失 | `GET /api/alternate` `GET /alternate` | 权重守恒、跨馆上限、换乘并入到站、方向化距离 |

## 核心规则

1. 展位号解析成 `馆-排-位`，坐标 = 馆锚点 + 位×8 米 + 排×6 米；跨馆另加 150 米。
2. 论坛时间窗 `[start, end)`：端点相接不算冲突，重叠 1 分钟即冲突。
3. 固定论坛窗先按时间落位，展商按优先级插入「到站最早」的位置，
   最后对整天序列做 2-opt，只在窗口仍满足且总步行更短时接受。
4. 对账：候选 = 排入 + 改期 + 冲突 + 跳过；总步行 = 分段之和（米，1 位小数）。
5. 备选方案：同一批候选排三套（主方案 / 减站换权重 / 限跨馆），
   权重账「排入 + 未排入 = 候选权重合计」三套各自成立。
6. 跨馆换乘 4 分钟计入到站；二层 A2→B2 是单向扶梯，反向多 60 米，距离按方向相加。

字段与计算方式见 `docs/DATA-DICTIONARY.md`，陷阱与流水线义务见 `docs/DEPTH.md`。

## 启动

```bash
docker compose up -d --build
open http://127.0.0.1:18202/          # 主服务
open http://127.0.0.1:19202/          # 离线样例站
```

重建数据库：`docker compose down -v` 后重新 `up`。

## 演示

```bash
curl -s -X POST http://127.0.0.1:18202/api/crawl -H 'Content-Type: application/json' -d '{"source":"all"}'
curl -s -X POST http://127.0.0.1:18202/api/plans -H 'Content-Type: application/json' -d '{}'
curl -s 'http://127.0.0.1:18202/api/export/itinerary.csv' -o itinerary.csv
curl -s 'http://127.0.0.1:18202/api/alternate?plan=C&cap=3' | head -c 400
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18202/api/crawl \
  -H 'Content-Type: application/json' -d '{"mode":"delta"}'   # 400
curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:18202/api/alternate?cap=99'   # 400
```

## 页面

`/` 名录采信 · `/exhibitors` 展商网格 · `/conflicts` 冲突隔离 ·
`/plan` 路线排程 · `/batches` 批次版本 · `/export` 行程单 · `/alternate` 备选方案

## 测试

```bash
pytest -q
```

## 目录

```
app/{main,config,errors,reasons,manifest,store,web}.py
app/modules/m1_catalog/ … m7_alternate/
app/templates/  base.html + 7 页
app/static/     theme.css layout.css components.css app.js grid.js table.js
docs/           DEPTH.md DATA-DICTIONARY.md
tests/          conftest.py + 每模块 1 个测试文件 + test_e2e.py + fixtures/
sample_site/    离线样例站（名录 5 页 + 议程 + 导出件）
sample_data/    样例说明与字段摘录
```
