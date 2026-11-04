"""配置加载：合并 agent_config.yaml 与环境变量（Multi-Agent v2）。

案例二（Multi-Agent）在案例一基础上新增 orchestration / fault 配置段：
- orchestration：超管规划与并行扇出的超时、重试、熔断、并发上限、降级开关
- fault：故障注入（级联故障 / 恢复测试使用），支持按 Agent 或 LLM 注入

v2 新增（共享 Harness 设计说明 第 8.1 节 + 阶段一同款口径）：
- limits.tool_workers / tool_queue_size：有界工具执行池；
- observability.langfuse：自托管 Langfuse 观测导出配置；
- 非法模式、非正轮次/超时和负重试次数在启动时拒绝（fail fast）。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "src" / "config" / "agent_config.yaml"
PROMPTS_DIR = PROJECT_ROOT / "src" / "prompts"
# 共享基准数据集：三阶段单一事实源（见 testing/test-data/MANIFEST.md）
DATA_DIR = PROJECT_ROOT.parent.parent / "testing" / "test-data"
REPORTS_DIR = PROJECT_ROOT / "reports"

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "agent.db"


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """聚合配置对象（环境变量优先于 YAML，便于压测按 run 场景切换）。"""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_file = config_path
        with config_path.open("r", encoding="utf-8") as fh:
            self.raw = yaml.safe_load(fh) or {}

        agent = self.raw.get("agent", {}) or {}
        tools = self.raw.get("tools", {}) or {}
        limits = self.raw.get("limits", {}) or {}
        monitoring = self.raw.get("monitoring", {}) or {}
        llm = self.raw.get("llm", {}) or {}
        orchestration = self.raw.get("orchestration", {}) or {}
        fault = self.raw.get("fault", {}) or {}
        obs = (self.raw.get("observability", {}) or {}).get("langfuse", {}) or {}

        # Agent 基础
        self.agent_name = agent.get("name", "客服工单Multi-Agent")
        self.agent_version = agent.get("version", "2.1.0")
        self.model_name = os.environ.get("LLM_MODEL", agent.get("model", "mock"))
        self.temperature = float(agent.get("temperature", 0))
        self.max_tokens = int(agent.get("max_tokens", 2000))
        self.streaming = bool(agent.get("streaming", True))
        # 流式接口模式：real=真实流式（片段计时实测）；
        # replay=回放校准（仅用于采集器时序校准，证据类型标注 replay）
        self.stream_mode = os.environ.get(
            "STREAM_MODE", agent.get("stream_mode", "real")
        ).lower()

        # LLM provider：环境变量 > YAML > 默认 mock
        default_provider = llm.get("provider", "mock")
        self.llm_provider = os.environ.get("LLM_PROVIDER", default_provider).lower()
        self.llm_api_key = os.environ.get("LLM_API_KEY", llm.get("api_key", "")) or ""
        self.llm_base_url = os.environ.get(
            "LLM_BASE_URL", llm.get("base_url", "")
        ) or ""
        self.llm_stream_include_usage = bool(
            llm.get("stream_include_usage", True)
        )

        # Mock 延迟模型参数（无模型环境下可复现的性能基线）
        mock_cfg = llm.get("mock", {}) or {}
        self.mock_seed = int(os.environ.get("MOCK_SEED", mock_cfg.get("seed", 42)))
        self.mock_plan_latency_ms = float(
            os.environ.get(
                "MOCK_PLAN_LATENCY_MS", mock_cfg.get("plan_latency_ms", 600)
            )
        )
        self.mock_plan_jitter_ms = float(
            os.environ.get(
                "MOCK_PLAN_JITTER_MS", mock_cfg.get("plan_jitter_ms", 120)
            )
        )
        # 子 Agent 任务决策延迟（Multi-Agent）：每位专业 Agent 独立决策一次
        self.mock_agent_latency_ms = float(
            os.environ.get(
                "MOCK_AGENT_LATENCY_MS", mock_cfg.get("agent_latency_ms", 350)
            )
        )
        self.mock_agent_jitter_ms = float(
            os.environ.get(
                "MOCK_AGENT_JITTER_MS", mock_cfg.get("agent_jitter_ms", 70)
            )
        )
        # 聚合校验 Agent 决策延迟（Multi-Agent）
        self.mock_validation_latency_ms = float(
            os.environ.get(
                "MOCK_VALIDATION_LATENCY_MS",
                mock_cfg.get("validation_latency_ms", 200),
            )
        )
        self.mock_token_latency_ms = float(
            os.environ.get(
                "MOCK_TOKEN_LATENCY_MS", mock_cfg.get("token_latency_ms", 40)
            )
        )
        # Mock 规则版本（007 数据档战役整改）：缺省 legacy 保持冻结基线行为，
        # performance-v1.1 启用金额与复合诉求的确定性路径（与数据档 v1.1 配套）
        self.mock_rule_version = os.environ.get("MOCK_RULE_VERSION", "legacy").strip().lower()

        # 工具配置
        self.tool_timeouts = dict(tools)

        # 限制
        self.max_iterations = int(limits.get("max_iterations", 10))
        self.max_tool_calls = int(limits.get("max_tool_calls", 5))
        self.request_timeout = float(limits.get("request_timeout", 30))
        # 有界工具执行池：并发线程 + 等待槽
        self.tool_workers = int(limits.get("tool_workers", 4))
        self.tool_queue_size = int(limits.get("tool_queue_size", 16))
        # 上下文窗口上限：窗口占用率指标的分母（教学口径，不影响业务行为）
        self.context_window_tokens = int(
            os.environ.get(
                "CONTEXT_WINDOW_TOKENS", limits.get("context_window_tokens", 8192)
            )
        )

        # 监控
        self.enable_trace = bool(monitoring.get("enable_trace", True))
        self.enable_metrics = bool(monitoring.get("enable_metrics", True))
        self.log_level = monitoring.get("log_level", "info")

        # Langfuse 自托管观测导出
        self.langfuse_enabled = _as_bool(
            os.environ.get("LANGFUSE_ENABLED", obs.get("enabled", False))
        )
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

        # 时延 SLO（B12，2026-11-03 阶段二三真实模型轮）：字段名与阶段三对齐，
        # 供 lib.sh 的 /version 口径解析与三层核对使用。阶段二首字默认不设门槛（0），
        # 需要开门禁时由 --caliber-env FIRST_ANSWER_SLO_MS 显式注入并登记。
        slo_cfg = self.raw.get("slo", {}) or {}
        self.slo_e2e_ms = float(os.environ.get("SLO_E2E_MS", slo_cfg.get("e2e_ms", 10000)))
        self.slo_first_answer_ms = float(
            os.environ.get("SLO_FIRST_ANSWER_MS", slo_cfg.get("first_answer_ms", 0))
        )
        real_slo = slo_cfg.get("real_model", {}) or {}
        self.slo_real_e2e_ms = float(
            os.environ.get("SLO_REAL_E2E_MS", real_slo.get("e2e_ms", 30000))
        )
        self.slo_real_first_answer_ms = float(
            os.environ.get("SLO_REAL_FIRST_ANSWER_MS", real_slo.get("first_answer_ms", 0))
        )

        # Multi-Agent 编排参数（案例二）
        orch = orchestration.get("supervisor", {}) or {}
        self.supervisor_timeout_ms = float(
            os.environ.get(
                "SUPERVISOR_TIMEOUT_MS", orch.get("timeout_ms", 8000)
            )
        )
        self.agent_timeout_ms = float(
            os.environ.get("AGENT_TIMEOUT_MS", orch.get("agent_timeout_ms", 3000))
        )
        self.max_retries = int(
            os.environ.get("AGENT_MAX_RETRIES", orch.get("max_retries", 3))
        )
        self.retry_backoff_ms = float(
            os.environ.get(
                "AGENT_RETRY_BACKOFF_MS", orch.get("retry_backoff_ms", 50)
            )
        )
        self.breaker_threshold = int(
            os.environ.get(
                "AGENT_BREAKER_THRESHOLD", orch.get("breaker_threshold", 5)
            )
        )
        self.breaker_window_ms = float(
            os.environ.get(
                "AGENT_BREAKER_WINDOW_MS", orch.get("breaker_window_ms", 30000)
            )
        )
        self.parallel_limit = int(
            os.environ.get("AGENT_PARALLEL_LIMIT", orch.get("parallel_limit", 8))
        )
        self.degradation_enabled = _as_bool(
            os.environ.get(
                "AGENT_DEGRADATION", str(orch.get("degradation", True))
            )
        )

        # 故障注入配置（级联故障 / 恢复测试场景）
        fault_cfg = fault.get("inject", {}) or {}
        self.fault_enabled = _as_bool(
            os.environ.get(
                "AGENT_FAULT_ENABLED", str(fault_cfg.get("enabled", False))
            )
        )
        self.fault_target = os.environ.get(
            "AGENT_FAULT_TARGET", fault_cfg.get("target", "")
        )
        self.fault_mode = os.environ.get("AGENT_FAULT_MODE", fault_cfg.get("mode", ""))
        self.fault_latency_ms = float(
            os.environ.get(
                "AGENT_FAULT_LATENCY_MS", fault_cfg.get("latency_ms", 3000)
            )
        )
        # 故障自愈次数：注入前 limit 次后停止（恢复测试用），0=持续注入
        self.fault_limit = int(
            os.environ.get("AGENT_FAULT_LIMIT", fault_cfg.get("limit", 0))
        )

        # 数据与环境
        self.db_path = Path(os.environ.get("AGENT_DB_PATH", str(DEFAULT_DB_PATH)))
        self.test_data_dir = Path(
            os.environ.get("AGENT_TEST_DATA_DIR", str(DATA_DIR))
        )

        self.validate()

    def validate(self) -> None:
        """启动时拒绝非法配置：不静默回退到另一个模式。"""
        if self.llm_provider not in ("mock", "openai"):
            raise ValueError(
                f"llm.provider 必须是 mock / openai，实际为 {self.llm_provider!r}"
            )
        if self.llm_provider == "openai" and not self.llm_base_url:
            raise ValueError("LLM_PROVIDER=openai 需要配置 LLM_BASE_URL")
        if self.max_iterations <= 0:
            raise ValueError("limits.max_iterations 必须为正整数")
        if self.max_tool_calls <= 0:
            raise ValueError("limits.max_tool_calls 必须为正整数")
        if self.request_timeout <= 0:
            raise ValueError("limits.request_timeout 必须为正数")
        if self.tool_workers <= 0:
            raise ValueError("limits.tool_workers 必须为正整数")
        if self.tool_queue_size < 0:
            raise ValueError("limits.tool_queue_size 不能为负数")
        if self.context_window_tokens <= 0:
            raise ValueError("limits.context_window_tokens 必须为正整数")
        if self.stream_mode not in ("real", "replay"):
            raise ValueError(
                f"agent.stream_mode 必须是 real / replay，实际为 {self.stream_mode!r}"
            )

        if self.mock_rule_version not in ("legacy", "performance-v1.1"):
            raise ValueError(
                "MOCK_RULE_VERSION 必须是 legacy / performance-v1.1，"
                f"实际为 {self.mock_rule_version!r}"
            )
        if self.max_retries < 0:
            raise ValueError("orchestration.supervisor.max_retries 不能为负数")
        if self.agent_timeout_ms <= 0:
            raise ValueError("orchestration.supervisor.agent_timeout_ms 必须为正数")
        if self.parallel_limit <= 0:
            raise ValueError("orchestration.supervisor.parallel_limit 必须为正整数")
        for name, cfg in (self.tool_timeouts or {}).items():
            retry = int(cfg.get("retry", 0))
            if retry < 0:
                raise ValueError(f"工具 {name} 的 retry 不能为负数")
            timeout = float(cfg.get("timeout", 1))
            if timeout <= 0:
                raise ValueError(f"工具 {name} 的 timeout 必须为正数")
        if self.langfuse_enabled and not (
            self.langfuse_host
            and self.langfuse_public_key
            and self.langfuse_secret_key
        ):
            raise ValueError(
                "LANGFUSE_ENABLED=true 需要同时配置 LANGFUSE_HOST、"
                "LANGFUSE_PUBLIC_KEY 与 LANGFUSE_SECRET_KEY"
            )
        if self.slo_e2e_ms <= 0 or self.slo_real_e2e_ms <= 0:
            raise ValueError("slo.e2e_ms / slo.real_model.e2e_ms 必须为正数")
        if self.slo_first_answer_ms < 0 or self.slo_real_first_answer_ms < 0:
            raise ValueError("slo 首字门槛不能为负数（0 表示本阶段不设门槛）")

    @property
    def prompts_dir(self) -> Path:
        return PROMPTS_DIR

    @property
    def data_dir(self) -> Path:
        return self.test_data_dir

    @property
    def reports_dir(self) -> Path:
        return REPORTS_DIR

    def tool_config(self, name: str) -> dict:
        """返回单个工具配置，未配置时给出默认值。"""
        return dict((self.tool_timeouts or {}).get(name) or {})

    def to_fingerprint_fields(self) -> dict:
        """返回进入版本指纹的配置字段子集（协议模式与有效预算）。"""
        return {
            "provider": self.llm_provider,
            "model": self.model_name,
            "max_iterations": self.max_iterations,
            "max_tool_calls": self.max_tool_calls,
            "request_timeout": self.request_timeout,
            "tool_workers": self.tool_workers,
            "tool_queue_size": self.tool_queue_size,
            "stream_mode": self.stream_mode,
            "mock_seed": self.mock_seed,
            "mock_rule_version": self.mock_rule_version,
            "parallel_limit": self.parallel_limit,
            "agent_timeout_ms": self.agent_timeout_ms,
            "max_retries": self.max_retries,
            "degradation_enabled": self.degradation_enabled,
            "fault_enabled": self.fault_enabled,
            "slo_e2e_ms": self.slo_e2e_ms,
            "slo_first_answer_ms": self.slo_first_answer_ms,
            "slo_real_e2e_ms": self.slo_real_e2e_ms,
            "slo_real_first_answer_ms": self.slo_real_first_answer_ms,
            "orchestration": "multi-agent",
            "langfuse_enabled": self.langfuse_enabled,
        }
