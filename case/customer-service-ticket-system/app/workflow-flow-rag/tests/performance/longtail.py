"""低频长尾分支：合成场景 + 定向数据增强（课程第 23 讲最后一块）。

数据契约里缺少的 human_transfer 分支以及低频意图，通过合成场景补齐，
验证：分支可达、意图可达、答复语义合理；并给出长尾缺口结论。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.services.workflow_service import WorkflowService  # noqa: E402
from tests.performance.common import stamp_and_write  # noqa: E402


@dataclass
class Case:
    name: str
    query: str
    expect_intent: str
    expect_branch: str
    check: str  # 判定子串


CASES = [
    # H1 判据改用语义标记「联系人工客服」：v2 重构已把人工处理文案从
    # 「已为您转接人工客服」改为只表述建议行为（系统并不能真的转接，
    # 声称已转交属虚假陈述），断言同步为语义判据，避免随文案再次漂移。
    Case("H1-投诉转人工", "我的订单 2024010100150 金额不对，我要投诉！",
         "complaint", "human_transfer", "联系人工客服"),
    Case("H2-换货政策", "订单 2024010100002 我想换货怎么办？",
         "exchange_policy", "auto_reply", None),
    Case("H3-客服电话", "你们的客服电话是多少？", "contact_info",
         "auto_reply", "400-800-8000"),
    Case("H4-模糊表述澄清", "那个东西到哪了", "clarification",
         "clarification", "订单号或物流单号"),
    Case("H5-退款进度（有单号）", "退款 2024010100003 到哪一步了？",
         "refund_status", "auto_reply", None),
]


def main() -> int:
    settings = Settings()
    service = WorkflowService(settings)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat(timespec="seconds")

    rows = []
    for c in CASES:
        r = service.process(c.query)
        ok_path = r["status"] == "completed"
        ok_branch = r["branch"] == c.expect_branch
        ok_intent = r["intent"] == c.expect_intent
        ok_text = True if c.check is None else (c.check in r["response"])
        passed = ok_path and ok_branch and ok_intent and ok_text
        rows.append({
            "name": c.name, "branch": r["branch"], "intent": r["intent"],
            "result": "通过" if passed else "失败",
            "detail": {"ok_path": ok_path, "ok_branch": ok_branch,
                       "ok_intent": ok_intent, "ok_text": ok_text},
            "response": r["response"][:80],
        })
        print(f"[{c.name}] branch={r['branch']} intent={r['intent']} "
              f"{'通过' if passed else '失败'}", flush=True)

    passed = sum(1 for x in rows if x["result"] == "通过")
    conclusion = "长尾分支均可达，human_transfer 通过合成场景补齐" if passed == len(CASES) else "存在长尾缺口"
    gap_conclusion = (
        "数据契约无 human_transfer 工单与投诉样例，属合成长尾；"
        "建议沉淀为正式回归集并纳入联合门禁（见 09-gate-testing 附录）"
    )
    payload = {
        "test_type": "longtail",
        "fingerprint": settings.to_fingerprint_fields(),
        "scenarios": rows,
        "passed": passed,
        "total": len(CASES),
        "conclusion": conclusion,
        "gap_conclusion": gap_conclusion,
    }
    out = stamp_and_write(
        settings, "longtail", payload,
        started_at=started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
        data_path=settings.data_dir / "tickets.json",
    )
    print(f"低频长尾通过 {passed}/{len(CASES)}，结论：{conclusion}")
    print(f"结果已写入：{out}")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())