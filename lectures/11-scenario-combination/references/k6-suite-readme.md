# k6 压测套件（客服工单系统 · 三阶段共用）（公开脱敏版）

> 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。

> 负载生成工具统一为 **k6**：本目录是三个阶段共用的**唯一实现**，各阶段 `tests/performance/k6/` 只保留薄入口。
> 监控使用案例已集成的 Prometheus/Grafana（`/metrics` 聚合趋势）与 Langfuse（逐笔 Trace）；
> k6 压测端指标可选直推 Prometheus remote write，在 Grafana「k6 压测」看板实时呈现（见下文「Prometheus 实时看板（可选）」）。
> 判定口径与各阶段 `tests/performance/common.py` + `src/utils/metrics.py` 对齐（四级判定 + 阶段附加维度 +
> 安全层）；门禁阈值、E2E 双口径与三态门禁统一继承 lib/contract.js
>（契约版本 `load-test-remediation-v1`，事实源见 目标契约），
> 由 selfcheck.mjs 做纯 node 断言自测（219 项，无 k6、无服务依赖）。

## 分工边界（重要）

| 职责 | 工具 | 说明 |
|---|---|---|
| 负载生成与聚合判定（基准/容量/长稳） | **本套件** | 非流式接口 `/api/v1/chat/complete`，一笔请求 = 一笔业务；覆盖三阶段 b0/c1/s1 |
| 阶段二 Agent 路径、降级/熔断、扇出效率 | 本套件（观测项） | 取自 `metrics.trace`（`plan_agents`/`agents`/`validation`/`degraded`/`fanout`），不进门禁 |
| 阶段三 path/branch/intent/tool 与首字 | 本套件（判定维度） | 与 `03-.<内部路径>` 同口径；`search_knowledge` 以 `knowledge_retrieval` 节点等效 |
| SSE 流式实测（TTFT/TPOT）与客户端中断 | `stream_benchmark.py`（三阶段） | k6 标准库缓冲整个响应，无法增量读取 SSE 分块；TPOT 按 `harness/tokenization.py` 的 token 边界聚合，阶段二/三服务端逐字符产出，原始片段间隔另存 `char_intervals_ms`，只作节奏校准 |
| 故障窗分类：级联降级/隔离、复稳判据 | `cascade.py`、`recovery.py`（阶段二） | 启动期故障注入 + 受影响/不受影响业务分类，判定逻辑复杂，保留 Python |
| 报告层派生：分支覆盖、联合门禁 | `branch_coverage.py`、`gate_suite.py`（阶段三） | 读取已落盘报告做聚合，非负载生成 |
| 进程内脚本：RAG 质量、检查点、关键路径 | `rag_quality.py`、`checkpoint*.py`、`critical_path.py`（阶段三） | 直连 `RagEngine`/服务对象与 `execution_id` 续跑，HTTP 无法覆盖 |

## 三阶段矩阵

| 维度 | 阶段一 Single Agent | 阶段二 Multi-Agent | 阶段三 Workflow+RAG |
|---|---|---|---|
| 服务端口（默认） | 8000 | 8001 | 8002 |
| 请求契约 | `{user_query, task_id, batch_id, stream}` | 同阶段一 | `{query, execution_id, task_id, batch_id}` |
| 判定主链 | 四级判定 | 四级判定（口径与阶段一相同，供跨架构对比） | 四级判定 **AND** `path_ok`/`branch_ok`/`tool_ok`/`intent_ok` |
| 时延口径 | 门禁 = `e2e_client_wall_ms`（客户端墙钟）；`e2e_server_ms`（服务端 `metrics.e2e_ms`）只诊断 | 同阶段一 | 门禁 = 墙钟 E2E + **首字**（`first_answer_ms` ≤ 服务端 `slo.first_answer_ms`） |
| 安全层 | `metrics.guardrail` | `metrics.guardrail` | `metrics.trace.guardrail`（阶段三护栏在 trace 内） |
| SLO 来源 | 阶段默认 10000ms（方案 §5 实验室档） | 同阶段一 | `/version` 指纹 `slo_e2e_ms`（默认 8000）、`slo_first_answer_ms`（默认 2000） |
| 计价来源 | 2.0 元/千 token（mock 假设） | 2.0（与阶段二 Python 脚本一致） | `/version` 指纹 `token_price_per_1k`（默认 0.15） |
| 阶段观测（不进门禁） | 工具覆盖、重试放大、首字分布 | + Agent 路径正确率、降级业务占比、熔断跳过数、扇出效率/墙钟/串行和 | path/branch/intent/tool 四项通过率、首字 P90 与达标率、节点错误数、置信度 |

