# 战役 20261014-v12-real1 场景矩阵（公开脱敏版）

> 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。

> campaign_id：`20261014-v12-real1` ｜ 角色：主智能体（经老板方案批准编制） ｜ 产出时间：2026-10-14
>
> **文件性质：准备阶段产出（准备包字段之一）**。真实大模型（DeepSeek 官方 API）新口径战役：Provider 常驻切换 `LLM_PROVIDER=openai` 后，完整复跑阶段一七轮（放行门两步 + 五正式轮），负载参数与执行序全部抄录 formal3 实际执行值。门禁阈值引用既有权威来源（002 契约 / `k6-suite/lib/contract.js`）；**SLO 数值与计价单值为本战役口径变更点**，来源与回填状态见 §1.5，未经校准回填不得开跑正式五轮。
>
> 权威依据：授权记录-20261014v12real1（授权链与待拍板项）、场景矩阵-20261012v11formal3（**负载参数抄录基线**）、性能测试方案 v1.0 §8/§13/§4.3（执行序/熔断）、k6压测执行多Agent编排手册 v1.0 §1/§8、<内部路径>）、Phase 0 云端核验实录（2026-10-14 09:37，四机常驻全栈健康）。

## 0. 上游先验结论（动作序列第 1 步）

| 先验项 | 结果 |
|---|---|
| 老板拍板齐备 | 是：模型选型（DeepSeek 官方 API）、范围（仅阶段一七轮）、SLO 口径（先校准再定新值）、费用前置（云端先核验）四项已拍板（2026-10-14 会话实录，见授权记录 §1） |
| 方案批准 | 是：五阶段实施方案获批（Phase 0—4），本矩阵为 Phase 1 产出 |
| 云端现状核验 | 是（Phase 0，2026-10-14 09:37 实测）：四机 Running、AutoReleaseTime 空（常驻）；网关 `/login` 200；Prometheus 13/13 targets up（9190）；Langfuse/Grafana 200；三阶段 Agent 容器 healthy；`/version` `protocol_mode.llm_provider=mock`（切换前基线）；余额 73.23 元（告警线 50 元，日耗约 74.6 元） |
| 切换通道就绪 | 是（Phase 1）：lab.sh `LAB_LLM_PROVIDER`/`LAB_LLM_MODEL` 覆盖位 + overlay `LLM_MODEL`/`LLM_TOOL_CALLING_MODE` 透传 + `--caliber-env` 判定口径注入已落地（bash -n 通过） |
| 数据绑定有效 | 是（Phase 1 本地复核）：两池哈希与 formal3 记录逐字节一致（见 §2） |
| 待拍板阻塞项 | ~~在~~ **全部解锁（2026-10-14 晚）**：DeepSeek API Key 已提供（deepseek-flash 实测核验）、DeepSeek 充值 ¥100（余额 100.25）、SLO 20000ms + 口径 B + 费用上限 ¥80/熔断 ¥120 三项拍板完成、云端切换与数据档核验通过——准备包翻 ready；阿里云余额 ¥41.96 今晚够用、**明早须充值**（已破 50 元告警线） |

**先验结论：上游通过、阻塞项登记在案，进场景矩阵编制；正式五轮开跑前须全部翻绿。**

## 1. 场景矩阵（本轮 5 正式轮 + 2 放行门，阶段一，Provider=DeepSeek）

run_id 命名规则：`20261014-v12-real1-<场景>`。执行序即下表行序；每轮 `collect` 归档成功（退出码 0）后才发起下一轮（串行纪律不放宽）。负载量纲数值**全部从 formal3 场景矩阵 §1 抄录实际执行值**（非脚本默认值）；真实模型下预期单笔时延显著拉长，但窗口定义按时长/迭代计，不改变参数本身——时延分布变化本身即本战役观测对象。

