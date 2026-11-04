# 压测问题清单（客服工单系统）（公开脱敏版）

> 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。

> 版本：v1.2（2026-09-27 建档；2026-09-28 增补数据档配对轮发现 A6—A7、B9、C6、D7—D8；2026-09-29/30 增补 v1.1 预压测两轮发现 B10—B15、C7—C8；2026-09-30 增补控制实验轮发现 B16—B17；2026-10-01 增补预压测第三轮（pre3）发现 B18—B22、D4 建议闭环；2026-10-01 增补正式轮（20261001-v11-formal）发现 A8、B23 定性修正、B24—B28 与 S7R-4—S7R-7 申请单登记；2026-10-11 增补跨环境复刻轮（20261011-v11-formal2）发现 B29—B33——环境重建系问题五条，全部当窗处置闭环或口径消化；2026-10-12 增补三方对照复刻轮（20261012-v11-formal3）发现 B34—B38——环境恢复系与操作面问题五条，全部当窗处置闭环、口径消化或跟踪中；2026-10-17 增补 v12real1 真实模型战役状态勘正（D1 转部分推进、D2/D5 转闭环）与 AW-O2 时间窗勘误联动；同日复审轮补登 B39/B40、B20 状态收口与数据完整性未销项登记）
> 范围：2026-09-22 k6 套件落地至 2026-09-26 三阶段全场景轮（含 09-25 阶段一场景链）、2026-09-27/28 performance-v1 数据档配对轮全流程、2026-09-29/30 v1.1 预压测两轮（pre/pre2）、2026-09-30 控制实验轮（s7ctl）、2026-10-01 预压测第三轮（pre3，20261001-v11-pre3 复刻验证轮）、2026-10-01 正式轮（20261001-v11-formal，D4 实验室口径）及 2026-10-11 跨环境复刻轮（20261011-v11-formal2，场景 A 恢复重建环境）发现的问题、2026-10-12 三方对照复刻轮（20261012-v11-formal3，场景 A 二次恢复常驻环境），含已闭环项——闭环项保留作回顾与教学素材。
> 口径：问题按「被测系统 / 测试平台与工具 / 流程与执行 / 覆盖缺口」四类组织；每条注明发现轮次、证据出处、状态与后续动作。
> 状态图例：✅ 已闭环｜🔴 阻断级待办｜🟡 跟踪中｜⚪ 已知缺口（暂不动）

## A. 被测系统缺陷与性能问题

| # | 问题 | 发现轮次 | 状态 | 证据 |
|---|---|---|---|---|
| A1 | 阶段一共享 pymysql 单连接并发腐坏：s1（10min/10VU）合格率 9.88%、e1 spike/drop 相位 0%，但错误率 0、中位时延反降至 ≈1670ms（短路指纹，健康值 ≈3370ms） | 09-25 场景链 | ✅ 已闭环 | 场景链结果与根因分析、案例一 §14 |
| A2 | 阶段三首字 2000ms 门槛被节点间排队/流式调度层击穿：C1 30 VU 合格率 61.1%、50 VU 归零，可用并发约 20 VU（5.724/s）；CPU 峰值仅 0.041 核、错误率 0、无道歉退化 | 09-26 全场景轮 | 🔴 阻断级（验收条件：优化后配对复测至 30 VU ≥90%） | 压测报告 §4 #1、底稿三 H02、案例三 §22 |
| A3 | 阶段三 b0 双峰：211 笔 2.5—4s + 34 笔（13.9%）5.5—7.2s，4—5.5s 空档；双路径已证实（澄清短链 vs 完整检索链），慢尾在首答之后的流式段，确切构成待验证 | 09-26 全场景轮 | 🟡 跟踪中（缺流式分块打点） | 底稿三 H01、案例三 §22 |
| A4 | 阶段二 50 VU 高档 P90 轻微上浮 +1.7%：候选编排聚合竞争，资源未饱和、无质量影响 | 09-26 全场景轮 | 🟡 跟踪中（低优先级） | 压测报告 §4 #3、底稿二 |
| A5 | 阶段一本机 SQLite 共享连接竞态：容量 20 档间歇掉点（90%）、长稳 2 笔失败（T00049/T00043） | 09-09 本机轮 | ✅ 已闭环（ Harness v2 改造中修复；与 A1 同属「共享可变资源无同步」反模式） | 案例一 §9.1 |
| A6 | 阶段三知识规模悬崖：语料 10→100 篇即 E2E P90 6889→30026ms（25.7% 任务撞 30s 请求超时墙，首字不变、Token 5.7×），contact/policy 族全灭、workflow_rag 43%；kb100/combined10 与 kb10 完全一致（饱和）；Goodput 0.215→0.059/s（-73%） | 09-27/28 数据档配对轮 | 🔴 阻断级（RAG 检索-生成循环缺收敛控制；修复后按同矩阵配对复测） | 压测报告-数据档配对 §8、底稿 §F1 |
| A7 | 数据集 v1 黄金预期与 mock 冻结基线不兼容：①expected_facts 含 total_amount 的条目（fixed/load 池 346/2000=17.3%）因 mock logistics 缺省模板无金额行且 IntentDetector 按 v1 等价设计永不返回 order_info 而必现不合格；②compound 族全败（balanced 池 0/75）——多意图预期 mock 单意图路径无法满足；两者使 b0 门禁恒 FAIL，掩盖真实退化信号 | 09-27/28 数据档配对轮 | 🔴 待决策（修复方向二选一：升级 mock 的 order_info/compound 路径，或对齐黄金用例预期与冻结基线；不得为通过而降断言） | 压测报告-数据档配对 §4/§7、底稿 §F2、`<内部路径>` IntentDetector |
| A8 | 被测容器内存 30min 单调爬升（H6 观察项，**不判泄漏**）：stab 30min（20VU 常态负载）agent 容器内存 49→96MB 单调爬升（三段均值 62.8/76.3/89.7MB，≈0.9MB/min），同期无任何性能劣化（5min 分桶 Goodput 5.54—5.63（±0.8%）无下行、P90/P99 末段无抬头、overload Δ=0、conn 恒带 7—11）——无进程内 GC/对象指标通道，不能定 Leak/GC 惰性回收 | 2026-10-01 正式轮（S4 rank 4；30min 首次正式长稳观察项） | 🟡 跟踪中（观察项登记不判泄漏；定向依赖 S7R-5 全时长 soak + 容器内存/python_gc 指标联动采集，待拍板） | 底稿-S4 §1 H6 行/§3 rank4/§5-3、底稿-S5 §4 H6 行/§6-U6、底稿-S7 §3.2 S7R-5 |

### A1 细节备忘

根因：`<内部路径>` 全应用共享一条 pymysql 连接，并发报文叠写导致协议永久腐坏（`InterfaceError (0, '')`）。五证闭环定位；干净重跑逐位复现（9.89% vs 9.88%）证伪「init-host 污染」误归因。修复：线程本地连接 + 连接级错误弃旧重建；复跑 s1 99.05%、e1 各相位 100%。波及面仅限阶段一（阶段二只读 SQLite、阶段三无独立数据层）。

### A2 细节备忘

已证伪候选：资源瓶颈、传输错误、道歉退化路径、节点级时延劣化（intent 中位 514→547ms、response_gen 556→596ms 持平）——增长发生在节点间排队/流式调度层，具体位置待验证（缺服务端请求级时间戳）。候选优化：检索与首答并行化、流式提前化、上游连接池扩容；任一改动须按四项对照配对复测。

## B. 测试平台与工具缺口

