# agent-perf-testing-30-lectures

**Author: 7DGroup**

> 《Agent 性能测试实战30讲》课程配套仓库（拟上架极客时间，链接上架后在此更新；课程动态见微信公众号 7DGroup）。
> 范围：贯穿案例客服工单系统、测试数据集、k6 压测套件、脚本与可复用模板；配套资产按讲次逐步开放，课程正文请在专栏阅读。

<p align="center">
  <img src="https://img.shields.io/badge/k6-v2.x-orange" alt="k6">
  <img src="https://img.shields.io/badge/Python-3.x%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/Docker-compose-blue" alt="Docker">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey" alt="Platform">
</p>

## 📌 课程信息

| 项目 | 说明 |
|------|------|
| 课程系列 | 《Agent 性能测试实战30讲》：开篇词 + 导学 + 30 讲正文 + 结束语 + 3 个附录，拟上架极客时间 |
| 课程定位 | 面向测试工程师、Agent 开发工程师和质量负责人的项目实战课：从性能需求到上线验收，用 RESAR 七步法完整做完一个 Agent 性能项目 |
| 贯穿案例 | 一个客服工单系统 + 两次架构升级：Single Agent → Multi-Agent → Workflow/Flow + RAG，业务为「查询并答复指定订单的配送进度」 |
| 核心指标 | TTFT、TPOT、Goodput（有效吞吐）、成功业务成本与四级业务判定（请求成功 / 流程完成 / 业务完成 / 结果合格） |
| 仓库用途 | 存放课程配套资产：案例系统、测试数据集、k6 压测套件、脚本与可复用模板，按讲组织 |
| 当前状态 | 已开放导学与第 01—03 讲的配套模板；项目源码、测试数据与脚本随对应讲次逐步开放 |

## 📚 课程篇目

| 讲次 | 标题 | 章节 |
|---|---|---|
| 开篇词 | Agent 的性能好不好，要拿证据说话 | 开篇 |
| [导学](lectures/00-guide/) | 为什么这门课要带你完整做完一个 Agent 性能项目？ | 导学 |
| [01](lectures/01-http-200-not-business-done/) | 接口返回 200，为什么 Agent 仍然没有完成业务？ | 一、必要知识储备 |
| [02](lectures/02-architecture-performance-risks/) | Prompt、Workflow/Flow、Single Agent、Multi-Agent 都是调模型，性能风险为什么完全不同？ | 一、必要知识储备 |
| [03](lectures/03-test-object-layering/) | 测一次请求还是测完整业务，Agent 的测试对象怎么确定？ | 一、必要知识储备 |
| 04 | TTFT、TPOT、Goodput、质量和成本指标应该怎么选？ | 一、必要知识储备 |
| 05 | 同一个 Agent 每次结果都不同，怎样建立可信的性能基线？ | 一、必要知识储备 |
| 06 | 并发还没有到峰值，为什么 Agent 已经开始退化？ | 一、必要知识储备 |
| 07 | 服务没有报错，怎样发现 Agent 正在悄悄失稳？ | 一、必要知识储备 |
| 08 | 老板只说客服工单 Agent 要「快、稳、准、省」，怎样变成可验收的需求？ | 二、主项目需求与方案设计 |
| 09 | 模型、查询工具和业务系统组成一条链，测试范围应该画到哪里？ | 二、主项目需求与方案设计 |
| 10 | 需求和架构已经明确，测试方案、数据和环境应该怎样准备？ | 二、主项目需求与方案设计 |
| 11 | 基准、容量和稳定性场景怎样组合，才能覆盖项目风险？ | 三、主项目场景、脚本与测试执行 |
| 12 | 流式 Agent 的压测脚本怎样采集 TTFT、TPOT、轨迹和业务结果？ | 三、主项目场景、脚本与测试执行 |
| 13 | 测试执行过程中记录什么，才能让结果经得住质疑？ | 三、主项目场景、脚本与测试执行 |
| 14 | 第一步：怎样获取并读懂业务压力场景数据？ | 四、RESAR 七步分析与优化 |
| 15 | 第二步：怎样分析 Agent 执行架构并找到高风险路径？ | 四、RESAR 七步分析与优化 |
| 16 | 第三步：一笔业务耗时几十秒，怎样拆分端到端业务时延与服务端耗时？ | 四、RESAR 七步分析与优化 |
| 17 | 第四步：监控指标很多，怎样通过全局可观测性缩小范围？ | 四、RESAR 七步分析与优化 |
| 18 | 第五步：怎样用定向 Trace 与 Eval 验证性能假设？ | 四、RESAR 七步分析与优化 |
| 19 | 第六步：最慢的组件就一定是 Agent 性能瓶颈吗？ | 四、RESAR 七步分析与优化 |
| 20 | 第七步：怎样确定优化方案并完成可信的回归验证？ | 四、RESAR 七步分析与优化 |
| 21 | 从 Single Agent 扩展到 Multi-Agent，测试方案哪些必须重做？ | 五、同一系统架构升级 |
| 22 | 一个 Agent 超时，为什么整个 Multi-Agent 系统都会被拖垮？ | 五、同一系统架构升级 |
| 23 | 工单系统升级为 Workflow/Flow + RAG 后，测试对象发生了什么变化？ | 五、同一系统架构升级 |
| 24 | 怎样把一次 Workflow/Flow + RAG 工单系统性能测试变成持续评估门禁？ | 五、同一系统架构升级 |
| 25 | 工具这么多，怎样从项目问题反推出最小工具组合？ | 六、工具选择与平台建设 |
| 26 | 压测、Trace、Eval 数据怎样串成证据链并完成平台建设？ | 六、工具选择与平台建设 |
| 27 | 开工前最容易踩的十大坑，哪些必须提前避开？ | 七、项目落地与演进 |
| 28 | 只有两周时间，怎样完成第一版 Agent 性能测试 MVP？ | 七、项目落地与演进 |
| 29 | 准备上线了，怎样完成一次可信的性能与稳定性验收？ | 七、项目落地与演进 |
| 30 | 运行三个月后，怎样制定从脚本到门禁和平台的演进路线？ | 七、项目落地与演进 |
| 结束语 | 工具会变，怎样保留不会过时的项目能力？ | 结语 |
| 附录 | 术语解释表、指标计算公式表、性能测试方案示例 | 附录 |