| # | scenario_id | 类型 | 负载量纲（抄 formal3 实际值） | 输入数据池（v1.1 reference 档） | 监控需求 | 停止条件 | 验收来源 |
|---|---|---|---|---|---|---|---|
| G1 | `20261014-v12-real1-smoke2` | 放行门①冒烟短样 | 脚本 `b0.js`：**ITERS=8 / WARMUP=3** | `fixed-stage1.json`（sha256 `18010cfa…3e8245b`） | 双路：pt-env-monitor + pt-business-monitor | §3 熔断线；**另作 result_ok 耦合探针**（§4 DV-R5） | 002 三态门禁；清单 C 节放行门① |
| G2 | `20261014-v12-real1-c1low` | 放行门②低档引用 | 脚本 `c1.js`：**LEVELS=10 / STAB_SEC=30 / MEASURED_SEC=90** | `load-stage1.json`（sha256 `da39b6cc…109d14`） | 同上；**另作 API 限流探针**（§4 DV-R6） | §3 熔断线 | 002 三态门禁；清单 C 节放行门② |
| 1 | `20261014-v12-real1-b0` | 基准（非流式 k6 口径） | 脚本 `b0.js`：**VUS=1 / ITERS=33 / WARMUP=3** | `fixed-stage1.json`（与 G1 同池同哈希） | 双路全编制；看板四块+gateway/java-service | §3 熔断线 | 002 三态门禁；方案 §8.2 基准场景 |
| 2 | `20261014-v12-real1-c1` | 容量（阶梯加压） | 脚本 `c1.js`：**LEVELS=1,5,10,20,30,50** × 每档 **STAB_SEC=60 + MEASURED_SEC=120**，**LOAD_MODEL=vu** | `load-stage1.json`（与 G2 同池同哈希） | 同上；施压机容量预检（清单 B6）+ DeepSeek 限流盯守 | §3 熔断线；B6 预检 | 002 三态门禁逐档判定；容量结论只引用逐档摘要（levels.json） |
| 3 | `20261014-v12-real1-e1spike` | 异常（流量突刺） | 脚本 `e1-traffic-change.js`：**PATTERN=spike / BASE_VUS=1 / SPIKE_VUS=20 / PHASE_SEC=20** | `load-stage1.json`（与 c1 同池同哈希） | 全编制；相位读数与注入/恢复时刻逐项记录 | §3 熔断线；相位样本量小、单笔失败即触限逐笔归因 | 002 三态门禁（相位级）；方案 §8.2 |
| 4 | `20261014-v12-real1-e1drop` | 异常（流量骤降） | 脚本 `e1-traffic-change.js`：**PATTERN=drop / SPIKE_VUS=20（high）/ BASE_VUS=1（low）/ PHASE_SEC=20** | `load-stage1.json`（与 c1/e1spike 同池同哈希） | 同上 | 同上 | 002 三态门禁（相位级）；方案 §8.2 |
| 5 | `20261014-v12-real1-stab` | 稳定性（正式 30min） | 脚本 `s1.js`：**VUS=20 / SOAK_MINUTES=30 / WARMUP_SEC=0** | `fixed-stage1.json`（与 b0 同池同哈希） | 全编制；首末段对比由看板 `run-quality` 给出 | §3 熔断线（token 熔断基线以本战役首窗为准） | 002 三态门禁；方案 §8.2 稳定性场景；R-G4 末段劣化 ≤20% 观测 |

> **抄录源标注**：七轮负载参数抄自 formal3 场景矩阵 §1（其本身抄自 formal 报告 §3.3 与 formal 窗口包）；k6 工厂脚本与入口脚本自 10-01 逐字节未变（`git status` 于 2026-10-14 复核干净），脚本指纹与 formal3 准备包一致。

### 1.0 前置放行门（先于第 1 行执行）

1. **real-smoke 校准窗** ✅ **已执行（2026-10-14 晚，run_id `20261014-v12-real1-calib`，20 笔全 200/completed、零错误零护栏违规、usage 全 reported，OSS 回执 verified）**：E2E 中位 8500 / P90 11899 / P95 13989 / max 13989 ms（首字中位 7904ms，推理型先思考后吐字）；单笔 token 均值 输入 2766 + 输出 1231 ≈ 3998；单笔费用低谷 ≈¥0.0082 / 高峰 ≈¥0.0165；evidence=provider_stream。据此定 §1.5 建议值呈老板拍板。
2. 冒烟短样 smoke2 **耦合探针已执行**（`20261014-v12-real1-smoke2probe`，ITERS=8+WARMUP=3，10s 旧口径下 gate FAIL 属预期：qualified 0.5 / error_rate 0 / e2e_p90 10696ms；逐笔四级 request/flow/business 100%，工具调用 0—3 次/笔真实发生；4 笔不合格中 3 笔卡 result_ok 逐字关键词（漏「已发货/待发货/物流信息」，模型语义正确措辞不同）、1 笔纯 SLO 边缘超 10041ms——换 20s SLO 后只剩关键词口径问题，DV-R5 处置待拍板；正式放行门①在拍板后以正式 run_id 重跑）。c1low 限流探针随正式放行门②执行。
3. 三步全过才进正式 5 轮；smoke2/c1low 占用正式 run_id 序列（与 formal 系列同型）。

