"""版本指纹工具（Workflow+RAG v2）。

运行指纹固定被测系统、模型、Prompt、数据、依赖与有效配置（含 RAG 溯源参数），
保证性能基线可复现、可回溯；共享数据使用相对于案例根目录的稳定路径标识，
不写入机器路径和凭据（共享 Harness 设计说明 第 7.3、8.2 节）。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

APP_VERSION = "3.1.0"
APP_NAME = "customer-service-workflow-rag"

# 进入指纹的代码与依赖文件（相对阶段三根目录；共享 harness 以案例根包为准）
CODE_FILES = [
    "requirements.txt",
    "src/config/workflow_config.yaml",
    "src/main.py",
    "src/workflow/engine.py",
    "src/workflow/graph.py",
    "src/workflow/nodes.py",
    "src/workflow/guardrail.py",
    "src/rag/engine.py",
    "src/rag/index.py",
    "src/gates/executor.py",
    "src/tools/base.py",
    "src/tools/data_tools.py",
    "src/services/workflow_service.py",
    "src/api/routes.py",
    "src/models/schemas.py",
    "src/models/store.py",
    "src/config/settings.py",
    "src/utils/version.py",
    # 共享包（相对阶段三根目录的稳定路径标识）
    "../harness/__init__.py",
    "../harness/contracts.py",
    "../harness/budget.py",
    "../harness/params.py",
    "../harness/registry.py",
    "../harness/tool_executor.py",
    "../harness/observation.py",
    "../harness/policy.py",
    "../harness/providers.py",
    "../harness/mock_provider.py",
    "../harness/openai_provider.py",
    "../harness/langfuse_exporter.py",
    "../harness/logging_utils.py",
]

# 共享数据文件 -> 指纹中的稳定路径标签（相对案例根目录）
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
    """生成版本指纹（v1 兼容入口）。"""
    files = [project_root / "src/config/workflow_config.yaml"]
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


def build_run_fingerprint(project_root: Path, settings) -> dict:
    """生成 v2 运行指纹：代码/共享包/数据哈希 + 有效配置字段。"""
    hashes: dict[str, str] = {}
    for rel in CODE_FILES:
        f = (project_root / rel).resolve()
        if f.exists():
            label = rel if not rel.startswith("../") else f"harness/{Path(rel).name}"
            hashes[label] = file_sha256(f)
    data_dir = settings.test_data_dir
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

    业务运行 run_id 由 Harness 运行器生成 UUID；本函数仅用于批次与报告命名。
    """
    suffix = fingerprint[:8] if fingerprint else "nosig"
    return f"run_{time.strftime('%Y%m%d_%H%M%S')}_{suffix}"