## 目录结构

```text
k6-suite/                        # 案例共享套件（唯一实现）
├── README.md                    # 本文件
├── run-scenario.sh              # 执行封装：<1|2|3> <b0|c1|s1|e1|e2|calib>，读 /version 指纹注入判定口径
├── selfcheck.mjs                # 判定与看板自测（纯 node）
├── scenarios/                   # 场景工厂：defineB0/defineC1/defineS1/defineE1/defineE2/defineCalib(stage)
└── lib/
    ├── config.js                # 阶段无关配置、工单池加载、运行口径解析（阶段默认 vs 环境注入）
    ├── judge.js                 # 四级判定、安全层、工具/路径规则（纯 ESM）
    ├── segments.js              # 长稳段位与末段劣化（纯 ESM）
    ├── board.js                 # 看板取数与 HTML/JSON 渲染（纯 ESM，含阶段分区）
    ├── api.js                   # runTicket：请求 → 判定 → 指标记录（k6 依赖）
    ├── fingerprint.js           # setup 探活 /version 与 /health（k6 依赖）
    └── summary.js               # handleSummary：落 out/<前缀>-summary.{html,json}（k6 依赖）
└── stages/                      # 阶段适配器（纯 ESM）：buildBody / readView / extraDims / extraObs
    ├── base.js                  # 视图归一化与判定组合（三阶段共用主链）
    └── stage1.js / stage2.js / stage3.js

<阶段目录>/tests/performance/k6/   # 薄入口（每阶段 8 个文件，逻辑全在上方共享目录）
├── b0-baseline.js / c1-capacity.js / s1-stability.js / e1-traffic-change.js / e2-multiturn.js
├── calib.js                     # 校准场景薄入口（1 VU × COUNT 笔，不参与门禁）
├── selfcheck.mjs                # 转发到共享 selfcheck
└── run-scenario.sh              # 转发到共享封装（阶段号写死在本阶段垫片里）
```

## 快速开始

```bash
cd <案例路径>

# 0. 判定与看板自测（不需要服务、不需要 k6）
node k6-suite/selfcheck.mjs
node <内部路径>     # 阶段垫片等价入口

# 1. 启动目标阶段服务（mock 模式；阶段三先做数据自检与 RAG 预热，无外部数据库依赖）
(cd <内部路径> && python scripts/init_db.py)    # 输出数据快照与 RAG 冒烟命中
(cd <内部路径> && LLM_PROVIDER=mock uvicorn src.main:app --port 8000)
(cd <内部路径> && LLM_PROVIDER=mock uvicorn src.main:app --port 8001)
(cd <内部路径> && LLM_PROVIDER=mock uvicorn src.main:app --port 8002)

# 2. 冒烟 → 正式场景（在“阶段目录”下执行；产物落该阶段 out/，已 gitignore）
cd <内部路径>
tests/performance/k6/run-scenario.sh b0 -e ITERS=2 -e WARMUP=0   # 冒烟短样
tests/performance/k6/run-scenario.sh b0                          # B0 基准（245 工单全量）
tests/performance/k6/run-scenario.sh c1                          # C1 容量阶梯（默认 6 档 × 3min）
tests/performance/k6/run-scenario.sh s1                          # S1 长稳（20VU × 2min 缩短版）
tests/performance/k6/run-scenario.sh c1 -e LEVELS=1,5,10         # 施压机余量不足时降档
tests/performance/k6/run-scenario.sh calib -e COUNT=20           # 校准：1VU × 20 笔，出 P50/P90/P95/P99+token，不参与门禁
```

**B0 冷启动与普通基准（契约 §2，批次 R02）**：两者是**独立运行**——冷启动先重启被测服务，再以新 `RUN_ID` + `-e WARMUP=0` 执行（warmup 窗口为空）；普通基准另用新 `RUN_ID` 预热后进入计量窗口。两者的样本、摘要与 Goodput 分母不合并。