### 1.5 判定口径（本战役口径变更点，待校准回填——**未回填不得开跑正式五轮**）

| 项 | 值 | 状态 | 来源与注入通道 |
|---|---|---|---|
| SLO（e2e_p90 上限） | **20000ms**（校准实测 P95 13989ms × 约 1.4 余量取整，覆盖 c1 高档并发排队） | ✅ 已拍板（2026-10-14 晚） | `--caliber-env SLO_MS=20000` + lab.env `LAB_SLO_MS=20000`；口径变更登记于授权记录 §1 |
| 计价（元/千 token） | **0.00205（低谷档）**——实测加权混合单价（输入 2766 : 输出 1231 × DeepSeek 官方低谷价折算；board.js 单一单价口径） | ✅ 已拍板 | `--caliber-env COST_PER_1K=0.00205`；报告成本口径声明「真实计价、混合单价（低谷档）」 |
| 门禁其余两项 | qualified_rate ≥0.90 / error_rate ≤0.005（contract.js 既有阈值，不变） | ✅ | 002 契约；**result_ok 口径=拍板 B 登记**（DV-R5）：正式轮统一 `--env RELAX_RESULT_TO_BUSINESS=1`，result_ok 回退业务完成级、关键词命中率单列观测（quality 命中/缺失明细照常落盘；阶段三附加维度仍硬判定） |
| 熔断 token 基线 | **≈4000 token/笔**（20 笔校准均值 3998、探针 11 笔均值 3519）；总量基线按各窗笔数预估，首窗 smoke2/c1low 实测复核 | ✅ 首窗复核 | 方案 §4.3-④「超基线 2 倍暂停」纪律沿用 |
| 费用上限 | **¥80**（七轮约 7000 笔 × 低谷单笔 ¥0.0082 ≈ ¥58 × 1.3 余量）；**熔断线 ¥120**；DeepSeek 账户实测 ¥100.25 | ✅ 已拍板 | 授权记录 §1/§2-3；逐窗盯 DeepSeek 余额折算累计，超线即停 |

CALIBER_SOURCE 说明：run-isolated 对 k6 侧统一标注 `orchestrator`；本战役 SLO/计价实际来源=环境显式注入（`--caliber-env`），以本节登记与 run 日志「判定口径显式注入」行为准，窗口包 `gate.evaluated_from.e2e_slo_ms` 与本节回填值一致性由收窗核对。

### 1.1 已知限制（结论边界）

1. **真实模型新口径**：读数不与 v11formal 系列直接比较（方案红线）；real-smoke 产物仅校准口径，不参与容量外推。
2. **单端点单模型**：DeepSeek 官方 API 单一端点单一模型，结论不外推其他模型/供应商；API 侧限流与波动是外部输入属性，异常窗归因须区分 SUT 侧与 API 侧（S2 映射纪律）。
3. **c1 计量窗 2 分钟/档**、**recovery 不在本轮范围**、**b0s 流式不适用**：与 formal3 §1.1 同口径。
4. **result_ok 判定依赖数据集关键词**（judge.js 按工单 `expected_answer_keywords/facts` 逐字命中）：关键词按 Mock 模板设计，真实模型命中率未知——DV-R5 探针前置，处置须拍板，不擅自降断言。
5. Token 逐位全等等价直证不适用；对照结论只做分布级（P90/分位/总量级）参考并携带 §4 声明。

## 2. 数据绑定快照（dataset_binding，供建包交叉核对）

与 formal3 同源同哈希（2026-10-14 本地实测复核，见数据清单-20261014v12real1）；数据五元组不变——真实模型改变的是**答复内容与 token 分布**，不改输入工单池。

```json
{
  "version": "v1.1",
  "profile": "reference",
  "mix": "balanced",
  "access": "uniform",
  "seed": 42
}
```

配套指纹（与 formal3 窗口包一致，本地复核）：