| # | 问题 | 发现轮次 | 状态 | 证据 |
|---|---|---|---|---|
| B1 | 聚合器缺口一：k6 被外部 SIGTERM 中止（退出码 105、仅 28 笔样本）仍判 PASS，契约规定中止窗应 NOT_VERIFIED | 09-25 场景链 | 🟡 待修 | 场景链文档 §4 |
| B2 | 聚合器缺口二：C1 level 5—50 五档零样本未触发 `INSUFFICIENT_SAMPLES`，「有阈值无样本」被静默放行 | 09-25 场景链 | 🟡 待修 | 场景链文档 §4 |
| B3 | k6 样本 tags 不含 ticket_id，跨源（k6↔Langfuse↔Prometheus）逐笔对账不可行 | 09-26 全场景轮 | 🟡 待修 | 底稿一 B-H04 |
| B4 | Langfuse 无 getTrace 单点查询接口，MCP 取数需绕行；浮动 tag `:4` 曾混入上游 4.46.0 致 MCP API 恒空（已回退钉版 4.45.1 并清理 ClickHouse v50 空表记录） | 09-26 窗口 | ✅ 已规避（钉版）；接口缺口 ⚪ 长期 | AI-MCP 分析过程记录、云资源清单 09-26 留痕 |
| B5 | e1 spike pre 相位仅 7 笔样本，1 笔失败即触限：门禁契约缺最小样本数定义 | 09-25 场景链 | ⚪ 契约增强项 | 场景链文档 §4 |
| B6 | `/version` 指纹缺共享数据文件哈希，数据版本需人工事后核对——直接导致了 C4 指纹笔误 | 09-22 起挂账 | 🟡 待修 | 案例一 §12.8 #5 |
| B7 | Langfuse ClickHouse 摄取自耗 5.4 核、loadavg 峰值 182/12 核，同机并发档读数不可用 | 09-22 本机轮 | ✅ 已规避（09-26 四机分离 + 压测窗关上报） | 案例一 §12.9、案例二 §14.5 |
| B8 | 09-27 新窗重建踩坑：实例重建后 known_hosts 旧条目拒连、内网镜像拉取单连接僵死（tinyproxy 误判）、镜像改走北京 2 ACR 恢复 | 09-26/27 窗口重建 | ✅ 已记录并闭环 | 云资源清单 09-26 留痕 |
| B9 | 数据档执行链路三缺陷（本轮发现并当场修复）：①collect 与下一轮 run 撞 `.lab.lock` 静默失败（run+collect 需同链串行）；②SSH 半开连接致 tar 上传挂死 30 分钟（ssh_node 缺探活）；③macOS bash 3.2 全角标点并入变量名（`${VAR}` 已修，C3 同源新增两处） | 09-27/28 数据档配对轮 | ✅ 已闭环（lab.sh switch-data/ssh_node 修复，幂等上传保证重试安全） | AI分析过程记录 §2、战役证据日志（.runtime） |
| B10 | `tunnel.sh status` 对刚建立的 Loki 隧道误报「不通」：探测时序早于远端就绪（Loki 绑定内网 `<IP>:3100`，隧道转发需一次连接预热），复测 `/ready` 实际通——影响：观测通道预检可能误导排障方向 | 09-29 v1.1 预压测轮 | 🟡 待修（建议探测改先建链后探测或带 1 次重试） | 压测报告-预压测-v1.1链路 §5、`<运行时路径>` 连接日志 |
| B11 | agent `/metrics` 观测仪表缺口：collab 队列无进程内计数器（queue_depth/active_workers/first_request_latency 类）、`agent_run_in_flight` 在途 gauge 无序列——rank1「客户端 collab 队列代理预热」机制内部构成（代理排队 vs 下游首请求）无法在观测层分解，启动期在途堆积不可观测 | 09-29 v1.1 预压测第二轮（S4 缺口 G1/G3 续接） | ✅ 已闭环（2026-09-30 R3：admission 仪表 `agent_request_admission_seconds` 全链路实测——服务端产生/`/metrics` 暴露/business.ndjson 采集（s1r3 轮 count 159→173）；工具池无排队设计经复核不设伪 queue 指标，队列压力仍以 `harness_tool_calls_inflight`×`harness_tool_slots_total`+过载速率呈现；冒烟轮 `20260930-r3smoke-*` 三份回执 verified） | 底稿-S4 §5 G1/G3、§2 层②、底稿-S7 §2 P1/§3.3-R3；修复验证：`<运行时路径>`（回执 3/3 verified） |
| B12 | 环境采样 `environment.ndjson` 未标注采样宿主且 ncpu 恒 unknown：`loadavg1` 实为 pt-control 发压机本机 uptime、`health_code` 为 HTTP 探 sut `/health`（本轮经 `monitor-env.sh` 源码澄清）——后续分析易把发压机负载误读为被测机负载，资源占比无法核数归一化 | 09-29 v1.1 预压测第二轮（S4 缺口 G10 续接） | ✅ 已闭环（2026-09-30：monitor-env.sh ncpu 跨平台 fallback + sample_host/host_role 字段，冒烟实测 `ncpu=4, sample_host=pt-control, host_role=loadgen`） | 底稿-S4 §5 G10、收窗交接单缺口 2、实测 `<运行时路径>`（首条键无 host、ncpu="unknown"）；修复验证：`<运行时路径>`（回执 3/3 verified） |
| B13 | Langfuse exporter 开销量化缺：flush 批量阈值/字节数/时延无仪表，15s 抓取粒度下小批量早期 flush 不可见（基线噪声约 45KB/s）——H2 候选 b（上报突发）经 obs 入站 RX 时序判别降级（可见突发滞后 cq 尖峰 10—32s）但未绝对排除 | 09-29 v1.1 预压测第二轮（S4 缺口 G11 改写收窄） | ✅ 已闭环（2026-09-30 R3：`langfuse_exporter_flush_seconds`（result=ok/error）已实测采到——b0 冒烟轮 flush 0.033s 入 business.ndjson；绝对开销量化通道建立） | 底稿-S4 §5 G11、§2 层④、底稿-S7 §2 P3；修复验证：`<运行时路径>`（回执 3/3 verified） |
| B14 | c1 场景聚合层形态：run.json `windows_actual.measured=null`（仅 `measured_envelope_from_levels` 包络窗），聚合单窗口径对多档容量场景不适用；叠加 levels.json 逐档 `e2e_client_wall_p99_ms`/`e2e_server_p90_ms` 均 null 未产出（与 D2 同源），窗口包核验员因「单窗与逐档口径可比性确认超核验员权限」置 needs_review，需统筹按「容量结论只引用逐档摘要」审定后以 r2 修订包转 ready——多档场景聚合契约未固化，每轮需人工审定 | 09-29 v1.1 预压测第二轮 | ✅ 已闭环（2026-09-30：verify-run.py 多档场景 measured 按逐档包络回填 + c1.js 空阈值物化 `e2e_server_ms{level,phase}` 子指标 + `--summary-trend-stats` 补 p99；冒烟 c1 轮实测 measured 非 null、p99=5744.25、server_p90=5192.91；unittest 2 用例 + 既有 run 只读重放回归通过） | 窗口包-c1-r2 status_reasons/monitoring_gaps、收窗交接单缺口 1/3、实测 `<运行时路径>`（windows_actual.measured=null）、`summary/levels.json`（六档 p99/server_p90 均 null）；修复验证：`<运行时路径>`（回执 3/3 verified） |
| B15 | 采集包缺逐笔 Eval 判定明细：`business.ndjson` 仅含 `metrics_sample` 快照事件（本轮四轮 7—110 条），002 契约四级判定（金额/关键词 check 逐项）逐笔明细不在采集包——终态质量只能以 k6 逐笔判定计数 + run.json gate 汇总为事实源，逐笔判定不可复核（S5 派发口径勘误发现） | 09-29 v1.1 预压测第二轮 | ✅ 已闭环（2026-09-30：EMIT_JUDGEMENT 逐笔判定通道 + `--console-output` 落盘 raw/judgements.ndjson（含 ts/ticket_id/四级判定/keywords_missed/facts_missed 明细）；冒烟三轮实测 b0=5/c1=8/s1r3=172 行；注意 k6 logrus 壳格式，解析取 msg=" 内 JSON） | 底稿-S5 §0.2 勘误一、§5 U2、底稿-S6 §5 #9、实测 `<运行时路径>`（事件类型仅 metrics_sample）；修复验证：`<运行时路径>`（回执 3/3 verified） |
| B16 | 施压机 k6 init 容量墙：2C4G pt-control 上 k6 多档 c1 的 VU 预分配推进至约百档即施压机饱和假死——sshd/gateway 饿死致 SSH banner timeout（`Timeout, server <IP> not responding`）、无 OOM 记录、k6 进程存活但近乎停滞；八档 LEVELS=1,5,10,20,30,50,70,100 卡在 init 116/170，拆分轮 LEVELS=50,70 卡在 init 096/120 后同型断联，另一拆分 LEVELS=50,100 同型复现（主编留痕，本地无日志副本）；coordinator 记录 `k6_alive=true` 持续至 2026-09-29T18:27:32Z；同环境单档 LEVELS=70/100（1 scenario，init 70/100 VU）正常完成 gate PASS；最终实例重启两次恢复——c1 扩档 >100VU 受施压机规格限制；检查清单 B6「施压机余量」此前只约束内存/CPU 经验值，未声明 k6 init VU 池容量 | 09-30 控制实验轮（s7ctl R1 c1x 及拆分轮） | ✅ 已闭环（2026-09-30 老板拍板扩规格：pt-control 升 ecs.c9i.xlarge 4C8G，terraform+lab_meta 同步、实测 nproc=4/内存 7526MB、变配后 SSH/网关/容器自愈核对；新有效小时价 3.1276 元） | `<运行时路径>`（Init 116/170 + banner timeout + Prom remote write flush 12m18s）、`<运行时路径>`（Init 096/120 + 同型 timeout）、`<运行时路径>`、远端 `<内部路径>` 尾部（本地无采集副本）、对照 `<运行时路径>`/`-c1-100.log`（单档 gate PASS）、窗口包 `<内部路径>`（核验员产出中，路径以产出为准）；修复验证：`<运行时路径>`（回执 3/3 verified） |
| B17 | c1 多档场景与单档 LEVELS 变体 run 前未做施压机容量预检：本轮三次中断（c1x 八档、LEVELS=50,70 与 LEVELS=50,100 两次拆分）才定位到 B16 容量墙——检查清单 B6 可补一条「多档 LEVELS 总 VU 池 >100 时先小轮试探」的执行提示（流程纪律改进候选） | 09-30 控制实验轮（s7ctl R1） | ✅ 已闭环（2026-09-30：检查清单 B6 行与命令速查已补「多档 LEVELS 总 VU 池 >100 先小轮试探」提示） | 检查清单 B6 L27/L87、`<运行时路径>`、`<运行时路径>`；修复验证：`<运行时路径>`（回执 3/3 verified） |
| B18 | 聚合器 scope 口径混合（S6 缺陷候选 DC-A，三处同根因）：①e1drop board.goodput=全轮 124 笔÷计量窗 23.230s=5.338 笔/s，较全轮口径 2.8811/计量窗口径 1.1621 高估 1.9—4.6 倍（e1spike 轻度同型）；②c1/c1low board 单笔成本=全轮 token÷计量笔数（5.290/4.455 元 vs 修正 ~3.485/3.386 元），c1low 聚合 P90 含稳态点与 latencyScope 标签 phase:measured 不符（差 0.9ms）；③c1 run 级 goodput 分母三口径并存（runSec 1085.498s / 002 包络 1025.399s / 逐档合计 745.052s）——根因为聚合器 scope 未随相位型/分档型场景对齐 + c1 族 window_measured_tokens_total 缺失 | 2026-10-01 v1.1 预压测第三轮（S1-H5、S2 §6.2、S3 §1.3/§3.1、S5-H5、S6 §2.5/§4） | 🟡 待修（修复走 S7 工程项 P-DC：聚合器 scope 显式化 + 补 window_measured_tokens_total 指标；修复前下游引用一律以 S1 修正值为准，board 混合口径不得引用——不阻塞门禁判定） | 底稿-S1 §6-H5、底稿-S3 §1.3/§3.1、底稿-S6 §2.5/§4、实测 `<运行时路径>` 与 `raw/k6-summary.json` ；**2026-10-01 已修复（B18①②）**：board.js 实施分子分母同域原则（计量窗分母仅当 business_measured_total 在场；窗口 token 缺失时成本分母取全量合格数 business_qualified_total），新增 costScope 标注与 selfcheck F2/F3 回归用例（夹具=pre3 真实数值 124/43.035、3908/1025.399、5932），k6-suite 与 app 双侧 selfcheck 全过，修复已同步 pt-control 云端 case；B18③ 分母三口径随 ① 消解（run 级 board 现唯一口径：c1=计量包络 3.8100，与 S1 推荐口径一致）|
| B19 | e1drop/e1spike 计量窗边界 gauge 语义（S6 缺陷候选 DC-B）：`e1-traffic-change.js` 对 FIRST_PHASE 每笔刷新 start gauge + k6 Gauge 摘要保留末值 → run.json 计量窗起点=首相位末笔发射时刻（e1drop 05:56:43.513Z=high 末笔发射、e1spike 05:54:58.122Z=pre 末笔发射），e1drop 窗内仅 27/124 笔——「计量窗口径」读数不代表 drop/spike 场景全量语义 | 2026-10-01 v1.1 预压测第三轮（S2 §6.2 代码级确认；S1 §1 caveat④、S3 §1.3 吸收） | 🟡 待修（起点改「仅记录一次」（setup 或 VU 防重）或聚合器取 gauge min 值，并入 P-DC；修复前 e1 两轮一律以分相位读数（S1/S3 §1.1）为准） | 底稿-S2 §6.2（代码级：`<内部路径>` + `raw/k6-summary.json` gauge 实测）、底稿-S3 §1.3 |
| B20 | levels.json 档级 gate 误报 NOT_VERIFIED(MISSING_MANIFEST)（S6 缺陷候选 DC-C）：聚合器档级证据视图不含 run 级 manifest 归属 → c1/c1low 全部档级 gate 误报，run 级 manifest 在位且 gate=PASS——档级证据视图与 run 级证据归属未打通 | 2026-10-01 v1.1 预压测第三轮（S1 §1 caveat②） | ✅ 已闭环（2026-10-17 镜像规则修复＋11 包权威件补齐，详见证据列；环境性遗留 verify-evidence CRLF 假阳性拆至 B40 并同日修复） | 底稿-S1 §1 caveat②、实测 `<运行时路径>` 档级 gate 字段 ；**2026-10-01 已实证闭环**：pre3 误报系云端 09-26 旧版聚合器（档级视图不含 run 级 manifest）；仓库新版档级 gate 复用 run 级 has_manifest，同步 pt-control 后对 pre3-c1 重跑 verify-run——run gate PASS 且六档全 PASS 无误报；已入库 pre3 levels.json 为历史事实不改写，S1 caveat「以 run 级为准」处理正确**；⚠️ **2026-10-17 复核发现复发**：后续三轮的入库 `levels.json` 档级字段仍为同一误报形态——`20261011-v11-formal2-c1`、`20261012-v11-formal3-c1`、`20261014-v12-real1-c1` 六档全部 `NOT_VERIFIED(MISSING_MANIFEST)`，而各自 `run.json` 均为 `gate=PASS`。说明「仓库新版档级 gate 复用 run 级 has_manifest」的修复**未在云端运行环境的聚合器版本上生效**（各轮仍由旧版聚合器产出档级视图）。引申影响：`压测收尾阶段交接单-20261014v12real1` §1 与 `底稿-20261014v12real1-S1` §一／§4.1 曾据此误写「六档逐档全 PASS／无 NOT_VERIFIED」，已于同日按原始产物勘误；专栏第 06／19 讲相关表述同步改为「run 级 PASS ＋ 档级 B20 误报」口径。**待办**：把聚合器修复同步到云端并重跑受影响轮的 `verify-run`，或在证据包入库流程中改由 run 级 gate 派生档级 verdict 字段。**⚠️ 2026-10-17 二次复核——根因更正（前面「修复未在云端生效」的判断作废）**：以本机留存的 formal3 完整 run 目录逐档复算（`<运行时路径>`）证实：**判定逻辑本身没有缺陷**。run 目录里存在两份档级文件——`raw/k6-levels.json` 是 k6 侧**预判定件**（生成时按 `MISSING_MANIFEST` 预置 `NOT_VERIFIED`），`summary/levels.json` 才是聚合器按运行级证据链重判后的**权威逐档判定**（`verify-run.py` 第 187—234 行：逐档复核 qr／err／P90 门槛后写入）。实测 formal3-c1 权威件六档全 `PASS`，与我按同一门槛（qr≥0.90／err≤0.005／P90≤SLO）独立重算逐档一致；c1low 同样 PASS。**真正的缺陷是入库视图取错文件**：`<内部路径>` 镜像的是 `raw/k6-levels.json`（预判定件），而非 `summary/levels.json`（权威件），所以四个战役的入库包一律显示 `NOT_VERIFIED(MISSING_MANIFEST)`。更正后待办＝①补采各轮 `summary/levels.json` 进证据包（或改判据镜像规则）；②`收窗交接单`／底稿／专栏按「run 级 PASS ＋ 权威档级 PASS／预判定件另标」口径表述。✅ **2026-10-17 已闭环（含镜像规则修复）**：①**云端归档核验**——从 OSS 只读拉取 formal2-c1／formal3-c1／v12real1-c1（另加两轮 c1low 低档）的 `evidence.tar.gz`，包内成员 `./runner/summary/levels.json` **就是权威逐档判定件**，实测**全部六档（c1low 单档）`PASS`、`reasons=[]`**，与按门槛独立重算逐档一致；②**入库补采**——五份权威件以 `levels-authoritative.json` 落进对应 `<内部路径>`（含 `_provenance` 段：源对象、包内成员、SHA-256、抽取方式与日期），并逐包登记进 `NAVIGATION.md`；③**镜像规则已修**——`k6-suite/orchestrator/package-evidence.py` 改为**两份都进包**：`levels.json`（摘要件，保名兼容既有包引用）＋`levels-authoritative.json`（权威判定件），导航表分列两者层级、`aggregation.md` 增「逐档摘要 vs 逐档判定」两行、缺权威件时向 stderr 显式告警；本机用 formal3-c1 完整 run 目录试打包实测产出两件（摘要件 `NOT_VERIFIED` 占位／权威件六档 `PASS`），`verify-evidence.py` **56 项 checks 全过**、`test_verify_run.py` 2 项回归通过。**未重跑任何压测、零云费用。** ④**全量补齐**——`<内部路径>` 下含 `levels.json` 的 **11 个包**（pre3／formal／formal2／formal3／v12real1 的 c1 与 c1low，另 v12real1 s7r2-c1low）**全部补齐权威件，实测全部 PASS**，各包导航已登记；此后新产出的包由已修复的 `package-evidence.py` 自动带两件。 |
| B21 | Langfuse 取数通道缺失：本轮环境未提供 `lf_pull.py`/obs 隧道（`<运行时路径>` 实测无该脚本），S3 首答段/首答后段段内拆分、S4 层②应用内段 trace 覆盖、S5 trace 级段分解三步登记无法执行——H2 段内构成（k6 排队 vs 私网 vs API accept）与 H1 trace 级构成无法归因，pre2 S5 trace 结论仅作跨轮旁证；H2/H4 判级封顶 candidate 的缺环之一 | 2026-10-01 v1.1 预压测第三轮（S3/S4/S5 三步登记；S6 §6 #1/#5） | 🟡 跟踪中（**2026-10-01 S7R-1 已补采**：老板拍板后经 Langfuse 会话通道（NextAuth admin → tRPC traces.all/traces.byId，脚本 `<运行时路径>`）定向拉取受染窗 667 条 trace + 分层对照 88 条，完成 S5 段内分解补采（底稿-S5r1：受染抬升锁定 SUT 服务端计时起点前链路，B3 主键闭合）；**MCP 端点通道当日已恢复**（Host 白名单假设经零副作用探测证实）：403 系 `LANGFUSE_MCP_ALLOWED_HOSTS` 白名单不含内网主机名 `<内网主机名>`（含 localhost/127.0.0.1/EIP），客户端带 `Host: localhost:3000` 头访问 `http://<IP>:3000/api/public/mcp` 即通过（initialize→tools/call listObservations 全链实测取数成功，台账密钥有效——首测 401 系漏 `pk-lf-`/`sk-lf-` 前缀的测试错误已纠正）；永久修复仍建议 obs-stack compose 白名单补 `<内网主机名>`；后续轮 manifest 补 observation 段联动 S2-U1——**2026-10-01 已补**：run-isolated.sh manifest 新增 observation 段（/version observability.langfuse_enabled，失败记 null 从严），冒烟实测落盘 `{"langfuse_enabled": true}`，S2-U1 闭合） | 底稿-S3 §5-1、底稿-S4 §4-3、底稿-S5 §0.1、底稿-S5r1、底稿-S7 §3.2 S7R-1 |
| B22 | 采集链完备度降级 MV-1—MV-6（S6 缺陷候选 DC-D，手工等价链路）：无 agent-build-fingerprint 文件/running-images.txt（以 /version 指纹与 pre2 逐字符比对替代 MV-1）、无 lab.sh 运行清单与 quote/facts（MV-2）、无容器级隔离快照（MV-3）、无 deploy-role 重部署（MV-4）、无 stage_pause_others（MV-5）、collect 手工拼装且 nodes/ 四机日志未采集（MV-6）——manifest 缺 observation/nodes 段，B7 上报状态无本轮直接实证（从严按开启判读），S2 部署变更核对证据强度降为「无变更证据」而非「变更被排除」；preflight 亦降级行使（pt-control 水位+smoke.sh 真实工单+Prom 13 target 替代四节点 preflight.sh） | 2026-10-01 v1.1 预压测第三轮（收窗交接单缺口 1—8；S2 §3.1/§4/U1—U3；S6 §4） | 🟡 待修（S7 工程项 P-MV：正式轮恢复 lab.sh 全量采集链——fingerprint 文件/清单/隔离快照/nodes+observation 段；本轮判定已按 MV-2「manifest 缺失一律 NOT_VERIFIED」从严消化，七轮仍全 VERIFIED） | 收窗交接单-20261001v11pre3 缺口登记 1—8、底稿-S2 §4/§7.2、底稿-S6 §4 |
| B23 | 短时冒烟与监控采样间隔竞态（**2026-10-01 正式轮定性修正**：原判竞态被证伪——按本条处置建议「ITERS≥8」执行的 `formal-smoke` 轮（原定放行门①，后改列工程轮留档）仍 NOT_VERIFIED(MISSING_SAFETY_EVIDENCE)，实为 **B24 tarfile 丢执行位伪装**（四监控脚本 Permission denied，与采样间隔无关）；chmod 修复后 `formal-smoke2` 同参数（ITERS=8/WARMUP=3）PASS——本轮 NOT_VERIFIED 非竞态所致；竞态成分是否独立存在待下窗观察）：lab.sh 链路下 ITERS=2（约 5s）冒烟，环境/业务监控（15s/10s 间隔）首样可能晚于运行结束 → events 缺覆盖计量窗样本 → gate=NOT_VERIFIED（20261001-v11-obs-smoke 实测；同参数 labsh-smoke2 竞态通过 PASS）。判定从严不放宽；性能读数不受影响（冒烟轮本不入读数） | 2026-10-01 pre3 后加固轮（B4 修复冒烟时发现）；2026-10-01 正式轮定性修正 | 🟡 跟踪中（正式轮冒烟按 ITERS≥8 行使，B24 修复后放行门① PASS；竞态成分待下窗观察） | run-isolated.sh、运行目录 `<内部路径>`（云端 pt-control）、正式轮证据 `<内部路径>`（B24 伪装留档）与 `122927-20261001-v11-formal-smoke2/`（同参数 PASS） |
| B24 | tarfile 打包丢执行位（正式轮工程轮 `formal-smoke` 当窗发现并修复）：Windows 侧 python tarfile 打包监控脚本未显式设 `info.mode`，脚本入包丢失原执行位 → 云端解包后四监控脚本 Permission denied → 监控/安全证据未产出 → gate=NOT_VERIFIED(MISSING_SAFETY_EVIDENCE)；chmod 恢复执行位后 `formal-smoke2` 同参数（ITERS=8）PASS（P90 5189.2ms，observation 段首证；修复前后读数 5186.3 vs 5189.2 偏差 0.06%——缺陷只伤证据链不伤读数）。教训（与 2026-10-01 上午回写 `.agents/rules/repository-safety.md` 的 LF 化条目**同源不同面**——同属 Windows→Linux tarfile 打包流的元数据保真：LF 管行尾、本条管权限位）：**tarfile 打包须设 `info.mode` 保留原执行位**；B23「采样竞态」定性由此修正 | 2026-10-01 正式轮（formal-smoke 工程轮） | ✅ 已闭环（当窗 chmod 修复+smoke2 替代；formal-smoke NOT_VERIFIED 按契约窗口级排除不入读数、留档作证据） | 收窗交接单缺口 1、底稿-S1 §2.2/§2.3 B24 行、证据包 `<内部路径>`（NOT_VERIFIED 留档）与 `122927-20261001-v11-formal-smoke2/`（修复验证）、`.agents/rules/repository-safety.md` LF 化条目（同源不同面） |
| B25 | OSS 只增桶 + collect.sh `--ignore-existing` 同 run 重试 collect 死锁（正式轮 c1/stab 两轮触发）：证据桶 `csagent-pt-evd-20260925` 实例角色无删权（只增桶语义），collect.sh 以 `--ignore-existing` 断点续传——首次中断后同 run 重试 collect 时远端已有部分对象被静默跳过、清单/receipt 无法一致生成，重试恒卡死；当窗以 `ossutil cp -f` 强制覆盖 + 手动 receipt 重生成手术闭环（OSS 回执 8/8 verified）。脚本级修复建议：collect.sh 增加重试语义（既有对象改 -f 覆盖或先查后传） | 2026-10-01 正式轮（c1/stab collect 重试） | 🟡 跟踪中（当窗手术闭环、回执齐；collect.sh 重试语义/-f 修复待排期） | 收窗交接单缺口 2、底稿-S1 §2.3 B25 行、OSS `oss://csagent-pt-evd-20260925/runs/`（回执 8/8 verified）、本地副本 `<运行时路径>` ；**2026-10-01 脚本级修复完成**：collect.sh 两处 `--ignore-existing` 改 `-f` 强制覆盖（run_id 全局唯一，覆盖仅发生于重试修复场景；注释已入脚本），同步 pt-control 后以 formal-stab 重跑 collect 回归验证一次通过（此前需 cp -f 手术），B25 转脚本级闭环 |
| B26 | pt-control 无 cadvisor 抓取（S6 缺陷候选 DC-F）：Prometheus 无 pt-control 容器 target → 统一网关/web-auth/k6 容器级 CPU 不可观测——「网关与 k6 共驻 pt-control」竞争（链路勘误新增候选）只能用宿主机层（node CPU/loadavg）间接排除（本轮 ≤50VU 窗内峰 5.14%/load1 ≤0.37，无饱和证据；pt 侧 14.7% 瞬时三处未分解）——共驻竞争的容器级正查通道缺 | 2026-10-01 正式轮（S4 缺口 1 续接、S2 §2 残余暴露） | 🟡 跟踪中（S7R-7 工程项一并闭合：pt-control 部署 cadvisor 并入抓取，或网关接 nginx stub_status/metrics） | 底稿-S4 §2/§4 缺口 1/§5-2、底稿-S6 §4 DC-F、底稿-S7 §3.2 S7R-7 |
| B27 | Langfuse tRPC `traces.all` 无 filter 深翻页丢页 + limit≤100（S6 缺陷候选 TC-1）：全窗 DESC 翻页 54 页后跳过 12:39—12:48 全部 trace（observation 层同期 7452 条完整在位、无缺页——查询侧丢页非采集缺失）；`limit=200` 报 HTTP 400——深翻页全量导出会静默丢段（pre3-S5r1 的全窗翻页法在本轮数据量下失效） | 2026-10-01 正式轮（S5 §0.2 受染窗补采实测） | 🟡 跟踪中（规避口径已固化：受染窗拉取一律「时间 filter + limit≤100」逐窗；接口分页语义修复与 limit 上限文档化走 P-TRACE-TOOL） | 底稿-S5 §0.2 TC-1、底稿-S6 §4 TC-1 |
| B28 | Langfuse tRPC 接口能力双缺口（S6 缺陷候选 TC-2/TC-3）：①`traces.all` 列表级 `metadata` 字段恒为 null——列表级无法 ticket_id 粗筛，只能逐条 `traces.byId`（本轮 692 次 ×0.08s sleep ≈2 分钟，可接受但有量上限）；②`traces.byId` 返回不含 `observations` 子树——SPAN 级段分解（root span/首子段/末子段）只能走 MCP `listObservations`（本轮仅 S3 对 AW3a 簇行使） | 2026-10-01 正式轮（S5 §0.2；TC-3 与 pre3-S5r1 R3 登记同构复现） | 🟡 跟踪中（双通道等价用法已固化：metadata 锚定 + MCP SPAN 树，证据强度不低于 SPAN 末端锚定；接口补字段或双通道用法文档化走 P-TRACE-TOOL） | 底稿-S5 §0.1/§0.2、底稿-S6 §4 TC-2/TC-3 |
| B29 | **ACR 钉版镜像与最新源码漂移**（环境重建系，跨环境复刻轮核心发现）：10-11 场景 A 恢复直接拉起 ACR `:1.0.0` 钉版容器，`/version` 指纹 `3b04bc17b2763aaf` ≠ formal `b0ca8191aaba2dfc`——镜像构建自 09-27 前源码（settings.py=db09b313 时代、version.py=8ed76771 时代，落后 formal 部署源两笔提交）；**钉版镜像≠最新源码**，重建窗口若未察觉直接开跑即口径混版、复现叙事不成立（本轮 preflight 不校验指纹故未拦截，靠指纹核对纪律发现） | 2026-10-11 跨环境复刻轮（阶段 0 指纹核对发现；恢复实录 §5.1 预拉重打语义的盲区） | ✅ 当窗闭环（`rebuild-agents` 现场重建三镜像约 75 分钟后指纹逐字符一致；30 项源文件哈希逐项比对将漂移面精确锁定到 2 文件）；**硬前置已固化（2026-10-12）**——恢复 runbook §6「run 前硬前置清单 1」+ 检查清单 B11（重建窗口指纹等价放行）双处回写 | 场景矩阵-20261011v11formal2 §4 DV-E2、恢复实录 §5.1、运维台账踩坑⑧、S1′ §2.3 指纹等价证据 |
| B30 | **`lab.sh extend` 覆写 `runtime.outputs.json` 冲掉 `control_public_ip=127.0.0.1` 自跳补丁**：`apply_runtime` 的 `terraform output -json` 重建 outputs 文件，自跳口径回落 EIP 值；`ssh_node pt-control` 走 EIP 回环被 SG 拒（连接超时）——本轮 `gateway --production` 首次启动失败（sshd 探活 60s 判死）即此因；恢复时打的自跳补丁只活到下一次 extend/up | 2026-10-11 跨环境复刻轮（extend 后 gateway 首启失败定位） | ✅ 已闭环（当窗 jq 重打恢复；**2026-10-12 脚本级修复**：`control_ip()` 内置本机即 pt-control 时自跳 127.0.0.1——寻址层统一处理，terraform output 覆写不再破坏口径，outputs 无需手工补丁；旧版 lab.sh 的手工重打口径保留在 runbook §6 硬前置 2 作验证点） | 运维台账踩坑⑥、云资源清单 §6 当晚节、收窗交接单登记 3 |
| B31 | **`quote.json` 在 extend 通道不生成，`do_run` 硬依赖**：`quote_now` 仅在 `up` 时写 `$RUNTIME_DIR/quote.json`，do_run 清单生成 `cp` 该文件——10-11 重建 pt-control 只跑过 extend（无 up），smoke2 首跑在清单生成前失败（k6 未起跑、run_id 未污染、无产物；同 run_id 重跑无障碍） | 2026-10-11 跨环境复刻轮（smoke2 首跑失败） | ✅ 已闭环（当窗手工补报价；**2026-10-12 脚本级修复**：do_run 内置 quote.json 缺失自动按 LAB_WINDOW_HOURS 同参补报价，预算闸门语义不变） | 运维台账踩坑⑦、收窗交接单登记 4/6、`<运行时路径>` |
| B32 | **案例树 `git archive` 展开无 `.git`，`baseline_commit` 语义断裂**：恢复通道以 git archive 上行（只含工作区文件），pt-control 案例树无 git 仓库 → manifest `baseline_commit` 落 `unknown`；本轮 `git init + commit` 生成新基线 `ab0e74a1…`（与历史 `3e23df46…` 无血统关系，跨轮不可比） | 2026-10-11 跨环境复刻轮（阶段 1 建 git 基线时确认） | ✅ 口径消化（DV-E3 登记：跨环境等价判据=指纹+逐文件哈希，不用 baseline_commit；本轮 manifest 如实记录新基线）；⚪ 已知缺口（若未来需仓库血统连续，恢复通道须改 git bundle/clone 上行） | 场景矩阵 DV-E3、运维台账踩坑⑨、S1′ §2.3 |
| B33 | **`mcp.ops.json` 不在恢复归位清单，观测取数工具链断链**：prom-pull.py/lf-pull.py 硬依赖 `<运行时路径>`（600 实例化配置），10-11 恢复的「.runtime 十二件归位」不含此件（老实例随窗销毁）→ 工具链直接 FileNotFoundError；观测机随窗销毁前若依赖该通道将丢 S4/S5 证据 | 2026-10-11 跨环境复刻轮（观测抢收时发现） | ✅ 已闭环（当窗直连 HTTP 抢收全量零缺页；**2026-10-12 固化**：直连脚本脱敏入库 `<内部路径>`（密钥环境变量注入、B27 规避口径翻页，py_compile 通过）+ 恢复 runbook §6 连锁项 7 登记「观测取数通道重建」——mcp.ops.json 重建与直连两通道任选其一） | S4′ §三 缺口 9、收窗交接单、抢收产物 `<运行时路径>` |

