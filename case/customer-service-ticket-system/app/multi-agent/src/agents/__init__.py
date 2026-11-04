"""Multi-Agent 编排层：超管（Supervisor）+ 专业子 Agent 工作池。

案例二核心组件：
- circuit_breaker.py：按子 Agent 独立的熔断器（5 次连续失败 → 熔断 30s）
- worker.py：专业子 Agent（order/logistics/knowledge/validation），
  每位独立消耗一次 LLM 决策后再执行工具/校验
- supervisor.py：超管引擎（意图识别 → Agent 任务分解 → 并行扇出 → 聚合 →
  校验 → 答复生成），输出与案例一 AgentResult 兼容的结构化结果与指标
"""