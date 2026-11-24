本文件收录第 26 讲正文引用的观测环境说明（公开脱敏版，源：Prometheus 监控部署说明）。

# Prometheus 监控方案

本目录是课程配套的系统级指标与日志监控环境：Prometheus 拉取客服工单三阶段服务与 MySQL/Java 依赖的指标并评估告警规则，Loki 集中存储容器日志（Promtail 采集），Grafana 提供预置看板。指标覆盖分层、数据消费模型（人看 Grafana、Agent 直查 Prometheus）与云上实验室的主机/容器层采集见《压测环境监控方案》。与 [Langfuse 观测环境](langfuse-selfhost-readme.md)分工互补：**Prometheus 回答"系统现在怎么样"（时延分布、终态分布、失败率的连续曲线），Langfuse 回答"这笔业务里发生了什么"（逐笔 Trace 与模型调用明细）**。

整套监控由 5 个容器组成：

| 容器 | 职责 | 宿主机端口 |
|------|------|-----------|
| prometheus | 拉取三阶段 `/metrics`、MySQL/Java 指标、接收 k6 远程写，存储时序数据（保留 15 天）、评估告警规则 | 9190 |
| grafana | 预置 Prometheus/Loki 数据源与五块看板（Agent 概览 / k6 压测 / 压测环境资源总览 / Java 订单物流服务 / 公网网关 Nginx） | 3001 |
| mysqld-exporter | 采集根 compose MySQL 的连接/QPS/慢查询等指标 | 9104 |
| loki | 接收 promtail 推送的容器 stdout 日志，保留 7 天 | 3100 |
| promtail | 经 docker socket 发现本机容器，按 compose 服务名白名单采集 stdout 推送 Loki | — |

端口说明：Langfuse 六容器已占用宿主机 `3000`（Web）与 `9090`/`9091`（MinIO），本栈改用 `9190`/`3001`/`3100` 避免冲突。镜像版本：prom/prometheus v3.5.0、grafana/grafana v12.1.0、grafana/loki 与 grafana/promtail v3.4.2（2026-09 实测）。

## 前置条件

- Docker 与 Docker Compose v2。
- 三阶段服务的端口规划（与根 `docker-compose.yml` 的 Docker 编排一致）：

| 抓取任务（job） | 阶段 | 宿主机端口 |
|----------------|------|------|
| single-agent | app/single-agent | 8000 |
| multi-agent | app/multi-agent | 8001 |
| workflow-rag | app/workflow-flow-rag | 8002 |

## 快速开始

以下命令默认在监控部署目录执行。

### 1. 启动监控栈

```bash
docker compose up -d
```

预期结果：两个容器 `Up`。`docker compose ps` 确认。

### 2. 启动三阶段服务（主路径：案例根 Docker 编排）

```bash
cd ../../..   # 案例根目录（customer-service-ticket-system/）
docker compose up -d --build                                  # 基础设施 + 案例一（8000）
docker compose --profile multi up -d --build                  # 案例二（8001）
docker compose --profile workflow up -d --build               # 案例三（8002）
```

备选：宿主机方式在各阶段目录执行 `PORT=8001 ./scripts/run_dev.sh`（端口与 Docker 编排对齐，二者勿同时运行以免端口冲突）。

阶段二/三需先 `cp .env.example .env`（脚本会自动加载；手动启动 uvicorn 时先 `set -a && source .env && set +a`，否则 Langfuse 导出不生效，但不影响本方案指标；Docker 路径的 Langfuse 启用见根 `docker-compose.yml` 头注释）。

### 3. 验证抓取与看板

```bash
curl -s http://localhost:9190/api/v1/targets | python3 -c "
import json,sys
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels']['job'], t['health'])
"
```

预期结果：`single-agent`、`multi-agent`、`workflow-rag`、`mysqld`、`java-service` 各行，未启动的服务为 `down`（属正常，启动后 15 秒内转 `up`），Prometheus 自监控 `prometheus` 为 `up`；`java-service` 依赖 Java 服务重建镜像后暴露 `/actuator/prometheus`（见《压测环境监控方案》4.5 节）。

发几笔业务（用各阶段 README 的压测脚本或直接 curl 工单接口）后查询任一指标：

```bash
curl -s 'http://localhost:9190/api/v1/query?query=agent_run_terminal_total'
```

预期结果：返回按 `job/status/stop_reason` 维度的计数序列。浏览器打开 http://localhost:3001 ，默认账号 `admin/admin`，首页即可见「客服工单案例」文件夹下两块看板：**Agent 概览**（服务在线、端到端 P50/P90/P99、终态速率、在途业务数、工具池在途/槽位、工具池过载拒绝、上下文窗口占用率、上下文压缩/剪枝事件、非 completed 终态占比、RAG 检索与引用速率、RAG 检索延迟）与 **k6 压测**（压测端实时指标，见下节）。

