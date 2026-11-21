# 第 25 讲配套资料

本讲主题：工具这么多，怎样从项目问题反推出最小工具组合？

本目录提供运行证据摘要、配对附表、流式专项摘要与工具入口说明，对应正文中的问题—能力—工具映射与反校验。本目录不包含正文图片、含内网地址的云窗全量脚本或逐笔原始样本。

## 实验数据

| 参考 | 用途 |
|---|---|
| [八轮 run 摘要表](experiments/run-evidence-summary-20261103.md) | 阶段一／二／三网关与直连、变更基线／变更轮门禁字段 |
| [各轮最小化 run.json](experiments/) | 与上表对应的单文件摘要（路径已遮蔽） |
| [流式专项摘要](experiments/stream-report-20261104-015811-summary.json) | 流式采集专项 |
| [中断验证摘要](experiments/stream-interrupt-report-20261104-020001-summary.json) | 中断路径验证 |

## 案例参考（公开脱敏版）

| 参考 | 用途 |
|---|---|
| [工具反校验主数据](references/tool-countercheck-master-20261104.md) | 能力项与工具反校验 |
| [直连网关配对](references/direct-gateway-pairing-20261104.md) | 直连／网关对照 |
| [W5 变更配对](references/w5-change-pairing-table-20261104.md) | 变更复测配对 |
| [三架构配对](references/three-architecture-pairing-20261104.md) | 三架构对照 |
| [取数与工具缺口清单](references/tooling-gap-checklist.md) | 缺口与取数边界 |
| [数据库连接腐坏轮导航](references/db-corruption-run-navigation-0520.md) | 共享库腐坏轮证据导航 |
| [Langfuse 自托管说明](references/langfuse-selfhost-readme.md) | 轨迹观测环境 |
| [Prometheus 监控说明](references/prometheus-monitoring-readme.md) | 指标观测环境 |
| [云窗入口说明](references/lab-window-entry.md) | 云窗职责（不含内网脚本全文） |

## 相关案例代码

| 入口 | 用途 |
|---|---|
| [k6 套件说明](../../case/customer-service-ticket-system/testing/k6-suite/README.md) | 发压主链 |
| [判定与看板自测](../../case/customer-service-ticket-system/testing/k6-suite/selfcheck.mjs) | 自测 |
| [本机隔离运行](../../case/customer-service-ticket-system/testing/k6-suite/orchestrator/run-isolated.sh) | 本机复测／配对入口 |
| [三态门禁校验](../../case/customer-service-ticket-system/testing/k6-suite/orchestrator/verify-run.py) | 运行摘要校验 |
| [多轮场景脚本](../../case/customer-service-ticket-system/testing/k6-suite/scenarios/e2-multiturn.js) | 多轮场景 |
| [版本指纹](../../case/customer-service-ticket-system/app/workflow-flow-rag/src/utils/version.py) | 指纹 |
| [服务端观测指标](../../case/customer-service-ticket-system/app/harness/metrics.py) | harness 指标 |
| [故障注入配置](../../case/customer-service-ticket-system/app/workflow-flow-rag/src/config/workflow_config.yaml) | 注入与门禁配置 |
| [共享数据清单](../../case/customer-service-ticket-system/testing/test-data/MANIFEST.md) | 数据集入口 |

## 使用方式与证据状态

1. 先按问题列必须能力，再映射工具；不要按产品功能清单堆工具。
2. 八轮摘要只给门禁与窗口字段，用于反校验「证据与可复跑」能力是否在位。
3. 云窗全量脚本含实验室内网地址，本仓库只发布职责说明；本机入口以 `run-isolated.sh` 为准。
4. 观测环境说明仅描述组件与用途，不含真实凭据。

## 参考章节与使用说明

- 参考课程第 25 讲「问题—能力—工具映射」「四笔账」「七个节点不是七选一」。
- 阶段一 S1 底稿见第 13 讲公开参考：[S1 压力数据](../13-execution-evidence/references/s1-pressure-data-20261014v12real1.md)。
- 问题清单见第 11 讲：[issue-register](../11-scenario-combination/references/issue-register.md)。
- 正文与完整讲解请在专栏阅读。
