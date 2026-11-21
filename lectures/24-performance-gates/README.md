# 第 24 讲配套资料

本讲主题：怎样把一次 Workflow、Flow + RAG 工单系统性能测试变成持续评估门禁？

本目录提供门禁判决件与变更复测附表，对应正文中的阈值生效对照、放行／阻断演练与 W5 变更配对。本目录不包含正文图片、完整云上运维脚本或逐笔原始产物。

## 实验数据

| 参考 | 用途 |
|---|---|
| [首跑判决件（阈值未生效留证）](experiments/gate_workflow_20261103224408.json) | performance 阈值仍为 8,000ms 的首跑留证 |
| [权威判决件（阈值 30000 生效版）](experiments/gate_workflow_20261103224758.json) | 同一批证据重判后 performance=30,000ms 的权威件 |
| [演练阻断件](experiments/gate_workflow_20261104034940.json) | 实测 6,894.408ms、阈值 5,000ms → FAIL |
| [演练放行件](experiments/gate_workflow_20261104035037.json) | 同实测、阈值 30,000ms → PASS |

## 案例参考（公开脱敏版）

| 参考 | 用途 |
|---|---|
| [W5 变更配对逐笔表](references/w5-change-pairing-table-20261104.md) | 变更复测同数据、同口径、单因子 timeout 对照 |

## 相关案例代码

| 入口 | 用途 |
|---|---|
| [门禁执行器](../../case/customer-service-ticket-system/app/workflow-flow-rag/src/gates/executor.py) | 联合门禁执行 |
| [门禁指标取数](../../case/customer-service-ticket-system/app/workflow-flow-rag/src/gates/report_metrics.py) | 从专项报告取数 |
| [冻结清单与校验](../../case/customer-service-ticket-system/app/workflow-flow-rag/src/gates/suite.py) | 冻结清单 |
| [门禁判决入口](../../case/customer-service-ticket-system/app/workflow-flow-rag/tests/performance/gate_suite.py) | 判决入口 |
| [联合门禁配置](../../case/customer-service-ticket-system/app/workflow-flow-rag/src/config/workflow_config.yaml) | 配置阈值 |
| [结果判定 judge.js](../../case/customer-service-ticket-system/testing/k6-suite/lib/judge.js) | 压测侧三态判定 |
| [三态门禁校验 verify-run.py](../../case/customer-service-ticket-system/testing/k6-suite/orchestrator/verify-run.py) | 运行摘要三态校验 |

## 使用方式与证据状态

1. 首跑与权威件证明：聚合阈值键名错配时，应用原证据重判，不必整窗重跑。
2. 阻断／放行两件证明：实测值不动，只换阈值，判定分支会翻转。
3. 判决件为 mock 机制演练口径，不能直接当作真实模型性能结论。
4. W5 附表只证明变更复测配对方法与单因子对照，不宣称优化有效。

## 参考章节与使用说明

- 参考课程第 24 讲「门槛怎么定、怎么证明生效」「变更触发矩阵」「发布卡点与条件放行」。
- 09-26 容量判例原文见第 11 讲公开参考：[压测报告](../11-scenario-combination/references/three-stage-load-test-report-20260926.md)、[问题清单](../11-scenario-combination/references/issue-register.md)。
- 正文与完整讲解请在专栏阅读。
