"""Multi-Agent Harness 层（v2）：超管编排、Worker 与模型接入。"""
from __future__ import annotations

import sys
from pathlib import Path

_CASE_ROOT = Path(__file__).resolve().parents[4]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))