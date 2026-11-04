"""主路径冒烟自检：本地闭环跑通后，联调前使用。

覆盖：意图匹配 / 路径=预期 / 分支=预期 / 工具覆盖 / 答复含要素 /
澄清分支 / 知识分支 / RAG 检索精确命中 / 护栏拦截 / token 非零。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.services.workflow_service import WorkflowService  # noqa: E402
from tests.performance.common import (  # noqa: E402
    MAIN_PATH,
    classify,
    load_tickets,
)


def main() -> int:
    settings = Settings()
    service = WorkflowService(settings)
    tickets = load_tickets(settings.data_dir / "tickets.json")

    ok = 0
    for t in tickets[:20] + tickets[-10:]:
        result = service.process(t.user_query)
        # v1 入口无首字实测，classify 自动跳过首字 SLO，仅校验 E2E
        _e2e_slo, _fa_slo = settings.active_slo()
        biz = classify(t, result, slo_ms=_e2e_slo, first_answer_slo_ms=_fa_slo)
        print(
            f"[{t.ticket_id}] intent={result.get('intent')} path={result.get('path')} "
            f"branch={result.get('branch')} qualified={biz['levels']['qualified']} "
            f"e2e={result.get('e2e_ms')}ms"
        )
        if biz["levels"]["qualified"]:
            ok += 1

    print(f"\n冒烟通过 {ok}/{30}")

    # RAG 精确命中自检
    probes = {
        "如何申请退款？": "faq:如何申请退货",
        "退款多久能到账？": "faq:退款到账时间",
        "官方客服电话是多少？": "faq:客服联系电话",
    }
    for q, expect in probes.items():
        hits = service.rag.search(q, top_k=3)["hits"]
        top1 = hits[0]["doc_id"] if hits else None
        print(f"RAG[{q[:14]}] top1={top1} expect={expect} {'OK' if top1 == expect else 'FAIL'}")

    # 护栏自检
    from src.workflow.guardrail import guardrail

    bad = guardrail.check("我的电话是 13812345678 {LLM_API_MODEL_ID}")
    print(f"护栏拦截敏感手机号: {bad}")
    return 0 if ok == 30 else 1


if __name__ == "__main__":
    sys.exit(main())