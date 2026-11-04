"""数据库初始化：从共享基准数据集（../test-data）导入订单、物流与知识库快照。

运行：python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.models.database import Database, load_seeds  # noqa: E402


def main() -> None:
    settings = Settings()
    orders, logistics, knowledge = load_seeds(settings.data_dir)
    db = Database(settings.db_path)
    db.seed_loads(orders, logistics, knowledge)
    print(
        f"数据库初始化完成：{settings.db_path} "
        f"orders={len(orders)} logistics={len(logistics)} knowledge={len(knowledge)}"
    )


if __name__ == "__main__":
    main()
    sys.exit(0)