| B34 | **`runtime.auto.tfvars.json` 不在恢复归位清单，`do_run` 窗口校验链首跑即断（B33 同族，归位清单第三次缺件）**：ensure_window/计价链 jq 读该件，10-12 恢复「.runtime 十二件归位」不含 → formal3 smoke2 首跑 `jq: Could not open …` 退出 2（k6 未起跑、run_id 未污染，补传后同 run_id 重跑 PASS）；同属恢复树能力面缺口：pt-control 系统 python 缺 pydantic，`precheck/generate` 不能在本机重建数据档（走 Windows 存档直传，formal2 先例） | 2026-10-12 三方对照复刻轮（smoke2 首跑失败） | ✅ 已闭环（当窗 Windows 补传占位口径件——台账已登记 2027-01-01 为过校验占位值；**检查清单固化**：B 节新增 B12「开跑前置件自检」三件套） | 收窗交接单 §缺口 1、检查清单 B12 |
| B35 | **pt-control 恢复后无 aliyun CLI，报价闸门链断（DV-E7 复发未沉淀为恢复清单项）**：B31 修复后 do_run 自动补报价仍需本机 `aliyun` 命令，10-12 二次恢复未预装 → smoke2 二跑 `FileNotFoundError: 'aliyun'`；补装 3.5.1 后恢复（alicdn `aliyun-cli-linux-latest-amd64.tgz`，版本化 URL 实测 404）。**新发现怪象**：凭据经 `<本地配置>` 环境变量注入时 `sts GetCallerIdentity` 报 SignatureDoesNotMatch——同一凭据 Windows 同法成功、sha256 比对传输无损；改写 `<本地配置>`（profile AK 模式）后一次通过，根因待查 | 2026-10-12 三方对照复刻轮（smoke2 二跑失败） | 🟡 跟踪中（CLI 已补装、config.json 口径恢复报价链；env 路径怪象待下窗复现时抓 `--verbose` 签名串比对） | 收窗交接单 §缺口 1/待拍板 5、排障手册第 19 行 |
| B36 | **`lab.sh backup` 硬前置挡道：核心备份件三件不在本机（`bootstrap.tfstate`/`foundation.auto.tfvars.json`/`backend-foundation.hcl` 在原 Mac）**：10-03/10-12 两窗均如此，此前仅以恢复实录「遗留③」文字登记未编号 → formal3 收窗 backup 退出 2（「核心备份件缺失…先补齐再备份」硬前置正确拦截）；保全替代=collect OSS 回执 7/7 + 观测抢收 120M + 运行目录拉回本机 | 2026-10-12 三方对照复刻轮（收窗 backup） | ⚪ 已知缺口（三件待从原 Mac 抢救归档后 backup 方可全绿；替代保全口径已在收窗交接单登记） | 收窗交接单 §缺口 4、运维台账 formal3 条目 |
| B37 | **Windows→pt-control 长窗 SSH 瞬断（操作面，本地失败≠远端失败）**：c1 六档运行中本地连接 `Connection closed by remote host`（exit 255），远端 lab.sh/run-isolated/k6 进程树未受影响、run 正常完成 PASS——本地任务退出码只反映连接，不反映远端执行 | 2026-10-12 三方对照复刻轮（c1 轮中途） | ✅ 已闭环（后续轮 SSH 加 `-o ServerAliveInterval=20` 保活零复发；存活判定一律以远端 runs 目录/日志 mtime 为准——与排障手册 29 行同判法，本条补 Windows 端形态并回写公共规则） | 收窗交接单 §缺口 3、repository-safety「路径与迁移」SSH 保活条 |
| B38 | **手工 compose 拉起 Agent 漏叠 `perf-data.yml` overlay → 假指纹漂移（B29 同族）**：switch-data 后手工 `docker compose up`（只带 sut-agent.yml）拉起，settings 回落镜像默认 test-data（file_hashes 36 项、含 `tickets.json`、读 v1.0 基准），`/version` 指纹 `ba3c5ba6…`≠目标 `b0ca8191…`——MOCK_RULE_VERSION 等其余 env 正常生效，极具迷惑性；本机同算法复算得正确指纹判定为输入侧差异，按 deploy-role 完整口径（叠 overlay）重建后恢复 35 项 | 2026-10-12 三方对照复刻轮（开跑前指纹核对） | ✅ 已闭环（当窗重建恢复；**判别法双沉淀**：排障手册第 18 行 + 公共规则「结构判等先行」——指纹判等先数条目数、再看哨兵键，两秒定位数据档未接） | 收窗交接单 §缺口 2、排障手册第 18 行 |
| B39 | **S4 聚合件 stab 桶键整体错一桶（分析产物键窗映射缺陷）**：`<运行时路径>` 的 stab_bN 键实为桶 N-1 区间数据（按 5 分钟桶键 5973276×300s 换算实证），S4 底稿 §一表桶 2/6/7 三列与 §二内存三段曾引错位值，并曾致 AW-O2 时间窗首标 12:55—13:00Z（实为桶 5 区间） | 2026-10-14 v12real1 战役（2026-10-17 复核发现） | 🟡 跟踪中（表值已按原始 Prom 序列正确窗全量重算回填并独立抽验一致；聚合件为运行产物不改写，修复＝聚合脚本窗口映射修正后重跑聚合、下窗生效；下游 17—19 讲已同步、17 讲配图已加勘误边界注定稿后重出） | S4 底稿 §H7 勘误与 §一勘误联动注、本文 §「2026-10-17 增补」AW-O2 联动段 |
| B40 | **verify-evidence.py 按原始字节校验，Windows CRLF 检出下哈希假性 FAIL**：本机 core.autocrlf 检出使 LF 入库件字节不符（formal3 四个非 c1 包 hashes.sha256/manifest.json 等报「哈希不匹配」；LF 归一双口径复检 38/38 全过，云端/WSL LF 检出不受影响） | 2026-10-17 复审实测 | ✅ 已闭环（脚本哈希比对加 LF 归一降级复检：原始字节不匹配且归一匹配时输出 DUAL-PASS 不判 FAIL；v12real1-c1 原 56 项口径零回归、formal3 四包转 PASS 退出码 0） | verify-evidence.py、B20 证据列 CRLF 双口径记载 |

