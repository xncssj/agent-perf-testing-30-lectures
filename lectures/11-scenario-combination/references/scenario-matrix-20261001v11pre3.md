# 战役 20261001-v11-pre3 场景矩阵（公开脱敏版）

> 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。

> campaign_id：`20261001-v11-pre3` ｜ 角色：`pt-scenario-designer`（专栏第 11 讲「设计测试场景」）｜ 产出时间：2026-10-01
>
> **文件性质：准备阶段产出（准备包字段之一）**。本文件把需求侧目标转为逐场景可执行矩阵；数据生成由 `pt-data-preparer` 负责、脚本参数落地与自测由 `pt-script-designer` 负责，本文件不代行。门禁阈值与 SLO 数值一律引用既有权威来源（002 契约 / `k6-suite/lib/contract.js`），本文件不新设、不改写；场景定义与性能测试方案 v1.0 §8 冲突时以方案为准并上报主智能体。
>
> 权威依据：方案 §8（压测设计）/§13（执行顺序）、k6压测执行多Agent编排手册 v1.0 §1/§8、压测前检查清单 v1.0 C 节/D 节、压测报告-预压测第二轮-20260929pre2（复刻基线，负载参数实值抄录源）、pre2 窗口包 5 份（数据绑定实读源）、需求追踪草稿-20261001v11pre3（上游交接）。

## 0. 上游先验结论（动作序列第 1 步）

| 先验项 | 结果 |
|---|---|
| `requirements_ref` 有效 | 是（`plans/战役-20261001v11pre3/需求追踪草稿-20261001v11pre3.md` 存在且为 2026-10-01 版） |
| 需求字段齐备 | 是：3 项验证性目标（R-V1/V2/V3）+ 5 项场景目标（R-S1—R-S5）+ 9 项判定/观测目标（R-G1—R-G9）逐条带来源；约束四清单（质量/时延/成本/安全）齐 |
| 目标有来源 | 是：`slo_refs` 五源齐（方案 §5、<内部路径> §1、002 契约 §3—§5、contract.js、pre2 §2.1）；阈值无新设 |
| unknowns 登记齐 | 是：7 项均含 missing/impact/where_to_get |
| 场景范围拍板 | 老板已拍板：复刻 pre2 四轮 + 补 e1 drop = 5 轮，执行序 b0 → c1 → e1spike → e1drop → stab，上一轮 collect 成功才发下一轮 |
| 需求侧遗留歧义 | unknowns-7（stab 是否启用 WARMUP_SEC）：统筹已拍板沿用 pre2 原口径 `WARMUP_SEC=0`（见 §4 偏差记录 DV-1），本轮就该条闭环 |

**先验结论：上游通过，不退回，进场景矩阵编制。**

## 1. 场景矩阵（本轮 5 轮，阶段一）

run_id 命名规则：`20261001-v11-pre3-<场景>`。执行序即下表行序；每轮 `collect` 归档成功（退出码 0）后才发起下一轮（铺底方案 §7.3 串行纪律）。负载量纲一栏数值**全部从 pre2 报告 §3.0/§4 与 pre2 窗口包抄录实际执行值**（非脚本默认值），e1 drop 例外（本轮新增，参数按统筹拍板对称 spike，见 DV-2）。

