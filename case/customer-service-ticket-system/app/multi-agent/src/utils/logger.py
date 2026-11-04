"""全局日志工具：统一格式，避免直接 print。"""
from __future__ import annotations

import logging
import sys

_FMT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FMT))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger