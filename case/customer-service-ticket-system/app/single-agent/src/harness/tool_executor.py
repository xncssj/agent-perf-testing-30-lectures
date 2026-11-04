"""薄适配 shim：实现已抽取到案例根共享包 harness（单一事实源）。

仅做转发，保持既有 `src.*` 导入路径可用；共享包见 ../../../app/harness/。
"""
from __future__ import annotations

import sys
from pathlib import Path

_CASE_ROOT = Path(__file__).resolve().parents[3]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.tool_executor import (  # noqa: E402,F401
    BACKOFF_BASE_S,
    BACKOFF_CAP_S,
    RunRecorderProtocol,
    ToolExecutor,
)
