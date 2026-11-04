"""RAG 检索质量测试：精确命中（top1）、精度@3、耗时、重复性。

题集分两部分（课程 RAG 质量题集）：
- QUERIES：整改前既有 10 题，期望保持不变，用于验证无退化；
- CONTROL_QUERIES：政策/流程/商品对照题，验证窄范围政策意图重排。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.models.store import DataStore  # noqa: E402
from src.rag.engine import RagEngine  # noqa: E402
from src.utils.metrics import describe  # noqa: E402
from tests.performance.common import stamp_and_write  # noqa: E402

QUERIES = [
    ("如何申请退款？", "faq:如何申请退货"),
    ("退款多久能到账？", "faq:退款到账时间"),
    ("官方客服电话是多少？", "faq:客服联系电话"),
    ("怎么申请换货？", "faq:如何申请换货"),
    ("发票怎么开？", "faq:发票开具说明"),
    ("偏远地区能配送吗？", "faq:物流配送范围"),
    ("退货退款政策", "policies:售后服务政策"),
    ("iPhone 退换说明", "products:iPhone 15 Pro 退换说明"),
    ("商品有质量问题，我要退货", "policies:售后服务政策"),
    ("怎么联系人工客服", "faq:客服联系电话"),
]

# V08 对照题（期望 top1）；用于验证政策意图、申请流程与指定商品三类边界
CONTROL_QUERIES = [
    ("七天无理由退货有什么规定", "policies:售后服务政策"),
    ("退货具体怎么申请", "faq:如何申请退货"),
    ("iPhone 15 Pro 退换政策", "products:iPhone 15 Pro 退换说明"),
    ("AirPods 保修政策", "products:AirPods Pro 2 保修政策"),
]

# 干扰题（B9）：语义相近、易误配到相邻文档；期望 top1=expect 且 must_not 不得出现在 top3
DISTRACTOR_QUERIES = [
    ("退款和退货有什么区别", "faq:如何申请退货", "faq:退款到账时间"),
    ("退货后退款多久到账", "faq:退款到账时间", "faq:如何申请退货"),
    ("iPhone 15 Pro 和 AirPods 保修一样吗", "products:iPhone 15 Pro 退换说明", "products:AirPods Pro 2 保修政策"),
    ("人工客服下班了怎么办", "faq:客服联系电话", "faq:物流配送范围"),
]


def main() -> int:
    settings = Settings()
    started_at = datetime.now().isoformat(timespec="seconds")
    store = DataStore(settings.data_dir)
    rag = RagEngine(store, top_k=settings.top_k)
    rag.warm()

    latencies = []
    samples = []
    precision = {"top1": 0, "at3": 0}
    repeat_top1 = True
    corpus_docs = 0

    for query, expect in QUERIES:
        first = rag.search(query, top_k=3)
        hits1 = first["hits"]
        latencies.append(first["latency_ms"])
        corpus_docs = first["corpus_docs"]
        top1 = hits1[0]["doc_id"] if hits1 else None
        ids3 = [h["doc_id"] for h in hits1]
        if top1 == expect:
            precision["top1"] += 1
        if expect in ids3:
            precision["at3"] += 1
        # 重复性：同一查询连续两次检索 top1 必须一致（函数确定性）
        again = rag.search(query, top_k=3)["hits"]
        if (again[0]["doc_id"] if again else None) != top1:
            repeat_top1 = False
        samples.append({"query": query[:24], "expect": expect, "top1": top1,
                        "top3": ids3})

    control_samples = []
    control_passed = 0
    for query, expect in CONTROL_QUERIES:
        hits1 = rag.search(query, top_k=3)["hits"]
        top1 = hits1[0]["doc_id"] if hits1 else None
        ok = top1 == expect
        control_passed += 1 if ok else 0
        control_samples.append({
            "query": query, "expect": expect, "top1": top1, "passed": ok,
            "top3": [h["doc_id"] for h in hits1],
        })

    distractor_samples = []
    distractor_top1_hits = 0
    distractor_forbidden_hits = 0
    for query, expect, must_not in DISTRACTOR_QUERIES:
        hits1 = rag.search(query, top_k=3)["hits"]
        top1 = hits1[0]["doc_id"] if hits1 else None
        ids3 = [h["doc_id"] for h in hits1]
        top1_ok = top1 == expect
        forbidden = must_not in ids3
        distractor_top1_hits += 1 if top1_ok else 0
        distractor_forbidden_hits += 1 if forbidden else 0
        distractor_samples.append({
            "query": query, "expect": expect, "must_not": must_not,
            "top1": top1, "top1_ok": top1_ok, "forbidden_hit": forbidden,
            "top3": ids3,
        })

    total = len(QUERIES)
    payload = {
        "test_type": "rag_quality",
        "fingerprint": settings.to_fingerprint_fields(),
        "quality": {
            "search_mode": settings.search_mode,
            "top_k": settings.top_k,
            "docs_count": corpus_docs,
            "queries": total,
            "precision_top1": round(precision["top1"] / total, 4),
            "precision_at_3": round(precision["at3"] / total, 4),
            "p90_latency_ms": describe(latencies).get("p90"),
            "repeatable": repeat_top1,
            "samples": samples,
        },
        # 对照题只验证 V08 标注边界，不推断真实用户问题的总体准确率
        "control_cases": {
            "queries": len(CONTROL_QUERIES),
            "passed": control_passed,
            "all_passed": control_passed == len(CONTROL_QUERIES),
            "samples": control_samples,
        },
        # 干扰题：验证不会把相近问题误配到相邻文档（B9）
        "distractor_cases": {
            "queries": len(DISTRACTOR_QUERIES),
            "top1_hits": distractor_top1_hits,
            "forbidden_hits": distractor_forbidden_hits,
            "passed": (distractor_top1_hits == len(DISTRACTOR_QUERIES)
                       and distractor_forbidden_hits == 0),
            "samples": distractor_samples,
        },
    }

    out = stamp_and_write(
        settings, "rag_quality", payload,
        started_at=started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
        data_path=settings.data_dir / "tickets.json",
    )
    print(f"精确命中(top1)={payload['quality']['precision_top1']} "
          f"精度@3={payload['quality']['precision_at_3']} 重复性={repeat_top1}")
    print(f"对照题={control_passed}/{len(CONTROL_QUERIES)} "
          f"全部通过={payload['control_cases']['all_passed']}")
    print(f"干扰题={distractor_top1_hits}/{len(DISTRACTOR_QUERIES)} "
          f"禁中命中={distractor_forbidden_hits}")
    print(f"结果已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())