**隔离编排（契约 §2/§5/§6，批次 R04 起）**：需要运行清单、真实 k6 PID、角色事件与三态门禁复核的窗口，改用 `orchestrator/run-isolated.sh <1|2|3> <b0|c1|s1|e1|e2|calib> [k6 参数]`——它为每次执行创建 `out/runs/<run_id>/` 目录（manifest、process/、raw/、events/、fuse/、summary/、hashes.sha256），落盘 manifest 并由 `verify-run.py` 聚合出权威三态判定。`calib` 为本轮新增的校准场景（1 VU × `-e COUNT=20` 笔，输出 P50/P90/P95/P99 与 token 摘要，不声明 thresholds、不参与门禁），供真实模型轮开窗前校准 SLO/首字/计价；云端经 `lab.sh run --stage N --scenario calib --count 20` 进入。

两个旁路工具（均不进三态门禁，产物独立归档）：

- `orchestrator/monitor-docker.sh <output.csv> [interval] [duration] [-- 容器名...]`：本机压测窗口的容器层资源采样（`docker stats --no-stream`，默认 15s 间隔），补齐「本机无 cAdvisor」的资源证据缺口；云上实验室仍走 cAdvisor（监控方案 §4.3），两链路读数不混比。
- `orchestrator/bucket-trend.py <ndjson> <metric> [--interval 10] [--tag k=v]`：把 k6 `--out json` 产物按时间桶聚合成趋势 CSV（trend 出分位数、counter 出增量、rate 出占比），用于长稳/容量窗口内按固定间隔看指标漂移轨迹；分位数为桶内最近邻排名法，只作趋势读数，阈值判定以 k6 阈值与 verify-run 为准。

不装封装也可手工执行（**须先建 `out/`**，见下节）：

```bash
mkdir -p out && cd <案例路径>
k6 run tests/performance/k6/b0-baseline.js
```

## Prometheus 实时看板（可选）

`run-scenario.sh` 在环境变量 `K6_PROMETHEUS_RW_SERVER_URL` **非空时**，自动追加
`--out experimental-prometheus-rw`，把 k6 指标实时直推 Prometheus（k6 v1.6.1 实测输出名），
Grafana「客服工单案例 - k6 压测看板」随之实时出曲线。**缺省关闭**：不设该变量时命令、
产物与读数口径与既有完全一致。

```bash
# 前提：监控栈已启动（cd monitoring && docker compose up -d），
#       prometheus 已带 --web.enable-remote-write-receiver（本仓库 compose 已预置）
export K6_PROMETHEUS_RW_SERVER_URL=http://localhost:9190/api/v1/write
tests/performance/k6/run-scenario.sh c1        # 压测中打开 http://localhost:3001 → 「k6 压测」看板
unset K6_PROMETHEUS_RW_SERVER_URL              # 用完关闭，避免后续窗口误推
```

要点与口径：

- 封装器自动追加运行级标签 `stage=<1|2|3>` 与 `run_id=<RUN_ID>`（进 Prometheus label），
  看板顶部按 `stage`/`scenario` 变量切换；`phase`/`level`/`cold` 等 tag 同样映射为 label。
- 指标名映射（k6 remote write 实测形态）：Trend → `k6_<名>_p90/_p95/_avg/_med`
  （由 `K6_PROMETHEUS_RW_TREND_STATS` 决定，缺省 `p(90),p(95),avg,med`）；Rate → `k6_<名>_rate`
  （0—1 运行累计值 gauge）；Counter → `k6_<名>_total`（k6 会在原名后再补一个 `_total`，
  如 `business_total` → `k6_business_total_total`）。
- 运行结束自动写 stale marker，序列置陈旧：看板不残留历史平线，无压测窗口时面板为空属正常。
- 容量阶梯两块面板只取 `phase="measured"`（C1 测量窗口）并按 `run_id`/`level` 分开显示、
  不跨运行求平均：soak 等非 C1 场景的窗口为空属场景口径；SLO 判定以各档合格率阈值与
  HTML 摘要为准，容量 p90 面板不画 SLO 参考线以免被误读为实测值。
- 看板 Goodput 为滑动窗口速率（合格业务/秒），与摘要页运行级 Goodput 同口径同单位、
  分母不同（窗口 vs 全运行时长），引用任一读数须注明来源。
