"""压测判定逻辑自测：无 pytest 依赖，纯 assert，可复跑。

验证内容（对应提审阻断项 C07 的修复）：
1. 业务判定为 AND 语义：全部关键词 + 全部事实命中才合格（“中转站。”不再误判合格）
2. 模糊工单回退 expected_behavior 判定
3. Goodput = 合格业务数 / 实测时长（业务/秒，非百分比）
4. 成功业务成本以合格业务为分母
5. 请求层失败业务的四级判定全 False

运行：python -m tests.performance.selfcheck
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.performance.common import (  # noqa: E402
    SHARED_DATA_DIR,
    Ticket,
    aggregate,
    agent_path_ok,
    classify,
    describe_degraded,
    failed_business,
    load_tickets,
)


def main() -> int:
    tickets = load_tickets(SHARED_DATA_DIR / "tickets.json")
    assert len(tickets) == 245, f"工单数应为 245，实际 {len(tickets)}"

    # 取一条标准工单（含关键词与事实）
    t1 = next(t for t in tickets if t.ticket_id == "T00001")
    assert t1.expected_answer_keywords, "T00001 应有关键词"
    assert t1.expected_facts, "T00001 应有事实"
    assert "order_agent" in t1.expected_agents, "T00001 应包含 order_agent"
    assert "validation_agent" in t1.expected_agents, "T00001 应包含 validation_agent"

    # 用例 1：仅命中一个关键词（审查报告复现场景：“中转站。”）→ 不合格
    r = classify(t1, "中转站。", {"e2e_ms": 100.0}, slo_ms=15000.0)
    assert r["levels"]["result_ok"] is False, "单关键词命中不得判定结果合格"
    assert r["levels"]["qualified"] is False

    # 用例 2：全部关键词 + 全部事实 → 合格
    full_answer = (
        "您的订单 {order_id} 当前状态为{status}，物流信息：{current_location}，"
        "预计 {estimated_delivery} 送达。".format(**{
            k: v for k, v in t1.expected_facts.items()
        })
    )
    full_answer += "".join(t1.expected_answer_keywords)
    r = classify(t1, full_answer, {"e2e_ms": 100.0}, slo_ms=15000.0)
    assert r["levels"]["result_ok"] is True, f"完整答复应合格：{full_answer}"
    assert r["levels"]["qualified"] is True

    # 用例 3：缺一个事实（去掉当前位置）→ 不合格
    partial = full_answer.replace(t1.expected_facts.get("current_location", ""), "")
    r = classify(t1, partial, {"e2e_ms": 100.0}, slo_ms=15000.0)
    assert r["levels"]["result_ok"] is False, "缺少事实不得判定结果合格"

    # 用例 4：SLO 超时 → slo_ok=False、qualified=False
    r = classify(t1, full_answer, {"e2e_ms": 99999.0}, slo_ms=15000.0)
    assert r["levels"]["slo_ok"] is False and r["levels"]["qualified"] is False

    # 用例 5：模糊工单（无关键词/事实）按 expected_behavior 判定
    m1 = next(t for t in tickets if t.ticket_id == "M001")
    assert not m1.expected_answer_keywords and not m1.expected_facts
    r = classify(m1, "请问您方便提供订单号或物流单号吗？", {"e2e_ms": 100.0}, slo_ms=15000.0)
    assert r["levels"]["result_ok"] is True, "澄清答复含行为特征文本应合格"
    r = classify(m1, "抱歉，我暂时无法回答这个问题。", {"e2e_ms": 100.0}, slo_ms=15000.0)
    assert r["levels"]["result_ok"] is False, "致歉模板不得判定模糊工单合格"

    # 用例 6：Goodput = 合格业务数 / 实测时长（业务/秒）
    ok_ticket = Ticket(
        ticket_id="OK1", user_query="q", expected_answer_keywords=["快"],
        expected_facts={"order_id": "1"},
    )
    bad_ticket = Ticket(
        ticket_id="BAD1", user_query="q", expected_answer_keywords=["快"],
        expected_facts={"order_id": "1"},
    )
    businesses = [
        classify(ok_ticket, "快 订单 1", {"e2e_ms": 1.0, "total_tokens": 1000}, 15000.0),
        classify(bad_ticket, "不相关答复", {"e2e_ms": 1.0, "total_tokens": 1000}, 15000.0),
    ]
    agg = aggregate(businesses, duration_s=10.0, cost_per_1k_tokens=2.0)
    assert agg["业务Goodput(合格业务/秒)"] == 0.1, f"Goodput 应为 0.1（1 合格 / 10 秒），实际 {agg['业务Goodput(合格业务/秒)']}"
    # 用例 7：成功业务成本 = 总成本 / 合格业务数（合格分母，非请求数）
    cost = agg["成本指标"]["成功业务成本(元/合格业务)"]
    assert cost == 4.0, f"总成本 2000token/1000*2.0=4.0 元，1 笔合格 → 4.0，实际 {cost}"

    # 用例 8：请求层失败业务四级全 False
    fb = failed_business(ok_ticket, "connection refused")
    assert not any(fb["levels"].values()), "请求失败业务四级判定应全 False"

    # 用例 9：Multi-Agent 路径校验（实际编排 Agent 覆盖 expected_agents）
    ok_metrics = {
        "trace": {
            "plan_agents": ["order_agent", "logistics_agent"],
            "validation": {"status": "complete"},
        }
    }
    assert agent_path_ok(t1, ok_metrics), "完整编排应通过路径校验"
    bad_metrics = {
        "trace": {
            "plan_agents": ["order_agent"],
            "validation": {"status": "complete"},
        }
    }
    assert not agent_path_ok(t1, bad_metrics), "缺少 logistics_agent 不应通过路径校验"
    no_val_metrics = {
        "trace": {"plan_agents": ["order_agent", "logistics_agent"], "validation": {}}
    }
    assert not agent_path_ok(t1, no_val_metrics), "缺校验环节不应通过路径校验"

    # 用例 10：降级统计口径
    degraded_business = {
        "metrics": {
            "trace": {
                "degraded": True,
                "agents": [{"name": "logistics_agent", "status": "skipped_breaker"}],
                "fanout": {"efficiency": 1.8},
            }
        }
    }
    normal_business = {
        "metrics": {
            "trace": {
                "degraded": False,
                "agents": [{"name": "order_agent", "status": "success"}],
                "fanout": {"efficiency": 1.9},
            }
        }
    }
    d = describe_degraded([degraded_business, normal_business])
    assert d["降级业务"] == 1, f"降级业务应为 1，实际 {d['降级业务']}"
    assert d["熔断跳过调用"] == 1, f"熔断跳过应为 1，实际 {d['熔断跳过调用']}"
    assert abs(d["平均并行效率"] - 1.85) < 1e-6, f"并行效率应 1.85，实际 {d['平均并行效率']}"

    # 用例 11（v2）：status!=completed 时流程完成必须为 False
    r = classify(
        t1, full_answer, {"e2e_ms": 100.0, "total_tokens": 500}, slo_ms=15000.0,
        status="budget_exhausted",
    )
    assert r["levels"]["flow_done"] is False, "v2 终态 budget_exhausted 不得判定流程完成"
    assert r["levels"]["qualified"] is False
    r = classify(
        t1, full_answer, {"e2e_ms": 100.0, "total_tokens": 500}, slo_ms=15000.0,
        status="completed",
    )
    assert r["levels"]["flow_done"] is True and r["levels"]["qualified"] is True, \
        "v2 终态 completed 且事实齐备应合格"

    # 用例 12（v2）：任一业务用量缺失 → 完整成本为空、覆盖率下降
    miss = list(businesses) + [{
        "ticket_id": "MISS1", "query": "q",
        "metrics": {"e2e_ms": 1.0, "total_tokens": None},
        "levels": {"request_ok": True, "flow_done": True, "business_done": True,
                   "result_ok": True, "slo_ok": True, "qualified": True},
    }]
    agg2 = aggregate(miss, duration_s=10.0, cost_per_1k_tokens=2.0)
    cost_block = agg2["成本指标"]
    assert cost_block["总成本(元)"] is None, "存在用量缺失时总成本必须为空"
    assert cost_block["成功业务成本(元/合格业务)"] is None, "存在用量缺失时成功业务成本必须为空"
    assert cost_block["用量缺失业务数"] == 1
    assert cost_block["用量覆盖率(%)"] == 66.67, \
        f"覆盖率应为 66.67%，实际 {cost_block['用量覆盖率(%)']}"

    print(
        "selfcheck 全部通过：判定 AND 语义 / 模糊回退 / Goodput 口径 / 成本分母 / "
        "失败判定 / 多Agent路径校验 / 降级统计 / v2 终态与用量覆盖"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
