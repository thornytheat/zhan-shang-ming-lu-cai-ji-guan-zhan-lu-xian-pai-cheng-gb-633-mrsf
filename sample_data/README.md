# 样例数据说明

主流程的数据来自 `sample_site/`（离线样例站，作为第二个 compose 服务跑在 19202）。
本目录只放一份可离线复读的说明与一份摘录，方便不启容器时核对字段。

| 文件 | 内容 | 脏点 |
|---|---|---|
| `sample_site/dir/list_p1.html` | 名录第 1 页，10 行 | 面积为千分位写法 `1,024` |
| `sample_site/dir/list_p2.html` | 名录第 2 页，8 行 | 声明 utf-8，实际 GB18030 字节 |
| `sample_site/dir/list_p3.html` | 名录第 3 页，9 行 | 文件带 BOM，表内出现重复表头 |
| `sample_site/dir/list_p4.html` | 名录第 4 页 | 表格未闭合（截断），末行只有半个单元格 |
| `sample_site/dir/list_p5.html` | 名录第 5 页，10 行 | 空值哨兵、空展位号、跨馆写法冲突 |
| `sample_site/forum/agenda.html` | 24 场论坛 | 时区 `Z` / `+08:00` / 无偏移混写，跨零点场次，同馆重叠 |
| `sample_site/exhibitors_export.csv` | 名录导出件 | 首列表头带 U+FEFF、列数不齐、全角展位号、面积写"约 24"、末行截断 |
| `excerpt.csv` | 上述名录的字段摘录，10 行 | 只用于核对列名，不参与跑批 |

期望值固定在 `tests/fixtures/expected.py`，改了样例就要同步改期望值。
