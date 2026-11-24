# 第 26 讲配套资料

本讲主题：如何把重要数据串成证据链并完成平台建设。

本目录提供四源取证样例、变更与直连配对附表、观测环境说明、截图采集记录与三份运行摘要，对应正文中的统一标识、六类数据、关联级别、七步取证、缺口登记与平台建设决策。本目录不包含正文图片、含内网地址的云窗全量脚本或逐笔原始样本。

## 实验数据

| 参考 | 用途 |
|---|---|
| [阶段一网关轮 run 摘要](experiments/0047-20261103-v13-real23-p1-03-p103-run.json) | 正式证据包，走出包核验；门禁 PASS，合格率 1.0，P90 9281.6ms |
| [变更基线轮 run 摘要](experiments/1041-20261103-v13-real23-p5-01-base3-run.json) | 原始运行归档；门禁 FAIL，合格率 0.86，P90 30016ms |
| [变更轮 run 摘要](experiments/1058-20261103-v13-real23-p5-02-chg3-run.json) | 原始运行归档；门禁 FAIL，合格率 0.88，P90 28275.5ms |

## 案例参考（公开脱敏版）

| 参考 | 用途 |
|---|---|
| [四源取证样例与缺失清单](references/four-source-evidence-sample-20261104.md) | 18 样本选样、四源覆盖表与缺失清单 |
| [W5 变更配对逐笔表](references/w5-change-pairing-table-20261104.md) | 变更复测 50 笔逐笔配对 |
| [直连网关配对逐笔表](references/direct-gateway-pairing-20261104.md) | 三阶段直连／网关逐笔对照 |
| [Langfuse 自托管说明](references/langfuse-selfhost-readme.md) | 轨迹观测环境 |
| [Prometheus 监控说明](references/prometheus-monitoring-readme.md) | 指标与日志观测环境 |
| [截图采集记录](references/observability-screenshot-collection-20261104.md) | 压测工具面、监控面、系统面与同笔核对图的来源与边界 |

## 相关案例代码与登记

| 入口 | 用途 |
|---|---|
| [k6 套件说明](../../case/customer-service-ticket-system/testing/k6-suite/README.md) | 发压主链与门禁判定入口 |
| [问题清单](../11-scenario-combination/references/issue-register.md) | 跨讲问题与缺口登记 |
| [取数与工具缺口清单](../25-minimum-tool-combination/references/tooling-gap-checklist.md) | 六件数据面能力与缺口边界 |

## 使用方式与证据状态

1. 先统一标识，再看六类数据；关联级别必须随结论一起写明。
2. 四源取证样例只证明各源能否挂到同一笔业务，不代替关键路径拆分与因果验证。
3. 三份 run 摘要只保留门禁与窗口字段，路径已遮蔽；原始逐笔样本不随本仓库发布。
4. 观测环境说明只描述组件与用途，不含真实凭据与内网地址。
5. 截图采集记录为只读回看，采集时未重新发压。

## 参考章节与使用说明

- 参考课程第 26 讲「先给一笔业务一个身份」「六类数据怎么挂到同一笔业务」「证据链怎么用：按顺序取证与缺口登记」「数据链长成什么样：四个阶段」「什么时候才建平台」。
- 正文与完整讲解请在专栏阅读。
