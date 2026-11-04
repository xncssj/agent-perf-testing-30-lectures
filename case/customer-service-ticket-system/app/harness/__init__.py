"""Harness 共享包：客服工单案例三个阶段共用的运行机制（单一事实源）。

由阶段二接入时从阶段一 src/harness 抽取而来（共享 Harness 设计说明 第 3.2 节：
"阶段二接入时再抽取可安装的公共包"）。各阶段通过仓库相对路径引用本包，
阶段一保留 `src/harness` 等薄适配 shim 转发到本包。

模块职责：
- contracts：消息、调用、观察、运行输入输出与事件契约；
- budget：轮次、工具尝试与 deadline 预算（含多 Worker 原子分配）；
- params / registry：业务工具参数模型、Schema 与注册表；
- tool_executor：有界工具执行器（校验/超时/重试/过载）；
- observation：事件编号、用量与耗时汇总；
- policy：客服策略出口（澄清/无结果/人工处理建议）；
- runtime：单 Agent（ReAct）异步运行循环；
- providers / mock_provider / openai_provider：模型接入契约与实现；
- langfuse_exporter：Langfuse 观测导出（v4 信封格式）。
"""