正文不在本仓库发布。配套资产按讲次逐步开放，未发布讲目不提前放置半成品；各讲实际引用的路径以正文为准。

## 📂 配套资料目录

| 讲次 | 目录 | 已开放资产 |
|---|---|---|
| 导学 | [lectures/00-guide](lectures/00-guide/) | 暂无（概念篇） |
| 01 | [lectures/01-http-200-not-business-done](lectures/01-http-200-not-business-done/) | 四级业务判定表、单笔业务复核记录、Goodput 与成功业务成本台账 |
| 02 | [lectures/02-architecture-performance-risks](lectures/02-architecture-performance-risks/) | 架构形态与性能风险速查表、风险假设记录 |
| 03 | [lectures/03-test-object-layering](lectures/03-test-object-layering/) | 分层测试对象表、时延口径自查表、时间口径记录 |

## 🧭 使用方式

1. 在专栏阅读正文，按正文中的配套资料提示找到对应讲次目录。
2. 本仓库只提供可运行或可复用的资产，不重复正文讲解。
3. 各讲资产以该讲目录内的 README 为准。
4. 本仓库资料仅限已购学员学习使用，请勿对外传播或用于商业再分发。

## 📄 相关链接

- [k6 官方文档](https://grafana.com/docs/k6/latest/)
- [grafana/k6 GitHub 仓库](https://github.com/grafana/k6)
- [Langfuse（LLM 观测平台）](https://github.com/langfuse/langfuse)
- [Prometheus 监控系统](https://prometheus.io/)
- 课程《Agent 性能测试实战30讲》：极客时间上架后在此更新链接；上架前请关注微信公众号 7DGroup 获取动态

## 📮 关注公众号

微信公众号 **7DGroup**：性能工程实战内容，课程更新与配套资料动态。

<p align="center">
  <img src="images/wechat-7dgroup-qr.jpg" width="180" alt="7DGroup 微信公众号二维码">
</p>

- 📣 微信公众号：**7DGroup**
- 📚 课程配套代码组织：[github.com/xncssj](https://github.com/xncssj)（本仓库所在组织）
- 🏢 主组织：[github.com/7dgroup-ai](https://github.com/7dgroup-ai)