## 指标清单（来源：三阶段 src 代码与共享包 harness/，实测口径）

| 指标 | 类型 | 维度 | 阶段 | 定义位置 |
|------|------|------|------|---------|
| `agent_e2e_latency_seconds` | Histogram（桶 0.5-60s） | path | 一、二 | 各自 `src/main.py`（HTTP 中间件计时） |
| `agent_run_terminal_total` | Counter | status, stop_reason | 一、二 | 各自 `src/services/agent_service.py` |
| `agent_runs_inflight` | Gauge | — | 一、二 | 各自 `src/services/agent_service.py`（arun/astream 进出） |
| `workflow_requests_total` | Counter | path | 三 | `src/main.py` |
| `workflow_e2e_latency_seconds` | Histogram（桶 0.5-60s） | path | 三 | `src/main.py` |
| `workflow_run_terminal_total` | Counter | status, stop_reason | 三 | `src/services/workflow_service.py` |
| `workflow_runs_inflight` | Gauge | — | 三 | `src/services/workflow_service.py`（arun/astream 进出） |
| `workflow_rag_search_total` | Counter | status（hit/empty） | 三 | `src/rag/engine.py`（RagEngine.search；仅知识意图请求出样本，hit/empty 序列预建 0 值） |
| `workflow_rag_search_latency_seconds` | Histogram（桶 0.5ms-500ms） | — | 三 | `src/rag/engine.py`（进程内 BM25 检索内部计时，毫秒级量级） |
| `workflow_rag_citations_total` | Counter | — | 三 | `src/workflow/nodes.py`（答复节点引用产出条数，无检索引用不 inc） |
| `harness_tool_calls_inflight` | Gauge | — | 一、二、三 | 共享包 `harness/tool_executor.py` |
| `harness_tool_slots_total` | Gauge | — | 一、二、三 | 共享包 `harness/tool_executor.py`（启动时写 workers+queue_size） |
| `harness_tool_overload_total` | Counter | — | 一、二、三 | 共享包 `harness/tool_executor.py`（槽满快速失败） |
| `harness_context_window_occupancy_ratio` | Histogram（桶 0.05-1.5） | — | 一、二、三 | 共享包 `harness/metrics.py`（每次模型调用前观测） |
| `harness_context_events_total` | Counter | type | 一、二、三 | 共享包 `harness/metrics.py`（compress/prune） |

另有 prometheus-client 默认导出的进程指标（`process_*`、`python_*`）。

**依赖层指标（2026-09-25 起含 Java 业务指标）**：MySQL 层来自 mysqld-exporter（`mysql_global_status_*` 连接/QPS/慢查询）；JVM 与应用层来自 Java 服务 Actuator——自动导出 `jvm_gc_pause_seconds_*`、`jvm_memory_used_bytes`、`hikaricp_connections_*`、`http_server_requests_seconds_*`（SLO 桶 0.5/1/2/5/10/20/40/60s，与 Python 侧 E2E 直方图对齐）、`logback_events_total`（按 level 计数），业务埋点（`BusinessMetrics.java`，Micrometer 名 → Prometheus 导出名实测映射：圆点转下划线、Counter 加 `_total`）见下表；主机层（`node_*`）与容器层（`container_*`）仅云上实验室提供（node_exporter/cAdvisor），见《压测环境监控方案》第 2 节覆盖矩阵。看板：**压测环境资源总览**（16 面板，含观测税专项）、**Java 订单物流服务**（19 面板：可用性/接口 RED/业务计数/日志/资源）、**公网网关 Nginx**（10 面板：QPS/状态码分布/时延分位/路径 Top10/网关拒绝/日志，数据源 Loki，见日志管道一节）。

| Java 业务指标 | 类型 | 维度 | 语义 |
|------|------|------|------|
| `order_logistics_orders_created_total` | Counter | — | 建单成功计数（业务口径，POST /api/orders） |
| `order_logistics_order_transitions_total` | Counter | from, to | 订单状态机合法转换（4 组合预建 0 值） |
| `order_logistics_logistics_transitions_total` | Counter | from, to | 物流状态机合法转换（2 组合预建 0 值） |
| `order_logistics_notfound_total` | Counter | resource | GET 查询未命中——该失败以 HTTP 200 + success=false 返回，不产生 4xx，是业务失败的真实口径 |
| `order_logistics_rule_violations_total` | Counter | type | 业务规则违例（非法状态转换、shipped 缺物流单号/承运商） |
| `order_logistics_orders_pending` | Gauge | — | pending 订单存量（30 秒定时取样，非实时） |

注意三条口径边界：

