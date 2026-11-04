# 第 19 讲配套资料

本讲主题：第六步：最慢的组件就一定是 Agent 性能瓶颈吗？

本目录提供 1 份瓶颈判定底稿与 1 份历史根因分析，对应正文中的五环证据链、两条瓶颈结论、未排除因素与结论段写法。本目录不包含正文图片、项目源码或逐笔原始数据。

## 案例参考（公开脱敏版）

| 参考 | 用途 |
|---|---|
| [S6 瓶颈判定底稿](references/s6-bottleneck-judgment-20261014v12real1.md) | 看五环证据链状态、主约束与 SUT 侧判定、未排除因素及对 S7 的影响 |
| [场景链结果与根因分析](references/scenario-chain-results-root-cause-20260925.md) | 看共享数据库连接缺陷如何从现象走到五证闭环，以及修复前后的对照 |

## 跨讲参考（公开脱敏版）

| 参考 | 用途 |
|---|---|
| [S1 压力数据底稿](../13-execution-evidence/references/s1-pressure-data-20261014v12real1.md) | 看压力现象环与七轮运行资格 |
| [S3 时间拆分底稿](../16-latency-split/references/s3-time-split-20261014v12real1.md) | 看轨迹环与逐笔时间对账 |
| [S4 全局观测底稿](../17-global-observability/references/s4-global-observability-20261014v12real1.md) | 看计数器环与四层资源同期对照 |
| [S5 定向验证底稿](../18-directed-trace-eval/references/s5-directed-validation-20261014v12real1.md) | 看机制环的 span 分解与七条可疑点判定 |
| [S7 优化回归底稿](../20-optimization-regression/references/s7-optimization-regression-20261014v12real1.md) | 看控制实验环与配对复测状态 |
| [RESAR 七步与专家团编排手册](../14-pressure-scenario-data/references/resar-orchestration-handbook.md) | 看七步职责、交接契约、审批路由与停止条件 |
| [正式轮压测报告 20261001](../16-latency-split/references/formal-load-test-report-20261001.md) | 看 Mock 正式轮的负结论与实验边界 |

## 使用方式与证据状态

1. 先分清楚性能现象、异常组件与瓶颈，不把最慢的组件直接写成瓶颈。
2. 五环逐环核对；缺哪一环就降级，并写明补证路径。
3. 结论分“已证实”“判断级”“候选”“未验证”，未排除因素随结论一起交付。
4. 控制实验必须在本轮条件下新跑，别轮结果不能拿来补本轮的环。
5. 系统侧没有查实瓶颈，也是一条可交付的结论；不为了凑结论放宽证据。

## 参考章节与使用说明

- 参考课程第 19 讲“先把三个术语分清楚：性能现象、异常组件、瓶颈”“五环证据链：缺一环就降级”“七条性能瓶颈可疑点逐条核对证据环”“系统里没查出瓶颈的依据，和四条还没排除的因素”“结论段怎么写，才交得出去”。
- 底稿中的示例读数取自客服工单案例真实模型轮次；历史根因分析取自同一案例的 Mock 阶段。两者口径不同，不直接比较绝对值。
- 正文与完整讲解请在专栏阅读。