| # | scenario_id | 类型 | 负载量纲（pre2 实际值，抄录源见注） | 输入数据池（v1.1 reference 档） | 监控需求 | 停止条件 | 验收来源 | 矩阵增量（阶段二/三） |
|---|---|---|---|---|---|---|---|---|
| 1 | `20261001-v11-pre3-b0` | 基准（非流式 k6 口径） | 脚本 `b0.js`：**VUS=1 / ITERS=33 / WARMUP=3**（per-vu-iterations：1VU × 36 迭代 = 3 笔 warmup + 33 笔 measured；pre2 计量窗实测 116.462s，P90 5598.6ms）。仅普通基准（预热后计量）；**冷启动独立运行（WARMUP=0）不在本轮复刻范围**（pre2 亦未执行） | `fixed-stage1.json`（sha256 `18010cfa1ca4…3e8245b`）；池容量 2000 条（v1.1 pool-size 2000），本轮抽取 3 预热 + 33 计量笔轮换，**非全量、非 2000 满额**（pre2 复刻采样量） | 双路：pt-env-monitor（/version、/metrics、Prometheus/Grafana、宿主资源、日志 ERROR 环比）+ pt-business-monitor；看板 Grafana `agent-overview` / `run-quality` / `k6-loadtest` / `lab-resources`；编制=主+A+B（手册 §8） | §3 熔断线（清单 D 节 5 条，任一触发即暂停保留现场） | 002 三态门禁（`contract.js`：合格率 ≥0.90 / 错误率 ≤0.005 / 客户端墙钟 E2E P90 ≤10000ms，等值边界即达标）；方案 §8.2 基准场景、§8.4-6 冷热分立原则（本轮仅热路径） | 本轮无（阶段一单阶段口径；阶段二/三增量在正式轮另表） |
| 2 | `20261001-v11-pre3-c1` | 容量（阶梯加压） | 脚本 `c1.js`：**LEVELS=1,5,10,20,30,50**（六档）× 每档 **STAB_SEC=60 + MEASURED_SEC=120**（3min/档 = 1min 稳定 stabilization 不入分母 + 2min 计量 measured），**LOAD_MODEL=vu**（封闭模型 constant-vus，level=并发数）；pre2 包络 16:00:55.221—16:18:00.864Z，六档全 PASS（Goodput 0.2821→13.3403/s，P90 5510.4—5773.0ms）。聚合窗 `measured=null` 为该场景已知形态 | `load-stage1.json`（sha256 `da39b6cc…3109d14`，**c1 生成式负载文件**）；池容量 2000 条，六档合计计量约 3912 笔（pre2 实测 34/175/347/679/1001/1676）跨池轮换 | 同上；另档间切换需业务读数看护（编制=主+A+B+D，手册 §8）；看板增加 `gateway` / `java-service`（依赖层观测） | 同 §3 熔断线；另施压机容量预检（清单 B6：多档总 VU 池 >100 先小轮试探；本轮六档合计 116 VU 预分配，2026-10-01 施压机已扩 4C8G，仍须试探确认） | 002 三态门禁逐档判定；方案 §8.2 容量场景（策略 ①—⑥）+ 检查清单 C 节；**容量结论只引用逐档摘要（levels.json），跨档汇总仅趋势展示（B14/DC5）**；本轮预压口径**不判容量拐点**（D1/D5） | 本轮无 |
| 3 | `20261001-v11-pre3-e1spike` | 异常（流量突刺） | 脚本 `e1-traffic-change.js`：**PATTERN=spike / BASE_VUS=1 / SPIKE_VUS=20**（pre2 报告 §3.0 显式记录「1VU 基线 + 20VU spike」）；**PHASE_SEC=20**（pre2 报告与窗口包未显式记录该值，按脚本默认 20 取值，与 pre2 计量窗 43.371s ≈ 三相位 60s 结构的自洽形态一致，**执行窗开跑前核对**）；相位 pre(1VU)→spike(20VU)→post(1VU) 三段独立判定 | `load-stage1.json`（sha256 `da39b6cc…3109d14`，**e1 生成式负载文件**，与 c1 同池同哈希）；pre2 计量窗 125 笔（全轮 130 笔） | 同上；编制=主+A+B+D；spike 相位读数与注入/恢复时刻、影响面、恢复时长逐项记录（方案 §8.2 异常场景要求） | 同 §3 熔断线；另**相位样本量小，单笔失败即触限**（手册 §8 e1 专属注意），失败逐笔归因 | 002 三态门禁（相位级）；方案 §8.2 异常场景 + §12 缺口 9；判读对照 pre2 §3.5（spike 相位尾延迟增量、尖峰 5.2s 收敛先例） | 本轮无 |
| 4 | `20261001-v11-pre3-e1drop` | 异常（流量骤降）**本轮新增首测** | 脚本 `e1-traffic-change.js`：**PATTERN=drop / SPIKE_VUS=20（高压段 high）/ BASE_VUS=1（低压段 low）/ PHASE_SEC=20**——统筹拍板参数对称 spike（同负载档、同持续时长，仅 PATTERN=drop，见 DV-2）；相位 high(20VU)→low(1VU) 两段独立判定；摘要键 `e1-drop`；执行资产已存在（`PATTERN=drop` 分支已实现） | `load-stage1.json`（sha256 `da39b6cc…3109d14`，与 e1spike 同池同哈希，保证 drop/spike 相位级可比） | 同上；编制=主+A+B+D；注入（骤降）与恢复时刻、影响面、恢复时长逐项记录 | 同 §3 熔断线；相位样本量小、单笔失败即触限同 e1spike | 002 三态门禁（相位级）；方案 §8.2 异常场景 + §12 缺口 9（D4 补测）；**无同型先例（unknowns-6）**：形态判读按方案 §8.2 记录要求陈述，spike 轮形态仅作非严格参照，不构成配对复测 | 本轮无 |
| 5 | `20261001-v11-pre3-stab` | 稳定性（**2min 缩短版，D1**） | 脚本 `s1.js`：**VUS=20 / SOAK_MINUTES=2 / WARMUP_SEC=0**（pre2 报告 §3.0 实际值 VUS=20、SOAK_MINUTES=2；WARMUP_SEC=0 为统筹拍板沿用 pre2 原口径——pre2 执行时该参数尚未落地，等效即无时间型预热、整场即计量窗，见 DV-1）；pre2 计量窗实测 124.262s，697/697 全合格，soak 三段 mean 3611/3410/3466ms 无递增 | `fixed-stage1.json`（sha256 `18010cfa1ca4…3e8245b`，与 b0 同池同哈希）；池容量 2000 条，pre2 计量 697 笔轮换 | 同上；编制=全编制（主+A+B+D+C 结束后起用，手册 §8）；首末段对比由看板 `run-quality` 给出（不进 k6 退出码） | 同 §3 熔断线 | 002 三态门禁；方案 §8.2 稳定性场景 + §12 缺口 4；**R-G4（末段劣化 ≤20%）仅观测不判定**；**不得出长稳/内存泄漏结论（D1）**，首末段对比仅作观测 | 本轮无 |

