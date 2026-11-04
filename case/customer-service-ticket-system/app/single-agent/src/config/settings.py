"""配置加载：合并 agent_config.yaml 与环境变量，供服务端与压测脚本共同使用。

v2 新增（共享 Harness 设计说明 第 5.2 节）：
- llm.tool_calling_mode：原生工具 / JSON 兼容协议选择（LLM_TOOL_CALLING_MODE 覆盖）；
- limits.tool_workers / tool_queue_size：有界工具执行池；
- observability.langfuse：自托管 Langfuse 观测导出配置。
非法模式、非正轮次/超时和负重试次数在启动时拒绝（fail fast）。
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
# 镜像内共享基准数据目录（app/single-agent/Dockerfile 将 testing/test-data
# COPY 到案例根/test-data）：切档 overlay 把 AGENT_TEST_DATA_DIR 指向数据档、
# 而档内缺 tickets.json 时，工作台目录接口回落到此，避免负载预览/客户库
# 静默空看板（2026-10-14 实测）。本地仓库布局下该路径不存在，回落不触发
FALLBACK_TEST_DATA_DIR = PROJECT_ROOT.parent.parent / "test-data"
REPORTS_DIR = PROJECT_ROOT / "reports"

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "agent.db"

TOOL_CALLING_MODES = ("native", "json")


class Settings:
    """聚合配置对象。

    - 文件配置：来自 src/config/agent_config.yaml
    - 环境配置：LLM_API_KEY、LLM_BASE_URL、LLM_PROVIDER、LLM_TOOL_CALLING_MODE、
      LANGFUSE_*、AGENT_DB_PATH 等
    环境变量优先于 YAML，便于压测时按 run 场景切换模型 provider。
    """

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_file = config_path
        with config_path.open("r", encoding="utf-8") as fh:
            self.raw = yaml.safe_load(fh) or {}

        agent = self.raw.get("agent", {}) or {}
        tools = self.raw.get("tools", {}) or {}
        limits = self.raw.get("limits", {}) or {}
        monitoring = self.raw.get("monitoring", {}) or {}
        llm = self.raw.get("llm", {}) or {}
        java = self.raw.get("java_service", {}) or {}
        obs = (self.raw.get("observability", {}) or {}).get("langfuse", {}) or {}

        # Agent 基础
        self.agent_name = agent.get("name", "客服工单Agent")
        self.agent_version = agent.get("version", "2.0.0")
        self.model_name = os.environ.get("LLM_MODEL", agent.get("model", "mock"))
        self.temperature = float(agent.get("temperature", 0))
        self.max_tokens = int(agent.get("max_tokens", 2000))
        self.streaming = bool(agent.get("streaming", True))
        # 流式接口模式：real=真实流式（实测片段计时）；
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
        # 工具调用协议：native=原生 tool_calls；json=决策提示词 JSON 兼容
        self.llm_tool_calling_mode = os.environ.get(
            "LLM_TOOL_CALLING_MODE", llm.get("tool_calling_mode", "native")
        ).lower()
        # 推理档位（S7R-R2 对照参数，2026-10-14）：OpenAI 兼容 reasoning_effort；
        # 空=不传（端点默认，与既有行为等价）；low/high/max 经 validate 放行
        self.llm_reasoning_effort = (
            os.environ.get("LLM_REASONING_EFFORT", llm.get("reasoning_effort", "")) or ""
        ).lower()
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

        # Java 订单物流微服务（java-order-logistics）HTTP 链路：
        # enabled=true 时 query_order/query_logistics 经 HTTP 调用 Java 微服务
        # （多一跳 HTTP 与 Java 侧 HikariCP，性能基线与直读模式不可混用）；
        # false 时进程内直读快照库。环境变量优先于 YAML。
        self.java_service = {
            "enabled": _as_bool(
                os.environ.get("JAVA_SERVICE_ENABLED", java.get("enabled", False))
            ),
            "base_url": os.environ.get("JAVA_SERVICE_URL", java.get("base_url", "")).rstrip("/"),
        }

        # 数据与环境
        self.db_path = Path(os.environ.get("AGENT_DB_PATH", str(DEFAULT_DB_PATH)))
        self.db_dialect = os.environ.get("DB_DIALECT", "sqlite").lower()
        self.mysql = {
            "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.environ.get("MYSQL_PORT", "3306")),
            "user": os.environ.get("MYSQL_USER", "root"),
            "password": os.environ.get("MYSQL_PASSWORD", "change-me"),
            "database": os.environ.get("MYSQL_DATABASE", "cs_agent"),
        }
        self.test_data_dir = Path(
            os.environ.get("AGENT_TEST_DATA_DIR", str(DATA_DIR))
        )
        self.fallback_test_data_dir = FALLBACK_TEST_DATA_DIR

        self.validate()

    def validate(self) -> None:
        """启动时拒绝非法配置：不静默回退到另一个模式。"""
        if self.llm_tool_calling_mode not in TOOL_CALLING_MODES:
            raise ValueError(
                f"llm.tool_calling_mode 必须是 {' / '.join(TOOL_CALLING_MODES)}，"
                f"实际为 {self.llm_tool_calling_mode!r}"
            )
        if self.llm_reasoning_effort and self.llm_reasoning_effort not in ("low", "high", "max"):
            raise ValueError(
                f"LLM_REASONING_EFFORT 须为 low/high/max 或留空，实际为 {self.llm_reasoning_effort!r}"
            )
        if self.llm_provider not in ("mock", "openai"):
            raise ValueError(
                f"llm.provider 必须是 mock / openai，实际为 {self.llm_provider!r}"
            )
        if self.llm_provider == "openai" and not (
            self.llm_base_url and self.llm_api_key
        ):
            raise ValueError(
                "LLM_PROVIDER=openai 需要同时配置 LLM_BASE_URL 与 LLM_API_KEY"
            )
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
        if self.db_dialect not in ("sqlite", "mysql"):
            raise ValueError(
                f"DB_DIALECT 必须是 sqlite / mysql，实际为 {self.db_dialect!r}"
            )
        if self.java_service["enabled"] and not self.java_service["base_url"]:
            raise ValueError(
                "JAVA_SERVICE_ENABLED=true 需要同时配置 JAVA_SERVICE_URL "
                "（或 yaml java_service.base_url）"
            )
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
            "tool_calling_mode": self.llm_tool_calling_mode,
            "max_iterations": self.max_iterations,
            "max_tool_calls": self.max_tool_calls,
            "request_timeout": self.request_timeout,
            "tool_workers": self.tool_workers,
            "tool_queue_size": self.tool_queue_size,
            "stream_mode": self.stream_mode,
            "mock_seed": self.mock_seed,
            "mock_rule_version": self.mock_rule_version,
            "langfuse_enabled": self.langfuse_enabled,
            # 链路口径：订单/物流查询走 Java 微服务 HTTP 还是进程内直读快照库，
            # 两种口径的时延分布不可比，指纹必须区分（见 README「Java 微服务链路」）
            "java_service_enabled": self.java_service["enabled"],
        }


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
