"""配置加载：合并 workflow_config.yaml 与环境变量。

案例三（Workflow+RAG）在案例一、二基础上提供：
- workflow：节点执行预算、超时、重试
- rag：检索模式、top_k、阈值、向量维度
- gates：六类门禁阈值与方向（与 门禁判定说明 GATE_RULES 对应）
- checkpoint：检查点存档与幂等
- fault：节点故障注入（关键路径 / 检查点恢复 / 自愈实验使用）
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "src" / "config" / "workflow_config.yaml"
PROMPTS_DIR = PROJECT_ROOT / "src" / "prompts"
DATA_DIR = PROJECT_ROOT.parent.parent / "testing" / "test-data"
REPORTS_DIR = PROJECT_ROOT / "reports"

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "flow.db"


class Settings:
    """聚合配置对象（环境变量优先于 YAML）。"""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_file = config_path
        with config_path.open("r", encoding="utf-8") as fh:
            self.raw = yaml.safe_load(fh) or {}

        agent = self.raw.get("agent", {}) or {}
        llm = self.raw.get("llm", {}) or {}
        routing = self.raw.get("routing", {}) or {}
        workflow_cfg = self.raw.get("workflow", {}) or {}
        rag_cfg = self.raw.get("rag", {}) or {}
        tools_cfg = self.raw.get("tools", {}) or {}
        cp_cfg = self.raw.get("checkpoint", {}) or {}
        fault_cfg = (self.raw.get("fault", {}) or {}).get("inject", {}) or {}
        gates_cfg = (self.raw.get("gates", {}) or {}).get("rules", {}) or {}
        slo_cfg = self.raw.get("slo", {}) or {}
        limits = self.raw.get("limits", {}) or {}
        monitoring = self.raw.get("monitoring", {}) or {}

        self.agent_name = agent.get("name", "客服工单Workflow+RAG")
        self.agent_version = agent.get("version", "3.0.0")
        self.model_name = os.environ.get("LLM_MODEL", agent.get("model", "mock"))
        self.temperature = float(agent.get("temperature", 0))
        self.max_tokens = int(agent.get("max_tokens", 2000))

        default_provider = llm.get("provider", "mock")
        self.llm_provider = os.environ.get("LLM_PROVIDER", default_provider).lower()
        self.llm_api_key = os.environ.get("LLM_API_KEY", llm.get("api_key", "")) or ""
        self.llm_base_url = os.environ.get("LLM_BASE_URL", llm.get("base_url", "")) or ""

        mock_cfg = llm.get("mock", {}) or {}
        self.mock_seed = int(os.environ.get("MOCK_SEED", mock_cfg.get("seed", 42)))
        self.intent_latency_ms = float(os.environ.get("MOCK_INTENT_LATENCY_MS", mock_cfg.get("intent_latency_ms", 550)))
        self.intent_jitter_ms = float(os.environ.get("MOCK_INTENT_JITTER_MS", mock_cfg.get("intent_jitter_ms", 100)))
        self.generation_latency_ms = float(os.environ.get("MOCK_GENERATION_LATENCY_MS", mock_cfg.get("generation_latency_ms", 600)))
        self.generation_jitter_ms = float(os.environ.get("MOCK_GENERATION_JITTER_MS", mock_cfg.get("generation_jitter_ms", 120)))
        self.clarification_latency_ms = float(os.environ.get("MOCK_CLARIFICATION_LATENCY_MS", mock_cfg.get("clarification_latency_ms", 500)))
        self.clarification_jitter_ms = float(os.environ.get("MOCK_CLARIFICATION_JITTER_MS", mock_cfg.get("clarification_jitter_ms", 100)))
        self.rag_search_latency_ms = float(os.environ.get("MOCK_RAG_SEARCH_LATENCY_MS", mock_cfg.get("rag_search_latency_ms", 50)))
        self.rag_search_jitter_ms = float(os.environ.get("MOCK_RAG_SEARCH_JITTER_MS", mock_cfg.get("rag_search_jitter_ms", 15)))
        self.tool_latency_ms = float(os.environ.get("MOCK_TOOL_LATENCY_MS", mock_cfg.get("tool_latency_ms", 40)))
        self.tool_jitter_ms = float(os.environ.get("MOCK_TOOL_JITTER_MS", mock_cfg.get("tool_jitter_ms", 10)))
        self.token_latency_ms = float(os.environ.get("MOCK_TOKEN_LATENCY_MS", mock_cfg.get("token_latency_ms", 40)))
        self.token_price_per_1k = float(os.environ.get("MOCK_TOKEN_PRICE_PER_1K", mock_cfg.get("token_price_per_1k", 0.15)))
        # Mock 规则版本（007 数据档战役整改）：缺省 legacy 保持冻结基线行为，
        # performance-v1.1 启用金额与复合诉求的确定性路径（与数据档 v1.1 配套）
        self.mock_rule_version = os.environ.get("MOCK_RULE_VERSION", "legacy").strip().lower()

        # 路由
        self.intent_edges = list(routing.get("intent_edges", []))
        self.clarification_intents = list(routing.get("clarification_intents", ["clarification"]))
        self.confidence_edge = dict(routing.get("confidence_edge", {}))
        self.confidence_threshold = float(routing.get("confidence_threshold", 0.8))

        # Workflow
        self.max_node_executions = int(workflow_cfg.get("max_node_executions", 10))
        self.node_timeout_ms = float(workflow_cfg.get("node_timeout_ms", 5000))
        self.max_retries = int(workflow_cfg.get("max_retries", 2))
        self.retry_backoff_ms = float(workflow_cfg.get("retry_backoff_ms", 50))

        # RAG
        self.knowledge_base = rag_cfg.get("knowledge_base", "customer-service-kb")
        self.search_mode = os.environ.get("RAG_SEARCH_MODE", rag_cfg.get("search_mode", "bm25"))
        self.top_k = int(os.environ.get("RAG_TOP_K", rag_cfg.get("top_k", 5)))
        self.score_threshold = float(os.environ.get("RAG_SCORE_THRESHOLD", rag_cfg.get("score_threshold", 0.3)))
        self.vector_top_k = int(rag_cfg.get("vector_top_k", 10))
        self.keyword_top_k = int(rag_cfg.get("keyword_top_k", 10))
        self.rerank = bool(rag_cfg.get("rerank", True))
        self.vector_dim = int(rag_cfg.get("vector_dim", 256))
        # 工具配置（query_order / query_logistics 超时与重试）
        self.tool_config = dict(tools_cfg) or {}

        # Checkpoint
        self.checkpoint_enabled = bool(cp_cfg.get("enabled", True))
        self.retry_idempotent = bool(cp_cfg.get("retry_idempotent", True))
        # 存储后端：memory（进程内，重启即丢）/ sqlite（持久化，跨重启续跑）
        self.checkpoint_store = os.environ.get(
            "FLOW_CHECKPOINT_STORE", cp_cfg.get("store", "memory")
        ).lower()
        self.checkpoint_db_path = Path(os.environ.get(
            "FLOW_CHECKPOINT_DB",
            str(cp_cfg.get("db_path") or (PROJECT_ROOT / "data" / "checkpoints.db")),
        ))
        # 检查点生命周期：成功终态即时清理 + 按存活时长/总量回收遗留检查点
        # 环境变量 FLOW_CHECKPOINT_PURGE_ON_COMPLETED / _GC_MAX_AGE_S / _GC_MAX_COUNT 优先
        self.checkpoint_purge_on_completed = os.environ.get(
            "FLOW_CHECKPOINT_PURGE_ON_COMPLETED",
            str(cp_cfg.get("purge_on_completed", True)),
        ).lower() in ("1", "true", "yes")
        self.checkpoint_gc_max_age_s = int(os.environ.get(
            "FLOW_CHECKPOINT_GC_MAX_AGE_S", cp_cfg.get("gc_max_age_s", 86400)
        ))
        self.checkpoint_gc_max_count = int(os.environ.get(
            "FLOW_CHECKPOINT_GC_MAX_COUNT", cp_cfg.get("gc_max_count", 1000)
        ))
        # GC 触发节流：每存档 N 次回收一次（不影响可恢复语义，不进指纹）
        self.checkpoint_gc_every_saves = int(os.environ.get(
            "FLOW_CHECKPOINT_GC_EVERY_SAVES", cp_cfg.get("gc_every_saves", 64)
        ))

        # 故障注入
        self.fault_enabled = bool(os.environ.get("FLOW_FAULT_ENABLED", str(fault_cfg.get("enabled", False))).lower() in ("1", "true", "yes"))
        self.fault_target = os.environ.get("FLOW_FAULT_TARGET", fault_cfg.get("target", ""))
        self.fault_mode = os.environ.get("FLOW_FAULT_MODE", fault_cfg.get("mode", ""))
        self.fault_latency_ms = float(os.environ.get("FLOW_FAULT_LATENCY_MS", fault_cfg.get("latency_ms", 3000)))
        self.fault_limit = int(os.environ.get("FLOW_FAULT_LIMIT", fault_cfg.get("limit", 0)))
        self.fault_node_id = os.environ.get("FLOW_FAULT_NODE_ID", fault_cfg.get("node_id", ""))

        # 门禁（B8：阈值支持环境变量覆盖，便于真实模型轮重定而不改 YAML；
        # 覆盖名形如 GATE_E2E_P90_THRESHOLD，threshold_source 记录生效来源）
        self.gate_rules = {}
        for name, rule in (gates_cfg or {}).items():
            r = dict(rule)
            r.setdefault("threshold", 0)
            override = os.environ.get(f"GATE_{str(name).upper()}_THRESHOLD")
            if override not in (None, ""):
                try:
                    r["threshold"] = float(override)
                except ValueError as exc:
                    raise ValueError(
                        f"GATE_{str(name).upper()}_THRESHOLD 须为数字，实际 {override!r}"
                    ) from exc
                r["threshold_source"] = "env"
            else:
                r["threshold_source"] = "yaml"
            self.gate_rules[name] = r

        # 时延 SLO（v2 流式口径，压测脚本与业务判定共用；课程正文对应段落）
        self.slo_e2e_ms = float(os.environ.get("SLO_E2E_MS", slo_cfg.get("e2e_ms", 8000)))
        self.slo_first_answer_ms = float(
            os.environ.get("SLO_FIRST_ANSWER_MS", slo_cfg.get("first_answer_ms", 2000))
        )
        # 真实模型口径（推理型模型含思考链，时延量级不同；课程正文对应段落）。
        # 与 mock 口径并存而非替换：两者测量对象不同，读数不可互比。
        _real = slo_cfg.get("real_model", {}) or {}
        self.slo_real_e2e_ms = float(
            os.environ.get("SLO_REAL_E2E_MS", _real.get("e2e_ms", 30000))
        )
        self.slo_real_first_answer_ms = float(
            os.environ.get("SLO_REAL_FIRST_ANSWER_MS", _real.get("first_answer_ms", 8000))
        )

        # 限制与监控
        self.max_iterations = int(limits.get("max_iterations", 10))
        self.request_timeout = float(limits.get("request_timeout", 30))
        # 有界工具执行池（v2：数据检索节点共享）
        self.tool_workers = int(limits.get("tool_workers", 4))
        self.tool_queue_size = int(limits.get("tool_queue_size", 16))
        self.max_tool_calls = int(limits.get("max_tool_calls", 5))
        # 上下文窗口上限：窗口占用率指标的分母（教学口径，不影响业务行为）
        self.context_window_tokens = int(
            os.environ.get(
                "CONTEXT_WINDOW_TOKENS", limits.get("context_window_tokens", 8192)
            )
        )
        self.enable_trace = bool(monitoring.get("enable_trace", True))
        self.enable_metrics = bool(monitoring.get("enable_metrics", True))
        self.log_level = monitoring.get("log_level", "info")

        # Langfuse 自托管观测导出
        obs = (self.raw.get("observability", {}) or {}).get("langfuse", {}) or {}
        self.langfuse_enabled = os.environ.get(
            "LANGFUSE_ENABLED", str(obs.get("enabled", False))
        ).lower() in ("1", "true", "yes")
        self.langfuse_host = os.environ.get(
            "LANGFUSE_HOST", obs.get("host", "http://localhost:3000")
        )
        self.langfuse_public_key = os.environ.get(
            "LANGFUSE_PUBLIC_KEY", obs.get("public_key", "")
        )
        self.langfuse_secret_key = os.environ.get(
            "LANGFUSE_SECRET_KEY", obs.get("secret_key", "")
        )
        self.langfuse_timeout_s = float(obs.get("timeout_s", 5))

        self.db_path = Path(os.environ.get("FLOW_DB_PATH", str(DEFAULT_DB_PATH)))
        self.test_data_dir = Path(os.environ.get("FLOW_TEST_DATA_DIR", str(DATA_DIR)))

        self.validate()

    def active_slo(self) -> tuple[float, float]:
        """按当前 llm_provider 返回生效的 (E2E, 首字) 时延门槛。

        mock 走 mock 口径；openai 等真实模型走 real_model 口径。
        两套口径测量对象不同，读数不可互比（课程正文对应段落）。
        """
        if self.llm_provider == "mock":
            return self.slo_e2e_ms, self.slo_first_answer_ms
        return self.slo_real_e2e_ms, self.slo_real_first_answer_ms

    @property
    def prompts_dir(self) -> Path:
        return PROMPTS_DIR

    @property
    def reports_dir(self) -> Path:
        return REPORTS_DIR

    @property
    def data_dir(self) -> Path:
        return self.test_data_dir

    def validate(self) -> None:
        """启动时拒绝非法配置（fail fast）。"""
        if self.llm_provider not in ("mock", "openai"):
            raise ValueError(
                f"llm.provider 必须是 mock / openai，实际为 {self.llm_provider!r}"
            )
        # 检索模式只接受实际实现的 bm25：防止以 BM25 执行却报告混合检索
        # （课程 RAG 口径说明；历史报告的 hybrid 为当时配置标签，实际算法为 BM25）
        if self.search_mode != "bm25":
            raise ValueError(
                f"rag.search_mode 仅支持 bm25（hybrid 未实现），"
                f"实际为 {self.search_mode!r}；请修改 workflow_config.yaml "
                f"或 RAG_SEARCH_MODE 环境变量"
            )
        if self.llm_provider == "openai" and not self.llm_base_url:
            raise ValueError("LLM_PROVIDER=openai 需要配置 LLM_BASE_URL")
        if self.max_node_executions <= 0:
            raise ValueError("workflow.max_node_executions 必须为正整数")
        if self.request_timeout <= 0:
            raise ValueError("limits.request_timeout 必须为正数")
        if self.tool_workers <= 0:
            raise ValueError("limits.tool_workers 必须为正整数")
        if self.tool_queue_size < 0:
            raise ValueError("limits.tool_queue_size 不能为负数")
        if self.context_window_tokens <= 0:
            raise ValueError("limits.context_window_tokens 必须为正整数")
        if self.max_tool_calls <= 0:
            raise ValueError("limits.max_tool_calls 必须为正整数")
        if self.mock_rule_version not in ("legacy", "performance-v1.1"):
            raise ValueError(
                "MOCK_RULE_VERSION 必须是 legacy / performance-v1.1，"
                f"实际为 {self.mock_rule_version!r}"
            )
        if self.langfuse_enabled and not (
            self.langfuse_host
            and self.langfuse_public_key
            and self.langfuse_secret_key
        ):
            raise ValueError(
                "LANGFUSE_ENABLED=true 需要同时配置 LANGFUSE_HOST、"
                "LANGFUSE_PUBLIC_KEY 与 LANGFUSE_SECRET_KEY"
            )

        if self.slo_e2e_ms <= 0 or self.slo_first_answer_ms <= 0:
            raise ValueError("slo.e2e_ms 与 slo.first_answer_ms 必须为正数")
        if self.slo_real_e2e_ms <= 0 or self.slo_real_first_answer_ms <= 0:
            raise ValueError("slo.real_model 的 e2e_ms 与 first_answer_ms 必须为正数")

    def to_fingerprint_fields(self) -> dict:
        return {
            "provider": self.llm_provider,
            "model": self.model_name,
            "architecture": "workflow+rag",
            "search_mode": self.search_mode,
            "top_k": self.top_k,
            "confidence_threshold": self.confidence_threshold,
            "token_price_per_1k": self.token_price_per_1k,
            "max_node_executions": self.max_node_executions,
            "max_tool_calls": self.max_tool_calls,
            "request_timeout": self.request_timeout,
            "tool_workers": self.tool_workers,
            "tool_queue_size": self.tool_queue_size,
            "mock_seed": self.mock_seed,
            "mock_rule_version": self.mock_rule_version,
            "fault_enabled": self.fault_enabled,
            "langfuse_enabled": self.langfuse_enabled,
            "slo_e2e_ms": self.slo_e2e_ms,
            "slo_first_answer_ms": self.slo_first_answer_ms,
            "slo_real_e2e_ms": self.slo_real_e2e_ms,
            "slo_real_first_answer_ms": self.slo_real_first_answer_ms,
            "checkpoint_store": self.checkpoint_store,
            # 保留策略影响"哪些运行还能续跑"，属于可恢复语义，进指纹；
            # gc_every_saves 只是触发节流，不改变保留语义，不进指纹。
            "checkpoint_purge_on_completed": self.checkpoint_purge_on_completed,
            "checkpoint_gc_max_age_s": self.checkpoint_gc_max_age_s,
            "checkpoint_gc_max_count": self.checkpoint_gc_max_count,
        }