> **抄录源标注**：b0/c1/e1spike/stab 的负载参数抄自 pre2 报告 §3.0（场景执行总览）、§3.2—§3.5（各场景读数）与窗口包 `dataset_fingerprint`；脚本参数名与默认值对照 `<内部路径>` 头部（只读）。唯一非抄录值：e1drop 整行（本轮新增）与 e1 的 `PHASE_SEC`（pre2 无显式记录，取脚本默认并要求执行窗核对）。

### 1.0 前置放行门（非本轮 5 轮场景，先于第 1 行执行）

依检查清单 C 节「预压放行门」两步要求与 pre2 先例（smoke2 + 同窗低档引用）：

1. 冒烟短样：b0 同型小样本（`-e ITERS=2 -e WARMUP=0` 型），gate 须 PASS（pre2 smoke2 先例：合格率 1.0、P90 3788.7ms）；
2. 目标容量低档 2 分钟零业务失败且 E2E 达标（清单 C 节示例：`c1 -e LEVELS=10 -e STAB_SEC=30 -e MEASURED_SEC=90`，或按 pre2 先例引用同窗低档既有定速数据——V09 探针口径）。

两步全过才进正式 5 轮；不过时先查环境、数据与口径，不加压重试。冒烟轮不占本轮 run_id 序列、不进结论（`run-scenario.sh` 直跑入口为 NOT_VERIFIED 口径；云端经 `lab.sh run` 的冒烟按其自身 manifest 判定）。

### 1.1 已知限制（写进矩阵的结论边界，继承不改写）