- `k6_*` 序列来自压测端，与服务端 `/metrics`（Agent 概览看板）分属两源；k6 与 Python
  脚本读数不混表的既有约定不变。TTFT/TPOT 为流式实测指标，k6 标准库缓冲整个响应、
  无法增量读取 SSE 分块（见「分工边界」），看板不合成该值，仅呈现业务首答复口径。

## 路径解析与产物

**路径解析（k6 v1.6.1 实测）**：k6 的 `open()` 以**调用它的脚本文件所在目录**为基准，与 cwd 无关。
共享 `lib/config.js` 位于 `k6-suite/lib/`，默认工单集按该目录上溯 2 级解析
（`lib → k6-suite → customer-service-ticket-system/testing/test-data/tickets.json`）。
`-e TICKETS=` 覆盖遵循同一规则，需要按 cwd 理解路径时请直接传**绝对路径**（封装器已自动传绝对路径）。

**产物（各阶段 `out/`，已 gitignore，不入库）**：

| 产物 | 内容 |
|---|---|
| `out/<前缀>.ndjson` | k6 逐笔采样（含 phase/level 标签），逐笔证据与报告层时序分桶用 |
| `out/<前缀>-summary.html` | 判定表（与 k6 退出码一致）+ 业务看板 + 安全层 + 阶段观测 + 长稳段位 + 环境指纹与口径一致性 |
| `out/<前缀>-summary.json` | 机器可读：看板字段、阈值判定、原始 k6 指标数据 |
| `out/<前缀>-calib.json` / `out/<前缀>-calib.md` | calib 校准摘要（机器可读 + 人读）：样本数/合格率、P50/P90/P95/P99、首字与 token 摘要、口径来源；不参与门禁 |

