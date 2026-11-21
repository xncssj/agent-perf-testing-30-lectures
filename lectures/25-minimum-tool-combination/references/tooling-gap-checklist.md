本文件收录第 25 讲正文引用的取数与工具缺口清单（公开脱敏版）。内部路径已删除。

# 客服工单系统性能分析取数与工具缺口清单

> 版本：v1.5（2026-09-25 初版 v1.0；同日 v1.1 新增 Loki 数据源与 `loki-readonly` 条目；同日 v1.2 云窗口联调 Prometheus 与 Loki 通过、Langfuse 403 根因定位；同日 v1.3 五件通过、MySQL 包缺陷与 ssh-ops 白名单留档；同日 v1.4 创建 OpenAPI MCP 核心 `5thCijfKU021RibW` 并实测策略语法，六件数据面全部联调通过；2026-09-27 v1.5 修订：对齐 09-26 全场景轮事实与问题清单 v1.0——G04 改标已实施并随 run-quality 看板 V14 验证，登记 B1/B2/B3/B4/B6/D2 与 A2/A3 缺口）
>
> 定位：性能分析体系（[规格包 004]( Agent 取证说明引用；不复制工具契约本身。
>
> 依据：[MCP 工具层]( v1.5（工具契约唯一事实源）、[压测环境监控方案](压测环境监控方案.md) v1.4、[分析契约](
>
> 状态口径（三态，不得互换）：**设计可查**＝按设计与配置该数据源应能提供，但未经本实验室联调；**联调通过**＝本实验室真实环境实测通过并留有证据；**当前缺失**＝无实现、无权限或无证据。未实现的 façade 一律不标可用。

## 1. 数据源分级清单

| 数据源 | 分析用途 | 设计能力（设计可查） | 已验证能力 | 当前缺失／边界 | 联调验收动作 |
|---|---|---|---|---|---|
| Prometheus：k6 远程写序列（`k6_*`） | 业务曲线、逐档 Goodput／E2E／合格率／成本 | 带 `run_id`／`stage` 标签，按运行窗口做范围查询 | **云窗口 MCP 联调通过（2026-09-25）**：`prom_targets` 13/13 `up`，`prom_range` 以 `run_id=gw3-b0-v210-r3` 过滤返回真实序列；工具面实测为 `prom_query`/`prom_range`/`prom_discover`/`prom_metadata`/`prom_targets`，env 键为 `PROMETHEUS_URL` | 序列结束写 stale marker，即时查询取不到值，必须范围查询；`prom_range` 的 `step` 必填（RFC3339 时间）；k6 样本 tags 不含 `ticket_id`，跨源（k6↔Langfuse↔Prometheus）逐笔对账不可行（问题清单 B3，🟡 待修） | 已实测；日常按 run 窗口 `prom_range` 并与 `summary/run.json` 对数 |
| Prometheus：服务端／JVM／MySQL／容器／主机序列 | 全局监控第一层、异常窗对齐 | 按 `job`／`instance` 与时间窗对齐 | 云上 9 个采集 job 实测 `up=1`，具名容器序列与容量／观测税面板表达式逐一核对（2026-09-25）；MCP 联调同 k6 行（2026-09-25，cAdvisor 具名容器序列有读数） | 本机无 node／cAdvisor 指标（macOS 有意边界）；15 天保留，跨窗口对比走证据包 | 已实测 |
| Langfuse Trace | 逐调用证据、正常／慢／失败任务对照 | 官方 v4 MCP 只读子集（`listObservations`/`getObservation` 等，客户端 allowlist 限定） | **云窗口联调通过（2026-09-25）**：云侧 env 增加 `LANGFUSE_MCP_ALLOWED_HOSTS` 修复 Host 白名单后，隧道握手 200、`listObservations` 返回真实观测；修复已固化进 lab.sh 部署行。**该结论仅对钉版 4.45.1 成立**——浮动 tag `:4` 曾漂到上游 4.46.0 致 `listObservations` 恒空（问题清单 B4），已回退钉版 4.45.1 并清理 ClickHouse 空表记录 | 实测工具面 82 个读写混合——客户端 allowlist 红线必须执行（只读子集清单见 ops-mcp README 第 8 节）；异步上报按批次完整性未验；Langfuse 无 `getTrace` 单点查询接口，MCP 取数需绕行（问题清单 B4，⚪ 长期缺口） | 已实测；按 Run ID 汇总 Observation 与业务样本对账待分析窗口执行 |
| Loki 日志（`loki-readonly`，v1.1 新增） | 错误串聚类、异常栈定位、按服务／级别过滤 | 自研 façade：`query_range`/`query`/`labels`/`label_values`/`series`（全部只读 GET，经 `tunnel.sh` obs 通道 9310） | **云窗口联调通过（2026-09-25）**：`label_values(job)` 返回 14 个服务、`query_range` 查 `{job="agent"}` 返回真实日志行；日志管道本身已建（Loki+Promtail，保留 7 天，`job`=compose 服务名） | 窗口外取证走 `out/runs/<run_id>/` 归档，不依赖 Loki 历史 | 已实测；日常分析直接 `query_range` |
| MySQL（`mysql-readonly`） | 业务库数据核对 | `lab_reader` 对 `order_logistics`／`cs_agent` 两库 `SELECT` | **云窗口联调（2026-09-25）**：账号已建且权限正反验证通过（`SELECT 1` 放行、`CREATE DATABASE` 被拒）；MCP 包 `list_databases`/`list_tables`/`describe_table` 经 3307 隧道可用 | **包 `@teolin/mcp-local-mysql@3.3.9` 的 `query` 工具 macOS 实测损坏**（上游最新版无修复，见 ops-mcp README 第 8 节），任意 SQL 查询暂走 `ssh-ops` 或人工取证；`lab_reader` 无 `performance_schema` 权限——连接、慢查询细节只能经 mysqld_exporter 序列或容器内人工取证 | 已实测；评估替代包后重钉版恢复 `query` |
| SSH（`ssh-ops`） | 跳板上只读排障命令 | `docker ps/logs/stats`、`df`、`free`、本机端点 `curl` | **云窗口联调通过（2026-09-25）**：连接文件 schema 核验；服务端 `commandWhitelist` 全匹配白名单实测生效——只读命令返回真实输出，`docker rm`/`rm -rf`/`shutdown` 被拒 | `upload`/`download` 两个文件传输工具不经命令白名单，仍须客户端逐条确认兜底；变更类操作一律走 `lab-ops` | 已实测 |
| 阿里云（`aliyun-cloud-readonly`） | 实例状态、配置与部署变更时间 | `*Describe*` 白名单只读（代理 `--allow-tools`、safety-policy 与 RAM 只读用户三层客户端外强制） | **云窗口联调通过（2026-09-26）**：MCP 核心 `5thCijfKU021RibW` 创建后 `DescribeInstances` 正向返回四台实例；`StartInstance` 与跨产品 `ram ListUsers` 被服务端策略以 `rule: *` 拒绝（痕迹落 `.runtime/aliyun-mcp.log`）；策略语法实测 `*Describe*` 命中、`ecs:describe-*` 与带空格形态不命中 | 只读范围按 `Describe*` 命名约定（全产品 describe 属只读操作），RAM 层仅 ECS 只读兜底；核心为账号级资源，独立于 lab 窗口生命周期 | 已实测；日常分析直接经 CallCLI 查实例/云盘/价格 |
| 运行原始产物与证据包 | 运行资格、逐笔样本、三态门禁 | `evidence-readonly` façade 契约（`list_runs`／`get_manifest`／`get_run_summary`／`get_receipt`／`compare_runs`） | 产物链路实测：`testing/evidence/` 9 个证据包入库且 `verify-evidence.py` 9/9 通过（2026-09-24）；云端完整产物在 `pt-control:<run-root>`（含 `summary/run.json`、逐档摘要、事件与哈希）；本地 `ops/.runtime/` 镜像仅运行日志与窗口级 manifest | `evidence-readonly` 未实现（占位配置防误启用），Agent 侧当前只能由人工提供归档内容；本地镜像不含 `summary/run.json` 与逐档摘要，逐档核对须回云端或证据包；聚合器两缺口待修（问题清单 B1：k6 被外部 SIGTERM 中止仍判 PASS，契约规定中止窗应 NOT_VERIFIED；B2：五档零样本未触发 `INSUFFICIENT_SAMPLES` 被静默放行）；`run.json` 未含 p99 字段，验收级 P99 须补采（问题清单 D2） | façade 实现后：列 runs → 取 manifest → 对 `run.json` 门禁 → 抽样本哈希核对 |
| Grafana 看板（人用入口） | 发现异常时间段 | 三块预置看板（k6 压测、Agent 概览、资源总览）＋Run 级质量与证据对账看板（`agent-case-run-quality`，G04 交付） | 三块预置看板已随历次运行窗口呈现；Run 级质量看板 **2026-09-26 V14 验证通过**：9 面板按 `run_id`／难度档过滤，逐档 Goodput／有效样本量／客户端 E2E／服务端终态可对账（006 E/R07 证据；原 `testing/evidence/observability-evaluation-006/` 已随历史数据清理移除，git 历史与 OSS 归档可取回） | Agent 数据链不依赖看板 | 面板值与 `summary/run.json` 对数（读数纪律见[监控方案](压测环境监控方案.md) §3） |

## 2. 看板与容量证据对齐设计（缺口 G04，已实施）

本节为 G04 原始改造要求留档；实现为「Run 级质量与证据对账」看板（uid `agent-case-run-quality`，9 面板），2026-09-26 V14 验证（006 E/R07 证据；原 `testing/evidence/observability-evaluation-006/` 已随历史数据清理移除，git 历史与 OSS 归档可取回），不在本清单内代写看板 JSON：

1. 压测总览顶部增加 `run_id` 变量过滤，`k6_*` 序列按所选 Run ID 联动。
2. 容量页按 `level_id` 逐档展示四列：Goodput、合格率、E2E P90（客户端墙钟口径）、有效样本量；零样本档显式留白，不以跨档均值填充。
3. 任何面板值写入结论前与 `summary/run.json`、逐档摘要和原始样本核对（读数纪律见[监控方案](压测环境监控方案.md) §3）。
4. 受染 C1 轮次（[场景链复盘](场景链结果与根因分析-v1.0.md) §2：`cloud-c1-20260925T0459Z` 被 SIGTERM 中止、`cloud-c1-20260925T0840Z` 受并行部署干扰、`cloud-c1-20260925T0858Z` 因 SSH 重置中止）不能证明容量；静默窗口完整重跑（约 20 分钟独占）原列为独立后续运行任务，**已由 2026-09-26 静默窗口补跑闭合**（问题清单 C1）：阶段一 c1 六档全 PASS（50 VU Goodput 14.654/s）。注意阶段三 C1 是另一口径——09-26 全场景轮实测其首字 2000ms 门槛被节点间排队击穿（30 VU 合格率 61.1%、50 VU 归零，可用并发约 20 VU／5.724/s，问题清单 A2、[压测报告 §4]( c1 结论不可互相外推。

## 3. 定向取证工具与权限边界

| 工具 | 适用症状（触发条件） | 采集位置 | 时间与 Run 关联 | 权限边界 | 归档方式 |
|---|---|---|---|---|---|
| `jstack`／`jmap`／`arthas` | JVM GC 或连接池面板异常，需线程栈／方法级证据 | sut-agent／dep-services 容器内单次执行 | 异常窗±取证，事件挂当前 `run_id` | 人工经跳板，不进压测常驻 | 产物入 `out/runs/<run_id>/` 证据目录 |
| MySQL 慢日志／`performance_schema`／`EXPLAIN` | mysqld 面板慢查询突增，定位具体语句 | dep-services `cs-agent-mysql` 容器 | 同上 | `performance_schema` 经 exporter 账号序列或容器内人工执行；`lab_reader` 不可直读 | 同上 |
| `py-spy`（P2，未预装） | Agent 进程 CPU 异常、需要火焰图 | sut-agent 容器 | 同上 | 人工安装后使用；启用前提写入当窗记录 | 同上 |
| `top`／`pidstat`／`iostat -x`／`vmstat`／`sar` | node 面板异常但需进程级／设备级归因 | 四机按需 | 同上 | 人工经跳板 | 同上 |
| 日志检索（P1，已建管道，2026-09-25；v1.1 起 Agent 通道为 `loki-readonly`） | 错误串聚类、异常栈定位 | Agent 走 `loki-readonly` MCP `query_range`（如 `{job="java-service"} |= ERROR`）；人用入口为 Grafana「Java 订单物流服务」看板日志面板或 Explore 查 Loki（`{job="容器名"}`，保留 7 天）；`out/runs/<run_id>/` 运行日志与 Loki 窗口外取证仍走 `out/runs` 归档 | 按运行窗过滤 | 跳板只读（Loki 3100 仅私网） | 关键片段摘录入底稿并注明来源（Loki 查询式或归档文件） |
| Alertmanager（P2，未部署） | 告警路由与静默 | 观测栈 | — | 部署属独立工程任务 | — |

## 4. 优先级与边界

| 优先级 | 缺口 | 本清单的处理 |
|---|---|---|
| P0 | MCP 联调进展（2026-09-25/26 云窗口）：**六件数据面 MCP 全部实测通过**（prometheus/loki/langfuse/mysql/ssh-ops/aliyun，最后一件于 09-26 创建 MCP 核心并完成正反验收）；`evidence-readonly` 未实现；Langfuse 异步上报按批次完整性未验（原「看板缺统一 Run 过滤与逐档入口」「C1 受染轮次不能证明容量」两项已于 2026-09-26/27 闭合：前者见 §1 Grafana 行与 §2，后者见 §2 第 4 条） | §1 逐工具列「设计可查／已验证／缺证据／联调验收动作」，不启动、不代写服务 |
| P1 | 日志分散（2026-09-25 已建 Loki+Promtail 管道闭环并交付 `loki-readonly` façade，保留 7 天；MCP 日志取数同日联调通过）；`lab_reader` 无法直读 `performance_schema`；进程与依赖层深挖需按需取证 | §3 列触发条件、采集位置、Run 关联、权限边界与归档方式 |
| P2 | `py-spy` 未预装；Alertmanager 未部署 | 说明适用症状与启用前提，不列为文档验收前置 |

边界：MCP 联调、看板改造与静默窗口重跑已完成并各留证据（§1／§2），`evidence-readonly` 实现仍为独立工程任务；只有得到相应真实证据后，本清单 §1 的状态才可改「联调通过」，规格包 004 的运行类结论不因本清单存在而提前认领。

## 5. 新增缺口登记（2026-09-26 全场景轮，对齐问题清单 v1.0）

以下缺口为 09-26 三阶段全场景轮新确认或仍开放项，登记备查；逐条状态与证据以[问题清单 v1.0](

| 缺口 | 登记 | 出处 |
|---|---|---|
| 阶段三首字门槛归因观测缺口 | 服务端缺请求级时间戳与流式分块打点，节点间排队／流式调度层时延无法分解；A2 阻断项与 A3 双峰慢尾（b0 13.9% 慢尾）的确切构成均卡在此 | 问题清单 A2/A3；[压测报告 §4]( |
| `/version` 指纹缺共享数据文件哈希 | 数据版本需人工事后核对，直接导致 C4 数据指纹笔误 | 问题清单 B6（🟡 待修） |
| 聚合器两缺口（B1/B2）与 `run.json` 无 p99（D2） | 已随 §1 运行原始产物行登记，此处仅索引 | 问题清单 B1/B2/D2 |