1. **stab 全部为 2 分钟缩短版口径，不得出长稳结论（D1）**：正式时长按方案 §8.2 公式（业务积累量 ÷ 选定 Goodput）回填后另开窗口补测；本轮 R-G4 仅观测。
2. **e1 drop 本轮首测（D4 补测，unknowns-6）**：无预压同型先例，恢复完整性结论只能按方案 §8.2 记录要求陈述，spike 形态仅非严格参照。
3. **c1 计量窗 2 分钟/档（D1 同源）+ Mock 口径（D5）**：本轮不建立新基线、不判容量拐点、全部读数不外推生产。
4. **b0s 流式基准不适用**：本轮非流式 k6 口径（R-G8 登记，pre2 §3.0 同型受限）。
5. **recovery 恢复场景不在本轮范围**：仅阶段三（手册 §8），本轮目标阶段为 1。
6. **工具选择准确率（R-G5）待补采集**（B15/DC6）：终态质量以 k6 判定计数 + gate 汇总为事实源，无逐笔 Eval 明细则按缺口登记不判达标。

## 2. 数据绑定快照（dataset_binding，供建包交叉核对）

绑定值从 pre2 窗口包 `dataset_fingerprint` 字段实读抄录（5 份窗口包一致）；档目录 `<内部路径>`（云端实档，`out/` 不入库，由 `pt-data-preparer` 按铺底方案 §7.2 切档清单重建并核验哈希）。

```json
{
  "version": "v1.1",
  "profile": "reference",
  "mix": "balanced",
  "access": "uniform",
  "seed": 42
}
```

配套指纹（建包核对项，源：pre2 窗口包 `dataset_fingerprint` + 铺底方案 §7.1）：

| 项 | 值 |
|---|---|
| 档标识 | `performance-v1.1/out/reference-balanced-uniform`（profile_id=reference-balanced-uniform） |
| profile 清单 sha256 | `734c2fef654b98a61c90cb75a046356caf7f7b95d30763116a503d735b253298` |
| `fixed-stage1.json` sha256（b0 / stab 绑定） | `18010cfa1ca4f17e9b54229d8dc670440689e09b45806d235b15a190f3e8245b` |
| `load-stage1.json` sha256（c1 / e1spike / e1drop 绑定） | `da39b6cca9cb1f69d97042bdee94e1f95e84019df38c6312ae2f7351dc109d14` |
| Mock 规则版本 | `performance-v1.1`（`MOCK_RULE_VERSION` 显式注入；model=mock、tool_calling_mode=native） |
| expected_rag_index_version | `d872c6e8` |
| 池容量口径 | `--pool-size 2000`：`load-stage1.json` 与 `fixed-stage1.json` 各 2000 条；`golden-stage1.json` 为黄金用例全量（阶段一适用 48 条），**本轮五轮场景均不绑 golden 池**（golden 池服务 `replay_golden.py` 回放校验） |
| 切档命令口径 | `lab.sh switch-data --dataset-version v1.1 --profile reference-balanced-uniform`（铺底方案 §7.2；`run --dataset-version v1.1 --data-profile reference-balanced-uniform --tickets <文件名>`，缺数据档参数拒绝开跑） |

**场景×池绑定一览**：b0→fixed、c1→load、e1spike→load、e1drop→load、stab→fixed（全部与 pre2 绑定一致；e1drop 沿用 e1 同池为统筹对称决策 DV-2 的组成部分）。

## 3. 停止条件（熔断线，统一引用检查清单 D 节，本轮不另设数值）

任一触发即暂停、保留现场（当窗产物 + 指纹 + 时间戳）、记录原因；处置按手册 §2 熔断协议（fuse-controller 注入 FuseEvent → 发压看护 10 秒内停 k6 并写 `k6-stop.json` → 是否以新 run_id 重启由主智能体决定；熔断/中止窗口一律 NOT_VERIFIED，不可判 PASS）：

1. 错误率连续 2 分钟 >5%；
2. 合格率较同档基线掉 >5 个百分点；
3. `/metrics` 非正常终态计数持续上涨（疑似循环/重试失控）；
4. 单位时间 token 消耗超同档基线 2 倍（成本熔断，方案 §4.3-④）；
5. 环境被变更（依赖、数据集、同机作业）。

