本文件收录第 26 讲正文引用的四源取证样例（公开脱敏版）。内部路径、内网地址与过程叙事已删除。

# 附表：四源取证样例（2026-11-04）

> 第 26 讲「四源取证样例」主件：每阶段正常/慢/失败样本的四源对齐（判定行 judgements + Langfuse 轨迹 + Loki 日志 + Prometheus/MySQL），产出覆盖表与缺失清单。**两笔定位 ≠ 四源关联完成**——本附表如实呈现各源关联键与缺失形态。

## 1. 选样清单（18 样本）

> 取样口径说明：慢样本系按运行中观察人工相对选取，未按分位标签复核；慢样本定位不受影响（仍为该轮内相对慢笔），因此选样方法记作「人工相对选取、非分位标签」。

| 阶段 | 类别 | 工单 | 轮次（源包） | wall_ms | qualified | 取样口径 |
|---|---|---|---|---|---|---|
| 一 | 正常 | LD1002000 | `0047-…-p1-03-p103` | 6109 | ✓ | P50 附近（网关轮） |
| 一 | 正常 | LD1001927 | `0047` | 6117 | ✓ | P50 附近 |
| 一 | 慢 | LD1000099 | `0047` | 9557 | ✓ | P90 以上 |
| 一 | 慢 | LD1001633 | `0047` | 10935 | ✓ | P90 以上（实测成立） |
| 一 | 失败 | —— | —— | —— | —— | **本战役阶段一无失败轮**（0047/0124 均 50/50）；旧轮 `FX1000029`（业务未完成 slo_ok=true、traceId a385cf75）按台账口径引用演示，判定原件已随旧环境销毁，无逐笔行——**跨战役引用降级登记，不作四源取证样本** |
| 二 | 正常 | LD2001948 | `0133-…-p2-21-direct` | 4713 | ✓ | P50 附近（直连轮） |
| 二 | 正常 | LD2001980 | `0133` | 5482 | ✓ | P50 附近 |
| 二 | 慢 | LD2001054 | `0133` | 7132 | ✓ | P90 以上（实测成立） |
| 二 | 慢 | LD2001317 | `0133` | 6238 | ✓ | 慢样本（人工选取，未达整轮 P90） |
| 二 | 失败 | 故障窗 2 笔 | `1318-…-p2-11-05b` | — | — | **失败形态=无判定行**：fault05 注入窗内笔 SUT 无可判定响应，judgements 仅含恢复段 15 笔（全合格）——判定行缺失本身即失败证据（缺失清单 #2） |
| 三 | 正常 | LD3001991 | `0024-…-p3-13-p313` | 13167 | ✓ | P50 附近（网关轮） |
| 三 | 正常 | LD3000555 | `0024` | 13192 | ✓ | P50 附近（订单 2024010100026 查询笔；归属分源——判定取自 0024 轮，轨迹样例仅九窗计数、无单笔轨迹，资源示例取自 1041 窗，MySQL 为事后底座核对，日志为 1041 窗跨轮查询方法演示，非同笔四源命中） |
| 三 | 慢 | LD3001994 | `0024` | 22904 | ✓ | 慢样本（人工选取，未达整轮 P90） |
| 三 | 慢 | LD3000527 | `0024` | 23436 | ✓ | 慢样本（人工选取，未达整轮 P90） |
| 三 | 失败(超时) | LD3000597 | `0024` | 30010 | ✗ | 30s 贴墙超时笔（订单 2024010100095 核对笔） |
| 三 | 失败(超时) | LD3001495 | `0024` | 30014 | ✗ | 30s 贴墙超时笔 |
| 三 | 失败(注入) | LD3000811 | `2201-…-p3-13-p314b-r6` | 30065 | ✗ | knowledge_path 持续注入+0 重试，12/12 全失败轮样本 |
| 三 | 失败(事故) | LD3000811/LD3001234 | `1029-…-p5-01-base3-422incident` | 秒级 422 | ✗ | **多源缺失演示**：前端 Nginx DNS 旧 IP 错转事故轮（2026-11-04 10:24，50 笔全 422、SUT 未收到） |

## 2. 四源覆盖表