1. `e2e_latency` 是 **HTTP 请求级**耗时；课程口径的业务级 Goodput（四级判定 + SLO + 安全）由压测脚本按笔计算，不在**服务端** `/metrics` 指标内。
2. **工具执行池为快速失败设计**（槽满明确拒绝、不排队），因此没有"队列等待时长"读数；队列堆积信号以「在途调用逼近槽位上限 + 过载拒绝速率」呈现。三个阶段历史上采集的实验记录（第 06、07 讲引用）没有本批队列/上下文指标，相关结论仍以当时记录为准。
3. `harness_context_window_occupancy_ratio` 的分母为各阶段配置 `context_window_tokens`（默认 8192，`CONTEXT_WINDOW_TOKENS` 环境变量可覆盖，教学口径）；阶段一/二分子按共享 `tokenization.py` 的确定性 token 边界计，阶段三分子取 LLM 结果的 `prompt_tokens`（Mock=估算，OpenAI=上报）。`harness_context_events_total` 的压缩/剪枝机制当前 Mock 环境未实现，序列恒为 0（观测点先于机制建立）。

## k6 压测指标（remote write，选通）

压测端 k6 指标经 k6 原生 `--out experimental-prometheus-rw` 直推本栈（prometheus 已带
`--web.enable-remote-write-receiver`，写入端点 `http://localhost:9190/api/v1/write`，仅本机可达），
在 Grafana「**客服工单案例 - k6 压测**」看板实时呈现：Goodput、合格率、业务 E2E vs SLO、
首答复时延、成功业务成本、容量阶梯各档与长稳段位。启用方式、指标名映射与口径边界见
[k6 套件说明](../../../case/customer-service-ticket-system/testing/k6-suite/README.md) 的「Prometheus 实时看板（可选）」；缺省关闭，
不影响本节 `/metrics` 拉取链路。`k6_*` 序列来自压测端，与上表服务端指标分属两源，不得混读。

## 日志管道（Loki + Promtail）

Promtail 经 docker socket 发现本机容器，按 **compose 服务名白名单**采集 stdout 推送 Loki（`loki/promtail-config.yml`，本机与云上各角色共用一份，push 地址经 `LOKI_URL` 环境变量注入）：白名单覆盖案例业务容器（java-service、agent 三阶段、frontend 三阶段、mysql、gateway/auth 等）与监控栈自身，排除 Langfuse 六容器。Loki 保留 7 天（`loki/loki-config.yml`，短于 Prometheus 的 15 天，控制磁盘；跨窗口日志对比仍以 OSS 证据包为准）。

- **公网网关链路（2026-09-25 起采集）**：网关 Nginx 的访问/错误日志写 `/dev/stdout`、`/dev/stderr`（`log_format main` 为书稿口径——性能测试实战30讲第6讲 combined 尾部追加 `$request_time $upstream_response_time`，另加 `$upstream_addr` 定位后端；apt 版 nginx 无官方镜像的 stdout 软链，必须显式路由，否则日志只落容器内文件、重建即丢）。云上由控制机 promtail 采集；本机冒烟起网关编排时由本机 promtail 自动发现。pipeline 对访问行提取 `status` 低基数标签供看板按状态码聚合。
- **日志标签**：`job` ← compose 服务名（Java 服务容器即 `job="java-service"`，与 Prometheus 抓取 job 名一致）、`container`/`compose_project`/`stream`；Java 服务日志为竖线分隔格式（`logback-spring.xml`：`时间|级别|线程|logger|消息`），promtail pipeline 提取 `level` 标签，不匹配的行（Python 服务纯文本、nginx error.log）原样透传不打 level；网关访问行（`ip - user [时间] "request" status …`）提取 `status` 标签，error.log 行不打 status。
- **查询入口**：Grafana Explore 数据源选 Loki，如 `{job="java-service", level="error"}`、`{job="gateway", status=~".+"}`（网关访问行）、`{job="gateway", stream="stderr"}`（网关错误日志）；「Java 订单物流服务」看板含 ERROR 速率曲线与实时日志面板；「公网网关 Nginx」看板（uid `agent-case-gateway`）提供 QPS、状态码分布、`$request_time`/`$upstream_response_time` 分位与路径 Top10、网关主动拒绝（401/302/503）与错误日志面板。判读口径：`request_time` 为入口总耗时、`upstream_response_time` 为后端耗时，rt 明显大于 urt 时优先排查入口侧消耗（TLS 握手、连接数、代理缓冲，书稿第6讲/第9章中间件速查口径）；auth_request 鉴权子请求的 upstream 不计入主请求日志变量（本机实测 401 行 urt/uaddr 均为 `-`），后端耗时面板先按 `urt != "-"` 过滤再 unwrap，静态页/网关本地拒绝不进该曲线。
- **启动期旧日志拒收属预期**：promtail 重建后 positions 清零会把 Docker 缓冲的历史日志重推，Loki 的 `reject_old_samples`（24h）拒收并使 promtail 短暂报 400，positions 追上尾部后自动恢复；该机制防止重建容器把旧日志写进当前时间线。

