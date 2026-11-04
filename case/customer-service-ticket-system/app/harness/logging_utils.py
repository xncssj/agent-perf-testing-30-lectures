"""共享包日志工具：与各阶段 src/utils/logger 保持一致的格式。"""
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