## C. 流程与执行纪律

| # | 问题 | 发现轮次 | 状态 | 证据 |
|---|---|---|---|---|
| C1 | 09-25 c1 完整六档两次被外部干扰打断：一次并行工作流中途 deploy（8738 笔 connection refused 集中于 level_10 稳定窗）、一次 SSH 经控制 EIP 被重置——计量窗需要静默窗口纪律 | 09-25 场景链 | ✅ 已由 09-26 静默窗口补跑闭合 | 场景链文档 §2、底稿一 R-01 |
| C2 | 首轮 b0 跑至 62% 被会话边界终止，整轮作废 | 09-25 场景链 | ✅ 已重跑；教训入账 | 场景链文档 §2 |
| C3 | `lab.sh`/`deploy-role.sh` 三处 `$变量` 后紧跟中文标点，UTF-8 locale + `set -u` 下 unbound variable 中止（含一次退出码失真为 0） | 09-25 场景链 | ✅ 已修（`${VAR}` 形式） | 场景链文档 §6.3 |
| C4 | 数据指纹笔误：压测报告 v1.0 与三份底稿指纹行写作 schema v2.0.0（205 条），实际运行为 v2.1.0（245 条，运行归档 `51cc1dd7` 的 tickets.json 为 245 条，b0 计量 245 笔=全量一轮） | 09-27 复核发现 | 🟡 实验记录 §13/§15/§22 已勘误；报告/底稿 v1.1 待老板决策 | 案例一 §13 勘误注记 |
| C5 | 09-25 首轮 s1 FAIL 一度误归因为「init-host 中途变更污染」——先复现再归因，干净重跑逐位复现后证伪 | 09-25 场景链 | ✅ 已纠正 | 场景链文档 §5 |
| C6 | switch-data 首版仅装载 `order_logistics_perf` 未切 Java 数据源（JDBC 仍指 `order_logistics` 原库），db10+ 档 A 组配对会静默失效——reference 轮因内容同构未受染 | 09-27/28 数据档配对轮 | ✅ 已闭环（dep `.env` 切库 + 强制重建容器 + Java HTTP 跨档特有 ID 交叉验证；reference 轮有效性留证） | 压测报告-数据档配对 §5、AI分析过程记录 §2 #2 |
| C7 | 跨轮口径矛盾：RESAR 编排手册 §7 实录记上轮「Langfuse 压测窗上报状态核对通过（clean）」、压测前检查清单 C 节记「压测计量窗关闭上报」，但上轮（pre s1-b0/s1-stab）与本轮（pre2 四轮+smoke2）共七份 run-manifest 实测 `observation.langfuse_enabled=true`（生效链：`<内部路径>` 取自 agent `/version` → `settings.py:123` 生效配置导出 → `langfuse_exporter.py:276` 对每个 run 异步 flush 上报 obs 机）——文档口径与实现不一致；S2 判 B7 verdict=contaminated（范围限定：Langfuse/观测层读数按受染口径标注，被测机资源层与门禁主路径不受染），历史口径可信度存疑 | 09-29 v1.1 预压测第二轮（S2 §3.2 勘误发现） | ✅ 已勘误（2026-09-30：手册 §7.2 追加勘误小节登记七份 manifest 实测开启、检查清单 C 节改实测口径 + exporter 仪表可量化；正式轮开/关上报口径仍待老板拍板，B7 受染标注在正式轮沿用） | 底稿-S2 §3.1/§3.2/§6.2-U2、手册 §7 实录 L168、检查清单 C 节 L49、七份 run-manifest `observation.langfuse_enabled=true`（实测复核） |
| C8 | run 前置条件提示可改进：首轮冒烟 `20260929-v11-pre2-smoke` 未先 switch-data 即开跑，被指纹闸门拒绝——闸门行为正确拦截、无读数影响（非受染），smoke2 补切档并通过 `/version` 校验后放行；「切档 → `/version` 校验 → 开跑」前置顺序建议在编排前置检查或检查清单显式提示，避免浪费冒烟轮次 | 09-29 v1.1 预压测第二轮（首轮冒烟） | ✅ 已闭环（2026-09-30：lab.sh 本地档缺失与 run-isolated.sh 指纹不符两处报错均已附「先执行 switch-data 切档」提示） | 收窗交接单「预压放行门」行、底稿-S2 §4 数据档行 |

## D. 覆盖缺口（当前仍开放，结论不可外推）

| # | 缺口 | 影响 | 后续动作 |
|---|---|---|---|
| D1 | s1 全部为 2 分钟缩短版，正式长稳未测 | 长时劣化证据不足 | 🟡 部分推进：30min 折中长稳已执行（2026-10-14 v12real1 stab 轮，5,526 笔/合格率 0.9960/末段对首段 P90 +2.6%，证据见 §「2026-10-17 增补」）；全时长（方案 §8.2 公式回填）另窗补测仍开放 |
| D2 | P99 未取（run.json 未含 p99 字段） | 长尾验收项空缺 | ✅ 已闭环（2026-10-14 v12real1：c1 六档 e2e_client_wall_p99_ms 15,865—17,689ms 随 levels-authoritative.json 落盘，载体为 levels 权威件而非 run.json 字段，S1/S3 底稿在用；详证见 §「2026-10-17 增补」） |
| D3 | GATEWAY=0 直连对照未执行 | 网关链路增量未量化 | 排期直连对照轮 |
| D4 | e1 仅 spike 模式，drop 未列入 09-26 轮 | 骤降恢复证据缺 | ✅ 已闭环（2026-10-01 pre3 轮补测 `20261001-v11-pre3-e1drop` gate PASS，老板确认转闭环；证据见 §「2026-10-01 增补」D4 项） |
| D5 | 真实模型仅 real-smoke 校准，Mock 容量绝对值不可外推生产 | 生产容量结论缺 | ✅ 已闭环（2026-10-14 v12real1 真实模型正式轮七轮全 PASS：DeepSeek deepseek-flash、8,917 笔、32.22M token，真实容量读数见收窗交接单与 S1 底稿；详证见 §「2026-10-17 增补」） |
| D6 | 阶段三首字门槛优化未做配对复测（联动 A2） | 上线阻断项 | 优化后配对复测至 30 VU ≥90% |
| D7 | B 组访问模式仅 1 VU 串行（零差异结论限于该口径），热点/缓存效应未在并发档验证 | B 组结论不可外推并发 | c1/hotspot 交叉档复验 |
| D8 | Multi-Agent 故障窗口（AP-MUL-05~08，fault_scenario 注入）本轮未执行（09-27 进程内试跑两条答复边界未过） | 降级答复边界验收缺 HTTP 集成证据 | 另开隔离故障窗口，按 fault_scenario 逐条注入并保留逐笔轨迹 |

## 维护约定

- 新问题追加到对应类别表格，编号不断号；状态迁移只改「状态」列并留日期。
- 每条必须可回溯：证据列指向报告、底稿、实验记录或台账的具体章节，不写无出处的结论。
- 已闭环项不删除，保留作回顾与专栏教学素材（A1/A5、C1—C5 均为现成案例）。

## 2026-09-29/30 增补：v1.1 预压测第二轮（pre2）证据补录与登记说明

> 依据收窗交接单 收窗交接单-20260929pre2 与底稿 底稿-20260929v11pre2-S1—S7（run_set=`20260929-v11-pre2-{b0,c1,e1spike,stab}`，四轮 gate PASS、合格率 1.0，窗口包 4+1 份含 c1-r2）。本小节只补既有条目证据、登记本轮核实说明，不改上文既有条目的历史记录与状态；B11—B15、C7—C8 已按编号规则入表。

### 既有条目补证据（不新建）

- **B3（k6 样本 tags 不含 ticket_id）**：本轮继续行使并扩展近似口径——S3 类别级瀑布、S5 三任务对齐均降级为 `case_family × expected_path_class + 时间窗` 对齐，并新增「trace 末端时刻与 k6 笔完成时刻差 1—2ms」对齐锚（4 笔关键样本逐笔核对）把慢笔样本与 Langfuse trace 一一锚定，结论强度相应降级并全程标注。证据：底稿-S3 §2.1/§5-3、底稿-S5 §0.3/§5-U1。
- **B7（Langfuse ClickHouse 摄取自耗，已规避）**：本轮复检两项规避措施——「四机分离」维持通过（obs-stack 独立机，观测税 promtail ≤0.54%、cadvisor ≤2.22% 单核，与上轮 ≤2.34% 同量级）；但「压测窗关上报」与七份 run-manifest 实测 `langfuse_enabled=true` 不符（见 C7），B7 verdict 按本轮 S2 为 contaminated（范围限定），「已规避」中「关上报」分支待主编辑复核后定状态。证据：底稿-S2 §3.1/§3.2、底稿-S4 §0.2/§2 层④。
- **D2（P99 未取）**：本轮重复确认——run.json `gate.evaluated_from` 无 p99 字段、levels.json 逐档 `e2e_client_wall_p99_ms` 与 `e2e_server_p90_ms` 均 null（聚合层形态另见 B14）；S1/S3 以逐笔经验重算行使（逐笔重算 P90 与官方聚合逐档全等、差 <0.6ms），重算 P99/max 不可当 k6 官方聚合值引用。证据：底稿-S1 §6 D2 行、底稿-S3 §1 脚注、收窗交接单缺口 3。

### 本轮已核实、暂不另立条目（附理由）

- 各轮无独立 `k6-stop.json` 停止确认文件、本地副本无独立归档回执：以 coordinator `run_finished k6_exit_code=0` 与 `.tar.gz` 归档件佐证正常收窗（四轮口径一致），属产物齐套性观察项，是否入 B 类待主编辑定夺（收窗交接单缺口 1）。
- `manifest.data.runtime_load_verified=false`：数据档声明指纹已核验、运行时装载读数未核验，四轮窗口包 unknowns 已按 V09 登记补证路径，对本轮门禁结论无直接影响。
- `baseline_commit/gitHead=unknown`（版本以 target fingerprint 锁定）、c1 各档计量窗仅 2 分钟（H3 拐点判定证据不足，由 S7 复测单 R1 承接）、D1/D5 既有边界：均为既有条目或底稿 unknowns 承接项，不重复登记。

## 2026-09-30 补登：控制实验轮（s7ctl）R1 中断轮

> run_set=`20260930-v11-s7ctl-*`（R1 c1x 加档轮、R2b stabnolf 对照轮及拆分/单档变体）；B16、B17 已按编号规则入 B 类表格。本小节登记既有条目状态维持与复跑注记，不改上文既有条目的历史记录与状态。

- **R1 中断轮门禁口径**：`20260930-v11-s7ctl-c1x`（八档加档）与拆分轮（LEVELS=50,70；LEVELS=50,100）因 B16 施压机容量墙中断，run 未完成、未产出 run.json/采集包，按 NOT_VERIFIED 待窗口核验员窗口包（路径 `<内部路径>`，产出中）登记后归档；同日单档 LEVELS=70/100 变体（`20260930-v11-s7ctl-c1-70/-c1-100`）与 R2b（`stabnolf`）正常完成收窗（采集副本与隔离快照在档 `<运行时路径>`）。
- **C8（run 前置条件提示）**：本轮无新证据，状态维持。
- **B11—B15/C7**：无状态变化（C7 历史口径复核仍待主编/老板拍板）。复跑注记：R1 及拆分轮因容量墙中断未产出读数，与上述条目现象无交集；R2b（`LANGFUSE_ENABLED=false` 对照）已正常完成，本轮复跑期间未见上述条目相关现象的新增记录——R2b 读数与 B13（exporter 开销）/C7（受染标注）的关联判读待 S7 复测流程出稿后回填，本清单仅注记不判读。
- **实例恢复留痕**：容量墙中断后经 pt-control 实例重启两次恢复（主编留痕，本地无独立日志；恢复后单档对照轮正常完成可作运行侧旁证）。

## 2026-09-30 修复轮（R3 工程包 + B16 扩配）：B11—B17/C7/C8/D2 闭环记录

老板拍板「修复这些发现的问题」（B16 选扩规格、放行短冒烟验证、正式轮上报口径暂不拍板）后一轮完成：

| 修复面 | 内容 | 验证 |
|---|---|---|
| k6 套件/编排器（零重建） | B14 measured 回填、D2 p99（--summary-trend-stats）、server_p90 子指标物化、B15 judgements.ndjson 逐笔明细、B3 逐笔身份（经 judgements 的 ticket_id+ts，弃 tags 高基数方案）、B12 采样宿主、P2 WARMUP_SEC 预热段、C8 切档提示 | selfcheck 68 断言全绿；unittest 新增 2 用例；既有 run 只读重放 diff 通过；冒烟 c1 轮实测三字段非 null |
| 服务端（R3，指纹变化） | B11/G1 admission 仪表、B13 exporter flush 仪表、P5 Trace 真实区间（decision start_wall、final_answer 真实时长） | 单测 150+集成 8 全绿（含 3 新 generation 用例与 /metrics 断言）；冒烟实测 admission/exporter 采集、Langfuse generation latency p50=633/max=3213ms 非 0 |
| 云资源 | B16 pt-control 升 c9i.xlarge（terraform+lab_meta 同步）；管理出口加白第四 IP（<IP>，出口漂移处置）并同步 lab.env 四值防 gateway 回退 | nproc=4/内存 7526MB 实测；变配后 SSH/网关/容器自愈核对；新有效小时价 3.1276 元入台账 |
| 文档/工具 | C7 手册 §7.2 勘误 + 检查清单 C 节实测口径；B17 B6 预检提示；取数隧道自检（prom/lf-pull 自动 tunnel.sh up）+ tunnel.sh B10 探测重试 | 断隧道实测自动建链取数成功；tunnel.sh bash -n 过 |

修复验证轮：`20260930-r3smoke-{b0,c1,s1r3}`（新指纹 run_set，不与旧读数混比、不建基线；s1 首跑因 goja 不支持对象字面量拼接键语法失败、运行链路闸门拦截一次未同步，均修正后通过——闸门行为正确）。注：judgements.ndjson 为 k6 logrus 壳内嵌 JSON（`msg="{...}"`），解析需取 msg 内层并反转义。

## 2026-10-01 增补：预压测第三轮（pre3，20261001-v11-pre3 复刻验证轮）证据补录与登记

> 依据收窗交接单 收窗交接单-20261001v11pre3 与底稿 底稿-20261001v11pre3-S1—S7（run_set=`20261001-v11-pre3-{b0,c1,e1spike,e1drop,stab}` 五轮 gate 全 PASS + 放行门 smoke/c1low 两步，窗口包 7 份全 ready，入库证据包 7 份 verify-evidence 7/7 PASS、48/52 checks 零失败）。本小节由 pt-defect-recorder 依 S6 §4 缺陷候选记录（DC-A/B/C/D）与 S1—S7 底稿登记：B18—B22 已按编号规则直接入上文 B 类表格（维护约定「新问题追加到对应类别表格、编号不断号」，清单保持单一事实源，不另建并行增补文件）；此处登记既有条目补证据、D4 建议闭环与不另立条目项。本轮全部读数为预压测复刻验证口径，不构成正式结论。

### 既有条目补证据（不新建）

- **B3（k6 样本 tags 不含 ticket_id）**：本轮对账主键经逐笔 Eval 通道（`raw/judgements.ndjson` 逐笔含 ticket_id+七项判定+质量明细）实质闭合——S3 逐笔直接配对（五轮 6920 笔与 k6 Point 笔数逐轮全等）、S5 同工单配对（stab 190 对 + c1 5561 对，`|server 差|`max 26.1/31.4ms）；k6 samples tags 本身仍无 ticket_id（缺口残留，但不阻塞跨源逐笔对账）。是否改判「部分闭环」待主编辑定夺。证据：底稿-S5 §0.2/§1.2、底稿-S3 §0。
- **B7（Langfuse 摄取自耗，已规避）**：本轮 S2 §3 判 verdict 维持 contaminated（范围限定）——本轮 manifest 缺 observation 段（MV 同源缺口），上报状态无直接实证，按开启从严判读；S4 §2 复核补强：obs 机入站在异常窗时点处全程最低位（40.9—83.0KB/s，无同步突发）、flush 速率=terminal 完成速率（无独立突发）、obs 机 L50 计量窗 62.8% CPU/load1 8.29 与流量水平相关且不在被测路径。受染范围（Langfuse/观测层读数）与不受染范围（被测机资源层、e2e 主路径、门禁）维持 pre2 结论。证据：底稿-S2 §3、底稿-S4 §2。
- **B11（agent 观测仪表，已闭环）**：R3 已落地部分本轮实测复用正常——`agent_request_admission_seconds` 五轮全程采到（异常窗时点 admission 0.09—0.45ms、无排队异常）；但 pre2-S7 P1/G1 的 collab 队列计数器部分实测未合入本轮被测版本（`metrics_sample` 字段集与 pre2 相同、指纹未变 b0ca8191aaba2dfc），仍为 P-G1 工程项待排期（S5 §4 附表、S6 §6 #2）。B11 闭环结论不变，未竟部分挂 P-G1。证据：底稿-S4 §1 层②、底稿-S5 §4、底稿-S6 §6-2。
- **D2（P99 未取）**：缺口范围收窄——run.json 聚合层 p99 官方字段仍缺（继承）；但 c1 `levels.json` 档级 p99 官方值已产出（六档 5803.38—5821.0ms，逐笔重算与官方全等差 <0.1ms；B14 修复后物化），b0/e1/stab 轮级 P99 仍为逐笔重算口径不可当官方聚合引用。证据：底稿-S1 §3/§7-1。