## 告警规则（prometheus/rules/agent-alerts.yml）

| 告警 | 表达式口径 | 阈值 | 持续 |
|------|-----------|------|------|
| StageServiceDown | `up{job=三阶段}` | = 0 | 2m |
| AgentRunFailureRateHigh | 非 completed 终态速率占比 | > 5% | 5m |
| WorkflowRunFailureRateHigh | 同上（workflow 指标） | > 5% | 5m |
| AgentE2ELatencyP90High | `histogram_quantile(0.9, ...)` | > 8s | 5m |
| WorkflowE2ELatencyP90High | 同上（workflow 指标） | > 8s | 5m |
| JavaServiceHttp5xxRatioHigh | Java 服务 5xx 请求占比 | > 1% | 5m |
| JavaServiceP95LatencyHigh | Java 服务接口 P95 | > 1s | 5m |
| JavaLogErrorSpike | Java 服务 error 日志速率 | > 0.5/s | 5m |
| JavaOrdersPendingBacklog | pending 订单存量（info 级） | > 500 | 10m |
| NodeCpuHigh / NodeMemoryHigh / NodeDiskAlmostFull | node_exporter 资源占比 | >85% / >90% / >85% | 5m/5m/10m |
| LabExporterDown | `up{job=采集器}` | = 0 | 2m |

Java 服务告警（`java-service-alerts` 组）阈值与 pending 积压线为实验室参考口径，首个压测窗口后复核；该服务当前未承载压测链路流量，无流量窗口接口/业务/日志序列无数据、告警不触发属预期（无数据≠零）。

资源层告警为实验室口径（`lab-resource-alerts` 组），node/cAdvisor 指标仅云上有，本机呈无数据属预期。

失败率与非 completed 占比的告警阈值按课程 mock 口径设定（SLO 8000ms）；换用真实模型时阶段三 SLO 变为 30s 口径，时延阈值需同步调整。当前栈未部署 Alertmanager，规则评估结果在 Prometheus UI（http://localhost:9190/alerts）查看，接入通知渠道属后续扩展。

## 常见问题

- **target 一直 down**：确认对应阶段服务已启动、端口与 `prometheus.yml` 一致；Prometheus 抓取周期 15 秒，启动后稍等再刷新。
- **loki/promtail 镜像拉取失败**：镜像自 2026-09-27 起直引北京 2 ACR（`registry.cn-beijing.aliyuncs.com/csagent-pt/loki:3.4.2` 等），先 `docker login` ACR 再重试；历史 daocloud 代理口径仅作离线应急留档。
- **Loki 里查不到某服务日志**：确认该容器在 promtail 白名单内（`loki/promtail-config.yml` 的 keep 正则按 compose 服务名匹配）且属于某个 compose 项目（`docker inspect <容器> | grep compose.service`）；手工 `docker run` 的容器不在采集范围。
- **Loki 里查不到网关日志（云上）**：三处依次核对——控制机已部署 promtail、安全组已放行日志端口、网关容器已按新配置重建（`up -d` 不因镜像更新而重建容器，以容器日志实见新格式为准）。
- **cAdvisor `up=1` 但容器面板为空**：`up=1` 只说明抓取端点可达，不代表容器采集可用。检查 `container_cpu_usage_seconds_total{name!=""}` 是否有具名容器序列——2026-09-25 实测 cAdvisor v0.49.1 的 Docker 客户端 API（1.41）低于 Docker 29.1.3 守护进程要求（1.44），三个目标全部只报根 cgroup；升级 v0.57.0 后恢复（见《压测环境监控方案》§4.3）。
- **k6 看板容量阶梯两块面板为空**：容量面板只取 `phase="measured"`（C1 测量窗口）且按 `run_id`/档位分别显示，soak 等非 C1 窗口为空属场景口径，不是采集故障；跨窗口无 C1 样本时保持为空。
- **改了 prometheus.yml 或规则不生效**：`docker compose restart prometheus`；配置校验可用 `docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml`。
- **Grafana 看板为空**：确认数据源（Connections → Data sources → Prometheus）Health 测试通过，且业务已发过若干请求（无样本时曲线为空属正常）。
- **镜像拉取失败**：本仓库镜像来自 `docker.io`；若直连失败，可用可用镜像源拉取后改回标准标签（2026-09 实测做法）：`镜像自 2026-09-27 起直引北京 2 ACR（registry.cn-beijing.aliyuncs.com/csagent-pt/prometheus:v3.5.0 等），无需再经代理拉取重打标签`，Grafana 同理。
- **停止与清理**：`docker compose down` 停止，时序与看板配置保留在 named volume；`docker compose down -v` 连数据一起删除。