| 项 | 值 |
|---|---|
| 档标识 | `performance-v1.1/out/reference-balanced-uniform`（profile_id=reference-balanced-uniform） |
| `fixed-stage1.json` sha256（b0 / stab / smoke2 绑定） | `18010cfa1ca4f17e9b54229d8dc670440689e09b45806d235b15a190f3e8245b` |
| `load-stage1.json` sha256（c1 / c1low / e1spike / e1drop 绑定） | `da39b6cca9cb1f69d97042bdee94e1f95e84019df38c6312ae2f7351dc109d14` |
| Mock 规则版本 | `performance-v1.1`（数据档与校验器配对仍按 v1.1 档执行；MOCK_RULE_VERSION 仅为数据档一致性校验用，Provider 切换后不影响） |
| 切档命令口径 | `lab.sh switch-data --dataset-version v1.1 --profile reference-balanced-uniform`（若云端档已在位则哈希核对跳过上传）；`run --dataset-version v1.1 --data-profile reference-balanced-uniform --tickets <文件名>` |

**场景×池绑定一览**：smoke2/b0/stab→fixed、c1low/c1/e1spike/e1drop→load（全部与 formal 系列绑定一致）。

## 3. 停止条件（熔断线）

沿用检查清单 D 节五条（错误率连续 2 分钟 >5%；合格率较同档基线掉 >5 个百分点；`/metrics` 非正常终态计数持续上涨；token 消耗超基线 2 倍；环境被变更），处置按手册 §2 熔断协议；熔断/中止窗口一律 NOT_VERIFIED。本战役口径调整：

- **token 熔断基线**：Mock 基线不适用，以本战役首窗（smoke2/c1low）实测回填（§1.5）；
- **费用熔断**：累计 API 费用超授权上限 1.5 倍即暂停（上限待回填）；
- **API 侧异常**：DeepSeek 429/5xx 连续出现或 error_rate 抬升即停，区分 API 侧与 SUT 侧后带数据请示（降档 `-e LEVELS` 属场景参数变更须拍板）。

## 4. 当窗偏差记录（本轮与 formal3 的全部已知口径差异及可比性影响）

> 本节为**预登记**：执行窗逐条核对并随窗口包落盘；与 v11 系列对比必须携带本节声明。

| # | 偏差项 | 内容与处置 | 对可比性的影响 |
|---|---|---|---|
| DV-R1 | Provider 常驻切换 openai | lab.env `LAB_LLM_PROVIDER=openai` + `LAB_LLM_MODEL=deepseek-flash` → sync-agent 写入 → `/version` `protocol_mode.llm_provider=openai` 实测核验（2026-10-14 晚）；manifest `model` 字段记 openai。**指纹判性完成**：/version 聚合指纹 `b0ca8191…`→`482ed0ffa2a09c17` 系配置层折入（model/provider 参与聚合计算），**35 项源文件哈希与 mock 基线逐字节相等（源码零改动实测）**；切换时 sync-agent 覆写 .env 的 PERF_*/MOCK_RULE_VERSION 已按 switch-data 尾步等价补回，`mock_rule_version=performance-v1.1` 保持 | 答复生成链路整体换轨，时延/token/成本读数全部为新口径；与 v11 系列只有结构性对照（合格率/错误率/相对形态），绝对值不可比；等价判据=源文件哈希（非聚合指纹） |
| DV-R2 | SLO 校准口径变更 | 新 SLO=real-smoke 校准 P90+余量（待回填），`--caliber-env SLO_MS` 注入 + `LAB_SLO_MS` 同步；旧 10s 口径不再作为门禁 | 门禁通过率与 v11 系列不可直接比；报告双记录新 SLO 值与来源 |
| DV-R3 | 计价口径变更 | `--caliber-env COST_PER_1K` 注入 DeepSeek 混合单价（输入输出按校准实测比加权）；报告成本为真实计价（非 Mock 假设 2 元/千 token） | 成本绝对值与 v11 系列（mock 假设计价）不可比；换算关系随单价表登记 |
| DV-R4 | 非确定性 | 真实模型输出非确定：token 逐位全等等价直证不适用；usage 口径 `reported`（端点返回），缺失时按既有纪律记 missing 不冒充 | 证据链依赖指纹+脚本等价+reported usage；重复运行只做分布级对照 |
| DV-R5 | result_ok 与数据集关键词耦合 | **探针实测（smoke2probe，计量窗 8 笔）**：request/flow/business 三级 100%、工具调用真实发生；result_ok 5/8（62.5%）——3 笔漏逐字关键词/事实（「已发货」「待发货」「物流信息」，warmup 另有「北京中转站」），模型语义正确但措辞不同。**处置=拍板 B 登记降断言（2026-10-14 晚）**：k6 判定链新增 `RELAX_RESULT_TO_BUSINESS` 开关（config/base/api/三适配器/selfcheck 七文件变更，默认关闭保持 formal 系列语义；selfcheck 新增 A3b 四例验证），正式轮统一携带 `--env RELAX_RESULT_TO_BUSINESS=1`，result_ok 回退业务完成级、关键词命中率单列观测（逐笔 judgements 增 `result_caliber` 字段留痕）；**「k6 工厂脚本自 10-01 逐字节未变」声明自本轮起失效**，改为「场景脚本与编排器未动，判定链七文件于 2026-10-14 登记变更（v12real1 口径 B）」，脚本指纹以本轮各窗口包 `script_fingerprint` 为准 | 门禁恢复可判定性（qualified 预期回归 ≥0.90，B 口径下与 v11 系列结构可比）；质量读数以关键词命中率单列呈现，不与 Mock 轮 result_ok 直接比较 |
| DV-R6 | DeepSeek API 限流/并发上限未知 | c1 六档最高 50 VU 预分配 116；真实 API 的 RPM/并发上限未证实——c1low 先探，429/超时形态带数据请示；降档属拍板项 | 若限流触发，容量档读数含 API 侧外部约束，须在结论中显式声明边界 |
| DV-R7 | 环境事实（与 formal3 同批四机、无重建） | 四机为 2026-10-12 二次恢复批次常驻实例（实例 ID 同 formal3 台账），未重建；`cs-agent-frontend` 较其他容器晚约 31 小时重启（2026-10-13 工作台流式修复上云所致，非本战役变更，登记为环境事实）；余额 73.23 元（09:37 实测） | 拓扑/规格不变；前端重启属应用层修复，如 e2e 分布观察到前端层差异再单独登记 |