### 建议闭环（待老板/主编确认后改状态）

- **D4（e1 仅 spike 模式，drop 未列入）——本轮已补测，建议转闭环**：`20261001-v11-pre3-e1drop`（PATTERN=drop，参数对称 spike：high 20VU/low 1VU/PHASE 20s）D4 首测 gate PASS、合格率 1.0、错误 0、E2E P90 5682.6ms；drop 后 low 相位 7 笔 diff 6.1—34.0ms 稳态阴性（S5-E5）、全 qualified——骤降恢复证据实质闭合。老板 2026-10-01 指令「继续完成待办」确认为闭环确认，D4 状态已改「✅ 已闭环（2026-10-01 pre3 轮补测）」，后续动作指向窗口包与 S1/S5/S5r1 证据。证据：窗口包-e1drop、底稿-S1 §1/§2、底稿-S5 E5、底稿-S7 §1.2。

### 本轮已核实、暂不另立条目（附理由）

- **勘误候选 ERR-1**：S3 §1.2 AW1 族分布记录「compound=2、合计 19」与 S5 §1.3 逐笔明细「compound=3、合计 20」差 1 笔——S6 §0-8 按逐笔明细复核属实，受染 20 笔=阶跃 VU 数的主结论不受影响；属上游记录笔误级，交主编勘误，不属缺陷条目。
- **obs 机 L50 计量窗高负载（62.8% CPU/load1 8.29）**：观测层自身容量信号，与流量水平线性相关、不在被测路径；S4 §4 缺口 5 已登记「obs 机容量基线与告警」补测路径——是否立 B 类条目待主编辑定夺。
- **S4 §4 其余监控缺口**（层②10s/层④15s 采样粒度对 1—6s 异常窗宽、Prometheus 无 k6 remote-write 序列、进程内 exporter 开销不可单列、cAdvisor 15s+rate[30s] 平滑）：补法明确（采样加密/remote-write/关上报对照轮/缩短抓取间隔），多为正式轮执行参数选项，暂不立条目（S4 §4 表 1/2/6/7）。
- **既有承接项**：baseline_commit=unknown、runtime_load_verified=false（7 轮同）、c1 各档计量窗 ~2 分钟与 stab 2 分钟（D1 同源）、成本计价 2 元/千 token 演示口径、D5 Mock 不外推——均为既有条目或底稿 unknowns 承接项，不重复登记。

## 2026-10-01 增补：正式轮（20261001-v11-formal，D4 实验室口径）缺陷登记与申请单

> 依据收窗交接单 收窗交接单-20261001v11formal 与底稿 底稿-20261001v11formal-S1—S7（run_set=`20261001-v11-formal-{b0,c1,e1spike,e1drop,stab}` 五轮 gate 全 PASS + 放行门 smoke2/c1low 两步；工程轮 formal-smoke NOT_VERIFIED 留档；窗口包 7 份 ready、入库证据包 8 份 verify-evidence 8/8 PASS、OSS 回执 8/8 verified）。本小节由 pt-defect-recorder 依 S6 §4 缺陷候选（DC-B/C/E/F + TC-1/2/3 + 口径修正项 + 已闭环登记共 9 项）与 S1—S7 登记：A8、B24—B28 已按编号规则直接入上文表格（维护约定「新问题追加到对应类别表格、编号不断号」，清单保持单一事实源）；B23 定性修正为既有条目变更，已同步改入 B23 行。本轮读数为 D4 实验室口径正式结论，报告发布待老板最终确认。

### 既有条目补证据与延续复现（不新建）

- **B18（DC-E 延续，B18 修复后收敛未竟）**：本轮 board.js B18 同域修复首证生效——`goodputScope`/`costScope` 显式标注，b0/smoke2/stab 的 board 读数与 002 契约口径完全一致，pre3 三处混合口径错误读数（e1drop 5.338、c1 单笔成本 5.290 元）不再出现；但 **c1 聚合 Goodput 三口径仍并存**（board total-run 3.6006 / 002 包络 3.8116 / 逐档合计 5.2404），下游引用须声明口径（S1 推荐：包络口径用于端到端叙事、逐档合计用于容量叙事）——聚合器按场景类型收敛分母口径并单列输出未竟，P-DC 继续。证据：底稿-S1 §3.1/§7-H5、底稿-S6 §4 DC-E。
- **B19（DC-B 延续，formal 复现）**：e1 计量窗边界 gauge 末值语义本轮代码级确认（S2 §6.2）并同型复现——e1drop run.json 计量窗 24.4s 窗内仅 27/124 笔（AW3b 两笔 13:10:46.8 在窗起点 13:11:00.207 之前）、e1spike 窗内 125/130；b0/stab 无此问题（start gauge 三值全等）。以分相位读数为准，修复并入 P-DC。证据：底稿-S2 §6.2、底稿-S3 §1.3。
- **B20（DC-C 延续，formal 复现）**：本轮 c1 levels.json 档级 gate 视图仍误报 NOT_VERIFIED（run 级 gate PASS），以 run 级为准——与上行 B20 行内「仓库新版已实证」注记的张力如实登记（正式轮采集链路产出的档级视图仍不含 run 级 manifest 归属），档级视图与 run 级证据归属未打通的根因面仍在，P-DC 继续。证据：底稿-S1 §7-H5、底稿-S6 §4 DC-C。
- **D4（维持闭环）**：本轮 e1drop 同参复测 gate PASS、合格率 1.0、错误 0、E2E P90 5687.2ms；drop 后 low 相位 7 笔 diff 6.1—34.0ms 稳态阴性（S5 E5）——闭环状态维持（2026-10-01 pre3 轮已确认）。
- **B7（受染标注升级为实测）**：本轮 manifest observation 段 8/8 轮实测 `langfuse_enabled=true`（/version 一手探测），受染范围限定延续且依据由「从严判读」升级为「实测开启」；obs 侧负载随档位水平（峰值 61—65% 在 L50 计量窗）、无异常窗同步突发。证据：底稿-S2 §3、底稿-S4 §2。
- **B22（DC-D 已闭环，本轮首证）**：lab.sh 全量采集链行使——build fingerprint 文件/lab.sh 运行清单（MV-1/2）、stage_pause_others 全量行使+隔离快照 OSS 回执（MV-5）、manifest nodes+observation 段（MV-6/U1）、deploy-role 重部署窗前完成（MV-4）均正式轮首证；B22 建议改判「✅ 已闭环」待主编辑确认。证据：底稿-S2 §1/§3/§4、底稿-S6 §4 已闭环登记行。
- **B3（闭合维持）**：judgements ticket_id 8/8 轮实核在位 + Langfuse metadata.task_id 全量可寻址（692/692）——跨源逐笔对账双通道闭合；k6 tags 本身无 ticket_id 的缺口残留不变。
- **D2（缺口收窄维持）**：run.json 聚合层 p99 官方字段仍缺；本轮 P99 官方值改以 k6-summary.json metrics 子指标 p(99) 引用（b0 5804.48 / c1 5824.73 / e1spike 6176.77 / e1drop 6238.88 / stab 5820），逐笔重算互核差 ≤1ms——D2 维持「收窄」表述。

### S6 §4 工具链残留 9 项消费对照（本登记的对照表）

| S6 §4 残留项 | 消费方式 |
|---|---|
| DC-B e1 计量窗 gauge 末值语义 | B19 补证据（上节） |
| DC-C 档级 gate 视图 | B20 补证据（上节） |
| DC-E c1 聚合 Goodput 三口径并存 | B18 补证据（上节） |
| DC-F pt-control 无 cadvisor | 新立 **B26** |
| TC-1 traces.all 深翻页丢页 + limit≤100 | 新立 **B27** |
| TC-2 列表级 metadata=null | 新立 **B28**（①） |
| TC-3 byId 无 observations 子树 | 新立 **B28**（②） |
| 口径修正项 CAL-1（非缺陷） | 不立条目——L50 两波结构表述修正（首波 32 + 复用波 17、破缺深度 28.4%），正式报告按修正口径表述 |
| 已闭环登记 | DC-D（B22）本轮闭环首证（上节）+ B24/B25 过程缺陷当窗闭环（已入表） |

### S7 配对复测/补证申请单登记（S7R-4—S7R-7，全部待老板拍板；本清单只登记不催办、不代跑）

| 单号 | 动机 | 口径（唯一改动；其余与正式轮全同） | 预期判据 | 成本估算 | 状态 |
|---|---|---|---|---|---|
| S7R-4 | H3 ≥L20 边际轻衰减本轮口径复证（承 pre3-S7R-2、S5 REQ-2；跨轮 s7ctl R1 已证伪） | c1 单档 L70 与 L100 两轮（执行前核对 pt-control 规格与施压机容量墙 B16；Langfuse 保持开启） | P90 平坦 + Goodput 超线性 → 跨轮证伪在本轮口径复现，H3 改判 refuted（跨轮+本轮双引用）；P90 抬升/亚线性 → 升级评估膝点 | 单档轮 10—15 分钟 ×2 + 收窗 | 待拍板（默认不执行） |
| S7R-5 | H6 全时长 soak 回填 + A8 内存爬升定向（承 pre3-S7R-3、S5 REQ-3） | s1 全时长（§8.2 公式口径）20VU；窗内开启容器内存 + python_gc 指标联动采集 | 早/中/晚分段无递增劣化；内存爬升长窗内趋平台（GC 惰性回收）或线性增长（泄漏嫌疑升级） | ≈§8.2 公式时长 + 收窗 | 待拍板（正式验收基线前置项） |
| S7R-6 | 真实模型 real-smoke 校准窗（D5 前置，任务指定新增） | provider 切真实模型（唯一改动），b0/冒烟级迭代数 + 20VU 短稳态窗；不判门禁只采基线读数 | 真实模型延迟谱/Token 谱/流式 chunk 节奏基线，与 Mock 谱差值定量 | 冒烟 + 短稳态 ≈20—30 分钟 + 收窗 | 待拍板 |
| S7R-7 | H2/H4 前链路段内分解仪表（非新压测，承 S5 REQ-1、S6 未排除 #1；DC-F/B26 一并闭合） | 网关/前端 Nginx 开启 request_time/upstream_response_time 日志或 stub_status（含 pt-control 部署 cadvisor）；k6 导出 per-request 发射时间戳 | 网关 request_time ≈ diff 全量 → 排队在网关前；upstream_response_time 显著 → 排队在前端→API 段；H2 判级具备完整闭环条件 | 仪表改动 0.5—1 工作日（不占压测窗） | 待拍板（工程项） |