场景级附加停止/预检条件：c1 施压机容量预检（清单 B6，见矩阵第 2 行）；e1 两轮相位样本量小、单笔失败即触限逐笔归因。

## 4. 当窗偏差记录（本轮与 pre2 的全部已知口径差异及可比性影响）

> 本节为**预登记**：执行窗按此逐条核对并随窗口包落盘；与 pre2 读数对比时必须携带本节口径差异声明。

### 4.1 统筹已拍的口径决策

| # | 偏差项 | 决策内容 | 对可比性的影响 |
|---|---|---|---|
| DV-1 | stab 预热口径（unknowns-7 闭环） | **沿用 pre2 原口径 `WARMUP_SEC=0`**（无时间型预热、整场即计量窗），保证与 pre2 stab 同型可比；20261001 stab60 轮已验证的 `WARMUP_SEC=60` 预热隔离参数**不采用**（那是 S7 控制实验口径，pre2 报告 §4.5 R2a 判据形态） | 与 pre2 可比性：**保持**。代价：AW1 型启动瞬态尖峰预期仍落入计量窗起点（pre2 §3.6 AW1 先例：cq 突刺簇 620.3—630.8ms、5s 内自愈），判读时按 pre2 同源现象对照，不计为新异常 |
| DV-2 | e1 drop 参数口径（本轮新增轮） | **参数对称 spike**：同负载档（高压 20VU / 低压 1VU）、同持续时长（PHASE_SEC=20），仅 `PATTERN=drop`；相位结构 high→low 两段（与 spike 三段不同，系脚本两种模式的固有形态差异） | drop 为 D4 首测（无 pre2 同型先例），与 spike 的相位级对照成立（同池同负载档），但**不构成配对复测**；恢复完整性结论按记录要求陈述（unknowns-6） |
| DV-3 | 执行顺序与方案 §13 的差异 | 老板拍板执行序 b0 → c1 → e1spike → e1drop → stab；方案 §13 顺序为 B0 → C1 → S1 → e1（stab 在 e1 之前）。本轮把 e1 两轮提前、stab 收尾 | 对 pre2 可比性无影响（pre2 实际执行序 b0 → c1 → e1spike → stab，本轮仅在 e1spike 后插入 e1drop，stab 仍收尾，与 pre2 轮间顺序同型）；与方案 §13 顺序的差异来源为老板拍板场景范围，登记备查 |
| DV-4 | Langfuse 上报开关 | **沿用 pre2 实测开启口径**（复刻原则：pre2 七份 manifest 实测 `observation.langfuse_enabled=true`，B7 contaminated 范围限定） | 观测层绝对读数延续 B7 受染标注纪律（方向性比较不受染，被测机资源层与门禁主路径不受染）；若执行窗切关上报属**口径变更**，须当窗另行声明且观测层读数不与 pre2 直接对比 |

### 4.2 手工等价链路 6 项（本轮执行链路与 pre2 全自动链路的差异）

本轮窗口若以手工等价链路执行（统筹派发输入给定），与 pre2 经 `run-isolated.sh` 全自动链路相比存在以下 6 项差异，逐项登记：