| 源 | 关联键 | 正常/慢样本（12 笔） | 0024 超时笔 | 2201 注入笔 | 1318 故障窗 | 1029 事故笔 | 说明 |
|---|---|---|---|---|---|---|---|
| judgements 判定行 | ticket_id+run_id（本地 ndjson 全量） | ✓ 逐笔 | ✓（qual=✗ 行在） | ✓ | **✗ 无行** | ✓（qual=✗ 行在） | 六轮加 1318/2201/1029/1041 原件均在库 |
| Langfuse trace（ClickHouse traces） | **无 run_id 标签**；tags 仅 `stage:s0N`/`status`/`evidence:*` → 时间窗+stage 对齐 | ✓（九窗计数 49—50/轮，边界 1—2s 裁剪） | ✓ 同窗 | ✓ 49 | ✓ 18（=判定行数） | **✗ 窗内 0 条** | 1029 轮 SUT 未处理即无 trace，缺失实锤 |
| Loki 日志 | **无 run_id 标签**（labels 仅 compose_project/container/job/level/service_name/status/stream）→ 容器+时间窗+行内 task_id/server run_id | ✓（LD3000555 终态行命中：`run_id=3f575b13…, task_id=LD3000555, status=completed`） | ✓ 同法 | ✓ 同法 | ✓（multi 容器窗） | ✓（网关/前端容器有 422 行；**workflow 容器窗内 0 行**） | SUT 侧日志缺失、入口侧日志在——双向形态都取证 |
| Prometheus（指标） | k6 业务指标带 run_id 标签（52 轮值在册），即时指标历史查询能力未验证（本次未取得；轮结束即时查询为空不等于历史序列不存在） | △ 边界（当窗实时可查，事后仅资源指标） | △ | △ | △ | △ | 替代口径：资源维度按窗可回捞（1041 窗容器 CPU 27 序列/108 样本实测 ✓） |
| MySQL（业务库 `order_logistics_perf`） | 订单号（user_query 内 ORD…） | ✓ 底座核对（orders 表 2024010100026/2024010100095 均 shipped） | ✓ 同 | ✓ 同 | ✓ 同 | ✓ 同 | SUT 为只读查询不留请求痕迹——MySQL 是**业务数据底座存在性核对源**而非请求痕迹源 |

## 3. 缺失清单

1. **Langfuse trace 查询标签无 run_id**——tags 仅 stage/status；trace 主键 id 即服务端执行标识 server_run_id，与判定记录同源在位，直取查询待建；按窗取数时窗边界 1—2s 裁剪致 50→49 的偏差仍存在。
2. **1318-05b 故障窗 12 笔判定行缺失**——fault 注入窗内 SUT 无可判定响应，judgements 只有恢复段 15 笔；失败样本的判定面为空（k6 summary 的整轮 qr 0.2228 承载该窗结论）。
3. **1029 事故轮 SUT 侧两源全缺**（Langfuse 0 trace、workflow 容器 0 日志行；MySQL 为业务数据底座核对源，只读查询本不留请求痕迹，不构成该维度的缺失，见 §2 MySQL 行口径）——入口侧（网关 access log 422×50、前端 Nginx 422×50）完整在案；事故根因取证靠入口侧与容器地址快照闭环。
4. **Prometheus 历史业务指标未验证可回捞**——本次只验证当窗实时查询与资源指标按窗 range，历史业务指标能否按窗回查与保留策略未验证；业务指标留痕需 `--out prometheus remote-write` 常驻（挂账）。
5. **阶段一失败样本缺位**——阶段一（0047/0124）全 50/50 合格无失败轮；旧轮 FX1000029 原件已随旧环境销毁，仅作台账引用。

## 4. 取证命令形态（可复跑）

- **Langfuse 九窗计数**（观测机）：`docker exec -i <clickhouse-container> clickhouse-client < w9-lf.sql`，核心 SQL 形如 `SELECT '<轮>' r, count() FROM traces WHERE timestamp BETWEEN toDateTime64('<窗起>',3) AND toDateTime64('<窗止>',3) UNION ALL …`。时间戳为 UTC（judgements +08:00 时间−8h）。
- **Loki 样本行**：`curl 'http://<observability-host>:3100/loki/api/v1/query_range' --data-urlencode 'query={service_name=~".*workflow.*"}' --data-urlencode start=<ns> --data-urlencode end=<ns> --data-urlencode limit=800` 后行内 grep task_id（ns=UTC epoch 纳秒）。
- **Prometheus**：业务面 `http://<observability-host>:9190/api/v1/query --data-urlencode 'query=k6_business_measured_qualified_rate_rate{run_id="<轮>"}'`（当窗实时）；资源面 `query_range container_cpu_usage_seconds_total` 按窗（1041 窗 27 序列实测）。
- **MySQL**：`docker exec <mysql-container> mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e 'select order_id,status from orders where order_id in (2024010100026,2024010100095);' order_logistics_perf` → 两行 shipped。
- **judgements**：`<run_id>/raw/judgements.ndjson`（logrus 剥壳：`re.search(r'msg="(.*)"\s*$')` + 反斜杠还原 + json.loads；ticket_id 为键）。

## 5. 口径边界

- 「四源」在本附表语境=判定行/轨迹/日志/指标与数据底座五个取证面的统称（MySQL 为底座核对源）；本附表逐源打 ✓/✗/△，✗ 行即缺失清单条目，不冒充已知。
- 时间窗取自各轮 judgements 首末 `time=` 戳（±5—60s 放宽）；1041 窗 Langfuse 49/50 为边界裁剪效应非缺失。