### 本轮已核实、暂不另立条目（附理由）

- **obs 机容量基线（S4 缺口 5）**：观测层自身容量信号（L50 计量窗 61—65% CPU/根 cgroup 2.23 core，ClickHouse 摄取链为大头），不在被测路径；沿用 pre3 判断不立条目，补测路径挂 P-OBS。
- **S4 其余监控缺口（缺口 2/3/6/7/8）**：采样间隔 1—2s、k6 remote-write、exporter 开销单列（对照轮人工拍板）、内存+python_gc 联动、Langfuse metadata/ticket_id 通道——补法明确，多为正式窗执行参数或工程项，挂 P-OBS 与 S7R-7，不逐项立条目。
- **隔离快照本地副本未含（S2-U1）与 coordinator 无隔离事件流（U2）**：OSS 回执 8/8 verified + hashes 本地实算 8/8 全对 + 容器层序列整窗缺席三重替代登记；collect 补快照同步与 stage_pause 事件写入挂 P-OBS。
- **L50 后续波 17 笔 trace 未配对（S5-U1）**：同 ticket 稳态重放密集带下唯一分配保守放弃，两波结构由 judgements 波次 + 32 条首波 trace 支撑，低风险登记。
- **e1drop 4 笔超小样本池稳态 max +2.9—7.7ms（S3/S5-U4）**：池内 steady 仅 104 笔的基线噪声量级，不推翻「服务端零抬升」主判定。
- **git_dirty_at_run=true（8/8 轮）**：云端 case 快照含 B18 修复未提交所致，代码一致性以三重指纹（/version 指纹 + build 指纹 554dfd8a… + baseline_commit 3e23df46fc5d）锁定；云端提交后消除。
- **pt-control 14.7% CPU 瞬时三处（S4 §5-2）**：时点均在轮间/启动沿（k6 生命周期活动），窗内负载 1.3—5.1%、load1 ≤0.37——未分解的观察项（无 pt 容器指标，B26），登记不硬凑。
- **受染批斜率跨轮微增（~25→~28ms/VU）与 L50 两波结构**：量化已完成、形态复现；破缺机制未证（S5-U3 从严声明），判别依赖 S7R-7/REQ-2，不作缺陷条目。

## 2026-10-11 增补：跨环境复刻轮（20261011-v11-formal2，场景 A 恢复重建环境）问题登记

> 战役定位：环境 2026-10-03 销毁、10-11 场景 A 全量重建后，按 formal 同参数完整复刻七轮（老板指令"重新完整跑一遍压测"+方案批准"执行方案"）。**七轮全 PASS、跨环境确定性复现第四次成立（P90 偏差 ≤0.47%、平台档 ≤0.11%、Token 三轮逐位全等）**——过程问题全部为环境重建系（B29—B33），无被测系统新缺陷（A 类零新增）、无门禁/聚合器新缺陷（既有 B 系列零复发：B24 修复保持、B25 修复保持、B23 竞态成分本轮未观察）。

### 环境重建系五条（B29—B33，全部当窗处置闭环或口径消化）

见 B 表新条目：B29 ACR 钉版镜像源码漂移（复现叙事的最大威胁，指纹核对纪律拦截）、B30 extend 覆写自跳补丁、B31 quote.json 通道缺口、B32 baseline_commit 血统断裂、B33 mcp.ops.json 观测取数断链。共性根因：**恢复通道（recovery runbook）产出的环境与 `up` 通道产出的环境存在隐性差异面**——本_campaign 逐项暴露并登记，恢复 runbook §6 的改进候选取材于此。

### 本轮已核实、暂不另立条目（附理由）

- **管理出口 IPv4 漂移（恢复时 `.163` → 压测窗 `.104`）**：老问题重演（历窗已四漂），三层加白 runbook 处置成熟（SG/admin-acl/lab.env 成套），不立新条目；但注意 `ifconfig.me` 同域会随机返回 v6（`curl -4` 亦不保证），出口探测一律用 `ipv4.icanhazip.com`。
- **L782 多 CIDR 渲染缺陷二次触发**：恢复窗已登记（踩坑②），本轮 `gateway --production` 再次走该路径——**当窗对 pt-control 云侧副本打了拆行补丁**（`cidrs=$(printf … | tr ',' '\n')` 逐行渲染），gateway 成功即补丁生效的实证；仓库脚本修复仍待授权（改进候选不变）。
- **`replay_golden.py` 容器外运行约束**：探针硬编码 `127.0.0.1` + `CASE_ROOT=HERE.parents[2]` 目录深度依赖——宿主机无 fastapi 时不可直跑，当窗以 docker cp 进 cs-agent-python 容器、按案例树层级摆放后对运行中服务回放（48/48 PASS）；用法属工具文档级知识，不构成缺陷。
- **pt-control 补装 aliyun CLI+凭据（DV-E7）**：重建机无历史工具（老实例随窗销毁），当窗按台账踩坑 alicdn 版本化 URL 通道补装；属环境重建固有成本，已登记场景矩阵偏差表。
- **stab 笔数 10103 vs formal 10096（+0.07%）**：时长界定场景的窗口边界抖动（两轮计量窗 1804.0s vs 1804.9s 同量级），单笔 Token 逐位一致（1768.70）——非缺陷，对照表 §四 已按口径登记。
- **c1 L1 档 P90 偏差 −5.91%**：35 笔小样本档固有抖动（formal↔pre3 的 L1 本身差 3.0%），跨环境对照不作结论依据——对照表 §三 登记为已知小样本抖动档。

### 老问题零复发清单（本轮验证面）

B16 施压容量墙（六档 116 VU 预分配 4C8G 全程稳定）、B24 tarfile 执行位（smoke2 首跑即 PASS——修复保持）、B25 collect 死锁（7/7 collect 一次通过——`-f` 修复保持）、B18 board 口径（窗口包/底稿引用全走 002 契约口径）、B27/B28 Langfuse 翻页（本轮走 Public API 全量分页零缺页，未触 tRPC 通道）。

## 2026-10-12 三方对照复刻轮批次（B34—B38）

> 战役定位：10-12 场景 A 二次恢复常驻环境上第三场完整战役（老板指令「再次在阿里云压测环境跑一遍完整的压测，注意数据需要符合专栏的需要」+ 方案批准，七轮同参复刻）。**七轮全 PASS、链路复现第五次成立**（全轮 P90 偏差 ≤0.33%、stab 0.00%、迭代数界定轮 Token 三方逐位全等、c1low=formal 与 c1=formal2 各自逐位）——过程问题全部为环境恢复系与操作面（B34—B38），**无被测系统新缺陷（A 类零新增）**。完整数字见三方配对对照表与收窗交接单。

### 老问题零复发清单（本轮验证面）

B16 施压容量墙（六档 116 VU 预分配 4C8G 全程稳定）、B24 tarfile 执行位与 B25 collect 死锁（7/7 collect 一次通过——修复保持）、B29 指纹漂移（本轮完整行使 rebuild-agents+指纹核等放行纪律，一次假漂移系操作纠偏 B38 非 ACR 钉版）、B30/B31（2026-10-12 脚本级修复首次跨窗实测生效：control_ip 自跳与 quote 自动补报价零触发）、B32（DV-E3 口径消化延续，当窗 git init 基线 `2415c514…`）；B27/B28 Langfuse 翻页（Public API 全量分页 16684/78567 零缺页）。

## 2026-10-17 增补：v12real1 战役状态勘正（D1/D2/D5）与 AW-O2 时间窗勘误联动

> 依据压测收尾阶段交接单-20261014v12real1与 S 系列底稿（run_set=`20261014-v12-real1-{smoke2,c1low,b0,c1,e1spike,e1drop,stab}`，七轮 gate PASS、error_rate 全 0）。本小节按 2026-10-17 批审意见（第一步数据完整性审计）勘正 D1/D2/D5 的历史登记——三条登记时点均早于 v12real1 战役，属「历史登记未更新」，非本轮仍缺失。

### D1 转部分推进

- 30min 折中长稳已执行：v12real1 stab 轮（run_id `131055-20261014-v12-real1-stab`）5,526 笔、合格率 0.9960、末段对首段 P90 +2.6%（R-G4 ≤20% 判据远未触线），读数见底稿-S1 §3.4。
- 全时长长稳（方案 §8.2 公式回填目标时长）仍未执行——D1 保持开放，列批审意见 P0 补采项。

### D2 转已闭环

- v12real1 c1 六档 `e2e_client_wall_p99_ms`（15,865—17,689ms）已随 `<内部路径>` 落盘（2026-10-17 随 B20 权威件补采入库），S1 §3.2／S3 底稿在用。
- 口径说明：载体是 levels 权威判定件，run.json 本身仍不含 p99 键——原缺口描述保留作历史。

### D5 转已闭环

- v12real1 真实模型正式轮七轮全 PASS（DeepSeek deepseek-flash、8,917 笔、32.22M token、实际计费 ¥39.10），真实端点容量读数已成体系（收窗交接单 §1—§4、S1/S3 底稿）——「真实模型仅 real-smoke 校准」前提不再成立。

### AW-O2 时间窗勘误联动（同批批审意见）

- S1/S4 底稿 2026-10-17 已勘误：AW-O2（stab 桶 6）时间窗为 ~13:00—13:05Z（按 5 分钟桶键 5973276×300s，桶 6 起点 13:00:00Z；原标注 12:55—13:00Z 属桶 5 区间）。
- 本批同步修正两处引用：专栏第 16 讲正文「波动窗里的账在服务端」条目、`pending/配套材料/14-20讲换锚影响面清单-20261016.md` 异常窗表 AW-O2 行。第 14 讲正文观察项表已在 10-17 三轮审查修复中改为新口径，无需再动。
### 数据完整性审计未销项补登（同批批审/复审意见 P0/P1）

上轮第一步数据完整性审计除 D1/D2/D5/AW-O2 外的未登记项，统一落点如下（登记不等于排期，补采按批审 P0/P1 优先级另窗执行）：

- **Loki 日志未随窗抢收**（管道可用、7 天保留）——窗口外错误栈/慢 SQL 聚类不可复现，列为下窗收尾固定动作；
- **校准窗原始 calib 证据未入证据包**（H1 首字 93%、SLO 20000ms 的原始证据链断在 OSS）——下窗打包补采或登记 OSS 对象清单；
- **<运行时路径> 分析聚合件未哈希入库**（s1/s3/s4/s5-agg 等，清理后无法复算）——按「路径＋SHA-256」最小化留痕，B39 同源缺陷已实证其风险；
- **Guardrail/安全护栏判定证据缺**（run.json 安全证据只指事件文件，非护栏判定）——Goodput「安全」项无数据，待判定链补齐；
- **质量语义评测未执行**——口径 B 约定本轮不宣布质量结论，维持登记、不排期。

