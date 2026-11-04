"""RAG 检索整改基线捕获（课程 RAG 整改基线）。

在整改前代码上记录：运行指纹（代码+语料+配置哈希）、10 题逐题检索输出
与失败明细，落盘到 baselines/ 目录；不覆盖 reports/ 下任何历史报告。

注意：本脚本结果反映**捕获时刻**的代码行为。基线文件一经生成即为历史证据，
整改后重跑脚本得到的是整改后行为，不得回写基线文件。

运行（阶段三应用目录）：
    .venv/bin/python scripts/capture_rag_baseline.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.models.store import DataStore  # noqa: E402
from src.rag.engine import RagEngine  # noqa: E402
from src.utils.version import build_run_fingerprint  # noqa: E402

# 与 rag_quality.py 同一题集（整改前 10 题），期望 doc_id 保持原样
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


def git_rev() -> dict:
    """记录捕获时刻的 Git 提交与工作区状态（应用目录范围）。"""
    case_root = PROJECT_ROOT.parent.parent
    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=case_root,
        capture_output=True, text=True,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--",
         str(PROJECT_ROOT.relative_to(case_root))],
        cwd=case_root, capture_output=True, text=True,
    )
    return {
        "git_head": rev.stdout.strip() if rev.returncode == 0 else None,
        "app_dir_dirty": bool(dirty.stdout.strip()),
    }


def main() -> int:
    settings = Settings()
    store = DataStore(settings.data_dir)
    rag = RagEngine(store, top_k=settings.top_k)
    rag.warm()

    samples = []
    failures = []
    for query, expect in QUERIES:
        result = rag.search(query, top_k=3)
        top1 = result["hits"][0]["doc_id"] if result["hits"] else None
        ids3 = [h["doc_id"] for h in result["hits"]]
        hit_top1 = top1 == expect
        hit_at3 = expect in ids3
        samples.append({
            "query": query,
            "expect": expect,
            "top1": top1,
            "top3": ids3,
            "hit_top1": hit_top1,
            "hit_at3": hit_at3,
            "index_version": result["index_version"],
        })
        if not hit_top1 or not hit_at3:
            failures.append({
                "query": query,
                "expect": expect,
                "actual_top1": top1,
                "expected_in_top3": hit_at3,
                "symptom": ("top1 错位" if not hit_top1 else "期望文档未进 top3"),
            })

    payload = {
        "test_type": "rag_remediation_baseline",
        "spec": "public-case-kit",
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git": git_rev(),
        "fingerprint": build_run_fingerprint(PROJECT_ROOT, settings),
        "corpus": {
            "docs_count": len(store.knowledge_docs()),
            "initial_doc_ids": [
                {"id": d.get("id"), "doc_id": DataStore.doc_id(d)}
                for d in store.knowledge_docs()
            ],
            "has_numeric_ids": all(
                isinstance(d.get("id"), int) for d in store.knowledge_docs()
            ),
        },
        "quality_10q": {
            "queries": len(QUERIES),
            "hit_top1": sum(1 for s in samples if s["hit_top1"]),
            "hit_at3": sum(1 for s in samples if s["hit_at3"]),
            "index_version": rag.index_version,
            "samples": samples,
        },
        "failures": failures,
    }

    out_dir = PROJECT_ROOT / "baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"rag-baseline-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"基线已落盘：{out.name}")
    print(f"git_head={payload['git']['git_head']} "
          f"app_dir_dirty={payload['git']['app_dir_dirty']} "
          f"fingerprint={payload['fingerprint']['fingerprint']}")
    print(f"10题 top1 命中={payload['quality_10q']['hit_top1']}/10 "
          f"top3 命中={payload['quality_10q']['hit_at3']}/10 "
          f"失败={len(failures)}")
    for f in failures:
        print(f"  失败：{f['query']} 期望 {f['expect']} 实际 top1={f['actual_top1']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