> k6 的 `handleSummary` **不会自建目录**（见 [grafana/k6#3084](https://github.com/grafana/k6/issues/3084)），
> `out/` 不存在时摘要写入失败且不报错中断；`run-scenario.sh` 已 `mkdir -p out`，手工执行请自行创建。

## 环境变量与判定口径注入

| 变量 | 默认 | 说明 |
|---|---|---|
| `BASE_URL` | 阶段默认 `http://127.0.0.1:<8000/8001/8002>` | Agent 地址；网关开启时用于探活、指纹与指标采样，网关关闭时也用于业务请求。云上默认业务入口由 `GATEWAY_URL` 决定 |
| `SLO_MS` | 阶段默认（阶段一/二 10000、阶段三服务端 8000） | E2E 判定 SLO；显式设置优先，其次取 `/version` 指纹，最后阶段默认 |
| `FIRST_ANSWER_SLO_MS` | 阶段三 2000；阶段一/二不适用 | 首字判定门槛（仅阶段三参与判定） |
| `COST_PER_1K` | 阶段一/二 2.0；阶段三 0.15 | token 单价（元/千 token）；阶段三优先取指纹 |
| `RUN_ID` | `k6-stage<阶段>-<时间戳>` | 批次 ID，写入每笔请求 `batch_id`，与服务端 Trace/指标对齐 |
| `TICKETS` | `<内部路径>`（相对 `k6-suite/lib/`） | 工单集路径（或绝对路径） |
| `OUT_PREFIX` / `OUT_DIR` | `<stage>-<场景>-<时间戳>` / `out` | 摘要产物前缀与目录 |
| `K6_PROMETHEUS_RW_SERVER_URL` | 未设置（关闭） | 非空时启用 Prometheus remote write 实时看板，值为写入端点（如 `http://localhost:9190/api/v1/write`）；仅影响指标推送，不影响判定与产物 |
| `TAG_TASK_ID` | `0` | 置 `1` 时把工单号作为 `task_id` 标签，仅用于小样本归因 |
| `ITERS` / `WARMUP` | `245` / `3` | b0 迭代数与预热轮数（schema v2.1.0 全量一轮；`WARMUP=0` 为冷启动独立运行：无 warmup 窗口，需服务重启后以新 `RUN_ID` 执行） |
| `LEVELS` / `STAB_SEC` / `MEASURED_SEC` | `1,5,10,20,30,50` / `60` / `120` | c1 档位与档内时间结构（契约 §2：稳定期不入容量分母，计量窗口为 Goodput/Token/成本共同范围） |
| `LOAD_MODEL` / `ARRIVAL_VUS` | `vu` / `200` | c1 负载模型：`vu`（默认，封闭模型，LEVELS=并发数）或 `arrival`（开放模型，LEVELS 重读为每秒到达笔数 TPS）；arrival 模式在途笔数 ≈ 到达率 × E2E 时延，`ARRIVAL_VUS` 为预分配 VU 上限，不足时 k6 丢弃到达计 `dropped_iterations`（business_total 低于 到达率×时长 即容量不足信号） |
| `VUS` / `SOAK_MINUTES` | `20` / `2` | s1 并发与时长（缩短版；正式按方案 §8.2 公式回填） |
| `GATEWAY_URL` / `GATEWAY_ORIGIN` | 本地直接运行未设置；云上 `lab.sh run` 默认注入 | 云上分别为 `https://<IP>` 与 `https://<控制机固定EIP>`：业务通过统一网关内网回环，认证 Origin 使用公网入口。`GATEWAY=0` 显式直连对照；详见下节 |
| `GATEWAY_USERNAME` / `GATEWAY_PASSWORD` | 无 | 网关链路凭据：云端经 `run-isolated.sh` 从控制机 `<内部路径>`（0600）读取；手工执行时经环境变量注入。**口令只进认证请求，不落 manifest、摘要与日志**；账号经 `<内部路径> add` 创建 |
| `CALIBER_SOURCE` | 由封装器注入 | `fingerprint`（按 `/version` 注入）/ `env`（人工覆盖）/ `stage-default`；写入摘要页 |
| `COUNT` | `20` | calib 校准笔数（1 VU `per-vu-iterations`；不声明 thresholds、不参与门禁） |
| `EXECUTION_ID` | 空 | 阶段三请求体 `execution_id` 注入（检查点续跑窗固定非空；缺省空串保持常规压测契约），逐笔判定明细回填该值 |
| `OBS_LANGFUSE` | 未设置（沿用 `/version` 探测） | 置 `0`/`1` 显式登记当轮 Langfuse 上报开关，写入 manifest `observation.obs_enabled`；未设置时沿用 `observability.langfuse_enabled`（既有行为不变） |
| `INJECTION_EXPECTATION` / `INJECTION_EXPECTATION_FILE` | 未设置（manifest 记 `null`） | 故障注入窗口预期 JSON（目标/时段/预期信号/判定边界），二者只能选一；非法 JSON 拒绝开跑，写入 manifest `injection_expectation` |

**本轮新增清单与逐笔字段（2026-11，阶段二/三真实模型轮 A 组改造）**：

- `run-isolated.sh` 落盘 manifest 新增：`link_mode`（`gateway`/`direct`，按 `GATEWAY_URL` 是否为空）、`observation.obs_enabled`、`injection_expectation`、`thresholds.first_answer_slo_ms`（阶段一/二为 `null`）、`thresholds.cost_per_1k`；派生公共子集档运行时另写 `data.subset`（派生清单哈希、父档指纹、条数、ticket_ids 哈希）并把 `data-subset-manifest.json` 复制进运行目录。
- 首字/计价口径解析优先级：环境显式值 > `/version` 指纹（真实模型轮优先 `slo_real_first_answer_ms`/`slo_real_e2e_ms`，Mock 轮仍用 `slo_first_answer_ms`/`slo_e2e_ms`）> 阶段默认；`run-isolated.sh` 用解析后值写 manifest 并显式 `-e FIRST_ANSWER_SLO_MS`/`-e COST_PER_1K` 透传 k6（首字为空时不传）。
- `raw/judgements.ndjson` 逐笔判定行新增：`server_run_id`（响应体 `run_id`）、`execution_id`（请求体顶层字段回填）、`started_at`/`ended_at`（由 `ts` 与 `wall_ms` 推）、`path`/`branch`/`intent`/`tools`（缺失给空值不报错）；旧字段与三态语义不变。
- 数据档校验白名单扩展：`TICKETS` 指向同目录 `subset-stageN.json` 时，校验同目录 `subset-stageN.manifest.json` 的父档指纹（父档 `manifest.json` 字节 SHA-256）、子集 SHA-256、条数与 `ticket_ids` 顺序，任一不符拒绝开跑；`golden|load|fixed-stageN.json` 旧行为不变。

封装器读 `/version` 的规则：环境显式值 > 指纹字段（阶段三 `slo_e2e_ms`/`slo_first_answer_ms`/`token_price_per_1k`；
真实模型轮优先 `slo_real_e2e_ms`/`slo_real_first_answer_ms`，Mock 轮保持原 `slo_*` 字段）
> 阶段默认值；`/version` 不可达时**不静默降级**——打印告警、标注 `stage-default`，并由摘要页给出「口径一致性」行。

## 网关链路口径（2026-09-25 起，口径变更）

`GATEWAY_URL` 非空时，**业务流量**走真实用户链路：k6 → 网关（TLS 终结 + `auth_request` 鉴权）→ 阶段前端（nginx 反代）→ Agent。
**控制面**（`/version` 指纹、`/health` 探活、monitor-biz/monitor-env 采样）保持直连 `BASE_URL`，不混入网关负载。

阶段路径由 `lib/gateway-headers.js` 拼接，`lib/api.js` 根据 `runtime.gateway.enabled` 选择 `runtime.gateway.businessUrl` 或 `runtime.baseUrl + runtime.endpoint`。阶段 1 当前 URL 为 `https://<IP>/single/api/v1/chat/complete`；阶段 2/3 分别使用 `/multi/`、`/workflow/` 前缀。网关去掉前缀后代理到 `<IP>:80/8081/8082`，前端 Nginx 再代理到本阶段 Agent 容器内 8000 端口。经过前端代理不表示 k6 执行了浏览器页面渲染。

要点：

- **内网回环，不走公网**：云端 k6 与网关同机（pt-control），业务请求打 `https://<IP>:443`，不经 EIP、不耗公网带宽。网关 compose 固定网桥 `<IP>/24`；`admin-acl.geo` 放行宿主机私网 `<IP>`（DNAT 路径下 nginx 实际看到的源 IP，2026-09-25 实测）、`127.0.0.1` 与网桥网段 `<IP>/24` 供本机回环。
- **TLS 主机名跳过**：证书 SAN 是公网 IP 而 k6 连内网地址，启动器对网关链路追加 `--insecure-skip-tls-verify`（仅实验室内网闭环口径）；`Origin` 头仍按认证服务强校验填 `https://<公网IP>`。
- **认证流**（`lib/gateway-auth.js`）：场景 `setup()` 真实登录预检（失败即中止，不带着坏凭据开跑）；每 VU 懒初始化独立会话（Cookie `__Host-cs-session` + 会话绑定 `X-CSRF-Token`）；业务请求遇 401/403 强制重登重试一次，重登计数走 `gateway_relogin_total` 指标（正常窗口应为 0，持续增长说明会话被服务端撤销）。服务端会话语义：空闲 30 分钟 / 绝对 8 小时（`sessions.py`）。
- **开跑前门禁**（`run-isolated.sh`）：凭据从控制机状态盘文件读取（环境变量已设不覆盖）；curl 网关登录预检非 204 即退出（事件记 `gateway_precheck_failed`），不静默直连。`manifest.target` 记录 `gateway_url/gateway_origin/gateway_username`（不含口令）。
- **判读纪律（口径变更）**：网关链路的 E2E 客户端墙钟含网关与前端两跳，与历史直连读数**不可直接对比**；直连基线保留「直连口径」标注，网关口径基线须在云端窗口重跑留档（`lab.sh run` 默认即网关口径）。`GATEWAY=0` 回退直连时，结论须标注直连口径，两种口径读数不混用。
- **路径证据优先级（2026-10-01 核实）**：先检查原始 `k6-summary.json` 的 `board.fingerprint.gateway.enabled/url`，再检查逐笔 `k6-samples.ndjson` 的业务 URL，并与该运行版本的请求选择代码交叉核对。`manifest.target.gateway_url` 是运行配置，`gateway_precheck_ok` 是登录预检事件；它们都须与实际业务样本互证。`manifest.target.base_url` 保留直连采样地址，不能单独证明业务绕过网关。字段或样本缺失时标为未验证，不沿用其他窗口推断。预压测第三轮 5 个窗口的已核对字段及历史误判见链路勘误记录。
- **施压资源边界**：k6 与统一网关同驻 `pt-control`，控制机资源既影响发压，也可能影响业务入口。k6 与 Agent 分机不能单独证明施压自耗不影响被测路径，需对照控制机水位和同期网关日志。Java 是否参与阶段 1 工具执行另由 `/version.java_service_enabled` 与工具注册分支核对，不由网关状态推断。

## 读数口径

- **合格业务率**：`request_ok AND flow_done AND business_done AND result_ok AND slo_ok AND security_ok is not False`，
  与三阶段 `business_levels` 同式；阶段三的 `result_ok` 另需 path/branch/tool/intent 四项通过。
- **业务数与分档**：业务数走 `business_total`（setup 的 `/version`、`/health` 探活请求不计入）；
  b0 的判定与 Goodput 取 `phase=measured` 分档（预热轮不计入），分档计数使用显式指标
  `business_measured_total` / `business_measured_qualified_rate`——实测 k6 v1.6.1 只为「阈值引用的标签」
  materialize 子指标，`business_total{phase:measured}` 并不存在。
- **协议层失败单列（第 12 讲断言分层）**：`business_payload_bad_total` 记「HTTP 200 但响应体不可解析」
  的笔数，不与业务不合格混计、也不冒充传输成功；三类信号的恒等关系为
  `business_total = 传输层失败 + payload_bad + 协议层成功笔数`，摘要对账时据此拆分失败原因。
- **Goodput** = 合格业务数 ÷ 分母秒数；分母优先取 manifest `measured` 窗口实际秒数，窗口缺失时退回
  整场时长并在摘要页标注 `total-run`（两种分母读数不混比；b0 整场分母含预热段）。合格笔数由
  `business_qualified_total` 计数器承载，Grafana「k6 压测」看板的 Goodput 面板即其滑动窗口速率。
- **E2E 双口径分开存储（契约 §3.1）**：`e2e_client_wall_ms` 为客户端墙钟，是 E2E P90/P99、SLO 判定与
  业务门禁的**唯一来源**；`e2e_server_ms` 取服务端 `metrics.e2e_ms`，只进诊断字段与观测读数。
  服务端读数缺失时**不再回退混入门禁分布**（缺失笔数单列 `e2e_missing_total` 诊断项）；
  两个指标不合并计算 P90/P99 或错误率，混口径读数不可直接比较。
- **安全层**：护栏结果存在时 `security_ok = (违规条数 == 0)`；缺失记 `null`（不适用、不阻断），
  并在摘要页单列「违规业务数 / 违规条数 / 安全数据缺失笔数」，缺失不冒充无违规。
- **阶段观测项**（不设阈值、不进判定）：工具覆盖率（`expected_tools` 全覆盖业务占比，<内部路径> §1 的
  Tool Accuracy 口径不同，不可直接比较）、重试放大系数、首字分布；阶段二 Agent 路径/降级/熔断/扇出；
  阶段三 path/branch/intent/tool 通过率与节点错误数。
- **长稳段位**（s1）：按 `exec.scenario.progress` 三等分 early/mid/late，给出各段墙钟 E2E avg/P90、合格率
  与**末段劣化（late 对 early，方案 §5 限值 ≤20%）**；该判定不进 k6 退出码。
- **阈值与三态门禁（契约 §3.3/§5，唯一来源 `lib/contract.js`）**：
  `qualified_rate >= 0.90`、`error_rate <= 0.005`、`e2e_p90_ms <= 本次运行口径 E2E SLO`
  （阶段一/二 10000ms，阶段三服务端 8000ms），**等于阈值即达标**（等值边界，不用严格不等号）。
  摘要页同时给出三态门禁 `PASS / FAIL / NOT_VERIFIED`：manifest、measured 窗口或必需安全证据缺失、
  熔断或中止的窗口判 `NOT_VERIFIED`（附机器可读原因码，如 `MISSING_MANIFEST`），不得当作通过；
  阈值判定的 k6 表达式与退出码一致，仅供参考对账。

## 与 Python 脚本的口径差异

- c1 按**档时长**（默认 1min/档），`capacity.py` 按**档样本数**；两者读数不直接混比。
- 阶段一/二 k6 默认 SLO 为实验室档 10000ms，Python 脚本默认 `--slo-ms 15000`（生产档）；
  引用读数必须连同档位标注，两套不混表。
- 阶段三 SLO 与计价由封装器从 `/version` 指纹注入，保证与门禁配置同源；Python 脚本从配置读取同一组值。