| # | 差异项 | 等价处置 | 对可比性与判定的风险 |
|---|---|---|---|
| MV-1 | 部署指纹文件缺失 | 改用 `/version` 三重指纹比对（target fingerprint + Mock 规则版本 + 数据档指纹，铺底方案 §7.2 第 4 步口径） | 指纹锁定手段弱于文件级指纹；需人工记录比对结果与时刻；读数口径不受影响，证据完备度降级 |
| MV-2 | 无 `make_manifest_file` 运行清单 | 手工组装等价 manifest 字段（run_id、场景、参数、数据指纹、时刻、SLO 来源） | manifest 缺失或不齐时三态门禁按 **NOT_VERIFIED** 从严（002 语义），不得以手工记录替代后判 PASS；这是本轮最大的判定风险项 |
| MV-3 | 无隔离快照 | 以 pre/post 手工核验（容器状态、/version 指纹前后一致、数据档哈希）替代快照留证 | 受染判定依据弱化；`contamination.detected` 无法机器判定时按「未验证」登记，不宣称 clean |
| MV-4 | 无 deploy-role 重部署 | 沿用窗口内既有构建，开跑前核对本地源与远端 `/version` 指纹一致（铺底方案 §7.2 第 2 步的核对子集） | 若指纹不一致本轮**拒绝开跑**（无重部署能力），须退回统筹处置 |
| MV-5 | 无 `stage_pause_others` | 阶段二/三容器隔离状态人工核验（Exited + 网关 503，pre2 §2.1-1 口径）并留证 | 隔离证据为人工记录；若发现阶段二/三在跑即触发熔断线第 5 条（环境被变更） |
| MV-6 | `collect` 需手工拼装 | 归档按 pre2 证据清单手工齐套：run.json、hashes.sha256、raw/k6-samples.ndjson、events/{environment,business,coordinator,load}.ndjson、levels.json（c1）+ OSS 归档回执 | 漏件即证据缺失 → 相应 claim 降 NOT_VERIFIED；上一轮 collect 未齐套前**不发下一轮**（串行纪律不因手工链路放宽） |

**合计影响说明**：读数口径（负载参数、数据档、SLO、Mock 规则）与 pre2 完全一致，逐轮读数可比；**证据链完备度低于 pre2**（manifest/快照/运行清单的机器保证缺失），三态门禁判定须按 002 从严语义执行——必需安全证据缺失一律 NOT_VERIFIED 并附原因码，禁止以「读数好看」放行。凡经手工链路产生的轮次，窗口包 `status_reasons` 须逐条注明等价处置依据。

## 5. 监控需求汇总（双路 + 看板，全轮适用）

- **环境路（pt-env-monitor，只读）**：`/version` 指纹（每轮开跑前后各一次）、`/metrics` 终态计数与 E2E 直方图、宿主/容器资源（CPU/内存/loadavg/磁盘）、服务日志 ERROR 环比、阶段二/三容器隔离状态核验。
- **业务路（pt-business-monitor，只读）**：施压端实时读数（合格率/错误率/时延分位）、终态分布、成本速率（Token/笔、相对成本）、清单 D 节业务类熔断三条（错误率/合格率/成本速率）盯守。
- **看板（Prometheus + Grafana，<内部路径>）**：`agent-overview`（应用层）、`k6-loadtest`（施压端）、`run-quality`（业务质量/段位对比）、`lab-resources`（四机资源）、`gateway` / `java-service`（链路与依赖层，c1/e1 轮必看）。
- **静默窗口纪律**：计量窗内禁止并行工作流 deploy、禁止经控制 EIP 的 SSH 维护操作（方案 §4.1-6、清单 B9）。

## 6. 交接（下游 pt-data-preparer）

```json
{
  "scenario_matrix_ref": "<内部路径>
  "dataset_binding": {
    "version": "v1.1",
    "profile": "reference",
    "mix": "balanced",
    "access": "uniform",
    "seed": 42
  }
}
```

下游核对要点：①按 §2 快照与配套指纹重建/核验数据档（`out/` 不入库，远端实档以哈希核对为准）；②`fixed-stage1.json` / `load-stage1.json` 两池哈希逐字节核对（A 组跨档同哈希纪律，铺底方案 §7.1）；③切档与装载交叉验证按铺底方案 §7.2 第 4 步（`runtime_load_verified` 只能由取证记录判定翻真）；④发现矩阵与方案 §8/§13 冲突时以方案为准并上报主智能体。

## 7. 建议提交信息（供交付参考；提交须老板授权）

```text
【文档】新增 20261001v11pre3 战役场景矩阵

1. 依方案 §8/§13 与 pre2 复刻基线编制五轮场景矩阵（b0/c1/e1spike/e1drop/stab）并逐行登记负载量纲、输入池、监控、停止条件与验收来源
2. 实读 pre2 窗口包固化数据绑定快照（v1.1/reference/balanced/uniform/seed=42）与两池哈希
3. 预登记当窗偏差记录：stab WARMUP_SEC=0 决策、e1drop 对称 spike 首测、手工等价链路 6 项及其可比性与判定风险
```