## 5. 监控需求汇总（双路 + 看板，全轮适用）

- **环境路（pt-env-monitor，只读）**：`/version` 指纹（每轮开跑前后各一次，重点核验 `protocol_mode.llm_provider=openai` 保持）、`/metrics` 终态计数与 E2E 直方图、宿主/容器资源、服务日志 ERROR 环比、DeepSeek 调用错误（httpx 超时/429/5xx）。
- **业务路（pt-business-monitor，只读）**：施压端实时读数（合格率/错误率/时延分位）、**分维度判定计数（request/flow/business/result/slo 各级失败数）**、终态分布、成本速率、§3 熔断三条盯守。
- **看板**：`agent-overview` / `k6-loadtest` / `run-quality` / `lab-resources` / `gateway` / `java-service`。
- **Langfuse**：真实模型 trace 全量留档（`observation.langfuse_enabled=true` 沿用；真实 trace 对后续 RESAR S5 定向分析价值高于 Mock 轮，窄窗 ±8s 取数纪律同 formal 系列）。
- **静默窗口纪律**：计量窗内禁止并行工作流 deploy、禁止经控制 EIP 的 SSH 维护操作。

## 6. 交接（下游：Phase 2 校准与 Phase 3 窗口执行）

```json
{
  "scenario_matrix_ref": "<内部路径>
  "dataset_binding": {
    "version": "v1.1",
    "profile": "reference",
    "mix": "balanced",
    "access": "uniform",
    "seed": 42
  },
  "caliber_pending": ["SLO_MS", "COST_PER_1K", "token 熔断基线", "费用上限"],
  "blocking_approvals": ["DeepSeek API Key", "充值额度", "SLO 数值与费用上限拍板"]
}
```

执行要点：①Phase 2 校准与切换按 <内部路径> → sync-agent → /version 核验 → real-smoke 20 笔）；②§1.5 四项回填并拍板后才进正式五轮；③每轮 collect 成功才发下一轮；④执行中拍板点见授权记录 §2。

## 7. 建议提交信息（供交付参考；提交须老板授权）

```text
【文档】新增 20261014v12real1 真实模型战役场景矩阵与准备包

1. 依 formal3 抄录基线编制 DeepSeek 真实口径七轮场景矩阵，登记口径变更点（SLO/计价待校准回填、token 熔断基线换新）与偏差预登记 DV-R1—R7
2. 数据绑定与两池哈希本地复核（与 formal3 逐字节一致），脚本自测与 lab.sh 语法核验留证
3. 准备包按 008 契约登记 blocked 状态与四项待拍板阻塞项（Key/充值/SLO/费用上限）
```