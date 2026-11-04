"""版本指纹工具。

版本指纹用于固定被测系统、模型、Prompt、数据和依赖的环境版本，
保证性能基线可复现、可回溯（课程第 05、10 讲要求）。

v2 扩展（共享 Harness 设计说明 第 7.3 节）：指纹至少包含 Harness 与适配代码哈希、
依赖文件、Prompt、共享数据哈希、协议模式、有效预算、Mock seed 及模型标识；
共享数据使用相对于案例根目录的稳定路径标识，不写入机器路径和凭据。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

APP_VERSION = "2.0.0"
APP_NAME = "customer-service-agent"

# 进入指纹的代码与依赖文件（相对阶段一根目录）
CODE_FILES = [
    "requirements.txt",
    "src/config/agent_config.yaml",
    "src/prompts/system_prompt.txt",
    "src/prompts/tool_selection.txt",
    "src/prompts/response_gen.txt",
    "src/main.py",
    "src/harness/contracts.py",
    "src/harness/budget.py",
    "src/harness/tool_executor.py",
    "src/harness/observation.py",
    "src/harness/runtime.py",
    "src/harness/langfuse_exporter.py",
    "src/agent/core.py",
    "src/agent/policy.py",
    "src/agent/prompt_manager.py",
    "src/llm/base.py",
    "src/llm/mock_provider.py",
    "src/llm/openai_provider.py",
    "src/tools/base.py",
    "src/tools/params.py",
    "src/tools/registry.py",
    "src/tools/order_tool.py",
    "src/tools/logistics_tool.py",
    "src/tools/knowledge_tool.py",
    "src/services/agent_service.py",
    "src/api/routes.py",
    "src/models/schemas.py",
    "src/models/database.py",
    "src/config/settings.py",
    "src/utils/version.py",
]

# 共享数据文件 -> 指纹中的稳定路径标签（文件路径相对所选 data_dir）
SHARED_DATA_FILES = [
    ("orders.json", "orders.json"),
    ("logistics.json", "logistics.json"),
    ("tickets.json", "tickets.json"),
    ("knowledge-base/faq.json", "knowledge-base/faq.json"),
    ("knowledge-base/policies.json", "knowledge-base/policies.json"),
    ("knowledge-base/products.json", "knowledge-base/products.json"),
]


def file_sha256(path: Path) -> str:
    """计算单文件 SHA-256，用于校验依赖文件未变更。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_version_fingerprint(
    project_root: Path,
    extra_files: list[Path] | None = None,
    extra_fields: dict | None = None,
) -> dict:
    """生成版本指纹（v1 兼容入口：仅配置、Prompt 与显式传入文件）。"""
    files = [project_root / "src/config/agent_config.yaml"]
    files += [project_root / "src/prompts" / n
              for n in ("system_prompt.txt", "tool_selection.txt", "response_gen.txt")]
    files += [f for f in (extra_files or []) if f is not None and f.exists()]

    hashes: dict[str, str] = {}
    for f in files:
        if f.exists():
            hashes[str(f.relative_to(project_root))] = file_sha256(f)

    payload = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "file_hashes": hashes,
        **(extra_fields or {}),
    }
    payload["fingerprint"] = _digest(payload)
    return payload


def build_run_fingerprint(project_root: Path, settings, data_dir: Path | None = None) -> dict:
    """生成 v2 运行指纹：代码与依赖哈希 + 共享数据哈希 + 有效配置字段。

    settings 用于携带协议模式、有效预算、Mock seed 与模型标识；
    data_dir 缺省时使用 settings.test_data_dir。
    """
    hashes: dict[str, str] = {}
    for rel in CODE_FILES:
        f = project_root / rel
        if f.exists():
            hashes[rel] = file_sha256(f)
    data_dir = data_dir or settings.test_data_dir
    for rel, label in SHARED_DATA_FILES:
        f = data_dir / rel
        if f.exists():
            hashes[f"test-data/{label}"] = file_sha256(f)

    payload = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "file_hashes": hashes,
        **settings.to_fingerprint_fields(),
    }
    payload["fingerprint"] = _digest(payload)
    return payload


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def new_run_id(fingerprint: str | None = None) -> str:
    """生成批次/报告 ID：时间戳 + 指纹前缀。

    注意：业务运行 run_id 由 Harness 运行器生成 UUID，不使用秒级时间戳
    充当唯一 ID；本函数仅用于压测批次（batch_id）与报告文件命名。
    """
    suffix = fingerprint[:8] if fingerprint else "nosig"
    return f"run_{time.strftime('%Y%m%d_%H%M%S')}_{suffix}"
