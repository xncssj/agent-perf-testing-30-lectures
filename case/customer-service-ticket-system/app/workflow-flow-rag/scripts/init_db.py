"""工作台初始化：加载共享数据集并预热 RAG 索引（自检）。

运行时无外部数据库依赖：订单/物流/知识库以进程内只读快照承载，
init 阶段完成数据完整性自检与 RAG 检索冒烟，输出环境指纹供报告对齐。

运行：python scripts/init_db.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.models.store import DataStore  # noqa: E402
from src.rag.engine import RagEngine  # noqa: E402


def main() -> None:
    settings = Settings()
    store = DataStore(settings.data_dir)

    manifest = settings.data_dir / "MANIFEST.md"
    rag = RagEngine(store, top_k=settings.top_k)
    rag.warm()

    smoke = rag.search("怎么申请退货退款", top_k=3)
    top = (smoke.get("hits") or [{}])[0]

    print(f"数据快照：订单={len(store._orders)} 物流={len(store._logistics_by_order)} "
          f"KB={len(store.knowledge_docs())} (from {settings.data_dir.name})")
    print(f"RAG 索引预热：docs={len(rag.index.docs)}，冒烟查询命中 top1={top.get('doc_id')} "
          f"(score={top.get('score')})，检索耗时={smoke.get('latency_ms')}ms")
    print(f"环境指纹：{json.dumps(settings.to_fingerprint_fields(), ensure_ascii=False)}")
    if not rag.index.docs:
        print("警告：知识库为空，RAG 门禁与知识分支无法生效", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)