# 第 27 讲配套资料

本讲主题：开工前最容易踩的十大坑，哪些必须提前避开？

本目录提供开工会五件产物模板、变更配对逐笔表、四源取证样例与阶段一至三的长稳／恢复读数，对应正文中的十条坑、开工会五问、开工避坑清单与「范围可以缩、口径不能缩」。本目录不包含正文图片、完整云上运维脚本或逐笔原始产物。

## 开工会五件产物（可用模板）

| 参考 | 承接问题 | 用途 |
|---|---|---|
| [判定纸](templates/judgement-sheet.md) | 第一问·定口径 | 四级判定、合格率分母、门槛与缺值处理 |
| [指纹纸](templates/fingerprint-sheet.md) | 第二问·定指纹 | 代码／数据／模型／工具版本、覆盖范围与三处一致核对 |
| [场景与欠条纸](templates/scenario-debt-sheet.md) | 第四问·定恢复 | 场景清单、失败分类、恢复验收与欠条字段 |
| [配对申请表](templates/pairing-request.md) | 第三问·定对照 | 冻结清单、唯一改动、四项门槛与四项复核 |
| [平台决策表](templates/platform-decision.md) | 第五问·定边界 | 三行前提、三行收益与「不要做的症状」 |

## 案例参考（公开脱敏版）

| 参考 | 用途 |
|---|---|
| [阶段一全时长长稳补证记录](references/stage1-full-duration-supplement-20261105.md) | 39 分钟、8,269 笔补证口径与趋平台结论 |
| [阶段二、三长稳读数](references/stage23-soak-data-card-20261105.md) | 连续 164.7 分钟两段长稳、全局窗聚合与废弃窗登记 |
| [阶段二恢复复测与长稳](references/stage2-recovery-data-card-20261103.md) | S1 长稳读数与恢复完整性、低负载复测 |
| [W5 变更配对逐笔表](references/change-pairing-w5-20261104.md) | 变更复测 50 笔逐笔配对，两轮门禁均未通过 |
| [四源取证样例与缺失清单](references/four-source-evidence-20261104.md) | 18 样本选样、四源覆盖表与缺失清单 |

## 相关案例代码与登记

| 入口 | 用途 |
|---|---|
| [压测前检查清单](../10-plan-data-environment/references/pre-test-checklist.md) | 开跑前检查，与本讲开工避坑清单分工 |
| [问题清单](../11-scenario-combination/references/issue-register.md) | 跨讲问题与缺口登记 |
| [真实模型轮压测收尾阶段交接单](../13-execution-evidence/references/closeout-handoff-20261014v12real1.md) | 收尾交接与证据状态 |
| [三阶段全场景压测报告](../11-scenario-combination/references/three-stage-load-test-report-20260926.md) | 三阶段压测结论 |
| [k6 套件说明](../../case/customer-service-ticket-system/testing/k6-suite/README.md) | 发压主链与门禁判定入口 |

## 使用方式与证据状态

1. 五件产物为空白模板，示例值不代表已完成实验；填写口径以正文「开工会怎么开才算数」为准。
2. 长稳与恢复读数只证明对应窗口的结论边界；阶段三样本量未达目标、原并发下二次加压未覆盖，均按未验证登记。
3. 变更配对与四源取证只证明方法与缺口，不宣称优化有效。

## 参考章节与使用说明

- 参考课程第 27 讲「十条坑，一条一条看」「开工会怎么开才算数」「清单怎么用，标题怎么写」「测试范围可以缩减，判定口径不能缩减」。
- 正文与完整讲解请在专栏阅读。
