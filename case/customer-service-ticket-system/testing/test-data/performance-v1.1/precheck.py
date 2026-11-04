"""performance-v1.1 黄金用例静态可达性预检（生成前门禁）。

对每条适用黄金用例，用三阶段真实的 v1.1 Mock 决策代码（意图识别、工具规划、
答复模板）在进程内做静态推演，只识别"已知不可能路径"：
- 金额/复合/订单类用例：核对预期工具与事实（total_amount、物流、状态）能否
  出现在 v1.1 答复模板中；
- 知识检索类用例：工具路径静态可核，答复文本依赖运行时检索，单独归类；
- 澄清/投诉类用例：仅核对工具规划，文本在回放中判定；
- 故障窗口专用预期（stage2 专项注入用例）：不计入正常池可达性。
预检不代替服务逐笔回放；实际质量以服务回放与既有业务判定为准。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import derive

HERE = Path(__file__).resolve().parent
CASE_ROOT = HERE.parents[2]
APP_ROOT = CASE_ROOT / "app"
V1_DIR = HERE.parent / "performance-v1"

STAGE1_GOLDEN_COUNT = 48
STAGE_SPECIAL_GOLDEN_COUNT = 56

# sys.path 说明：app/ 提供 harness 包；multi-agent/src 提供 mas 包；
# workflow-flow-rag 目录本身提供 src 包（src.llm / src.workflow）。
for _path in (str(APP_ROOT), str(APP_ROOT / "multi-agent" / "src"),
              str(APP_ROOT / "workflow-flow-rag")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _base_maps():
    """与 v1 生成器同源的共享铺底数据（黄金用例引用原始 ID）。"""
    base = V1_DIR.parent
    orders = {row["order_id"]: row for row in _load(base / "orders.json")}
    logistics_rows = _load(base / "logistics.json")
    logs_by_order = {row["order_id"]: row for row in logistics_rows}
    logs_by_tracking = {row["tracking_no"]: row for row in logistics_rows}
    docs = {}
    for name in ("faq", "policies", "products"):
        for doc in _load(base / "knowledge-base" / f"{name}.json"):
            docs[f"{doc['category']}:{doc['title']}"] = doc
    return orders, logs_by_order, logs_by_tracking, docs


def _case_observations(case: dict, orders, logs_by_order, logs_by_tracking, docs):
    """按用例引用从铺底数据构造三阶段共用的工具观察载荷。"""
    order_id = case.get("order_id") or ""
    order = orders.get(order_id)
    tracking = (case.get("reference") or {}).get("tracking_no")
    log = logs_by_order.get(order_id) or (
        logs_by_tracking.get(tracking) if tracking else None)
    hits = [{"title": docs[doc_id].get("title", ""),
             "content": docs[doc_id]["content"]}
            for doc_id in (case.get("reference") or {}).get("knowledge_doc_ids", [])
            if doc_id in docs]
    return {
        "order": {"found": bool(order), "order": order or {}},
        "logistics": {"found": bool(log), "logistics": log or {}},
        "knowledge": {"found": bool(hits), "hits": hits},
    }


def _fact_and_keyword_gaps(case: dict, answer: str) -> tuple[list[str], list[str]]:
    facts = [str(value) for value in (case.get("expected_facts") or {}).values()
             if value is not None and str(value) != ""]
    keywords = [k for k in (case.get("expected_answer_keywords") or []) if k]
    fact_gaps = [v for v in facts if v not in answer]
    keyword_gaps = [k for k in keywords if k not in answer]
    return fact_gaps, keyword_gaps


def _stage1_check(case: dict, maps) -> dict:
    from harness.mock_provider import (  # noqa: E402
        ORDER_ID_RE, TRACKING_NO_RE, _render_answer, IntentDetector, MockProvider,
    )
    from types import SimpleNamespace
    detector = IntentDetector()
    provider = MockProvider(SimpleNamespace(
        model_name="mock", mock_seed=42, mock_plan_latency_ms=1,
        mock_plan_jitter_ms=1, mock_token_latency_ms=1,
        mock_rule_version="performance-v1.1"))
    query = case["user_query"]
    intent = provider._detect(query)
    plan = provider._plan_for(query, intent)
    expected_tools = list(case.get("expected_tools") or [])
    obs = _case_observations(case, *maps)
    order = obs["order"]["order"] if obs["order"]["found"] else {}
    logistics = obs["logistics"]["logistics"] if obs["logistics"]["found"] else {}
    hits = obs["knowledge"]["hits"] if obs["knowledge"]["found"] else []
    # v1.1 静态推演与服务答复同模板：知识命中带标题（战役 A7 修复口径）
    answer = _render_answer(order, logistics, hits, amount=True, doc_title=True)
    result = {"intent": intent, "plan_tools": plan, "expected_tools": expected_tools}
    tool_gaps = [t for t in expected_tools if t not in plan]
    # 与冻结规划一致的无号剪枝：期望工具因缺单号被剪属预期形态，不算缺口
    has_order = bool(ORDER_ID_RE.search(query))
    has_tracking = bool(TRACKING_NO_RE.search(query))
    if intent in ("logistics_query", "delivery_time") and not (has_order or has_tracking):
        tool_gaps = [t for t in tool_gaps if t != "query_logistics"]
    if intent in ("logistics_query", "delivery_time") and has_tracking and not has_order:
        tool_gaps = [t for t in tool_gaps if t != "query_order"]
    result["tool_gaps"] = tool_gaps
    result["fact_gaps"], result["keyword_gaps"] = _fact_and_keyword_gaps(case, answer)
    return result


def _stage2_check(case: dict, maps) -> dict:
    from mas.mock_provider import (  # noqa: E402
        AGENT_TOOL, _render_answer, fallback_plan, IntentDetector,
    )
    query = case["user_query"]
    intent = IntentDetector().detect(query)
    agents = fallback_plan(query, intent)
    tools = [AGENT_TOOL[a] for a in agents if AGENT_TOOL.get(a)]
    expected_tools = list(case.get("expected_tools") or [])
    # 幽灵期望口径与 tests/performance/common.py 一致：
    # expected_agents 中对应工具不在 expected_tools 的条目不参与路径核对
    for expected in (case.get("expected_agents") or []):
        tool = AGENT_TOOL.get(expected)
        if tool and tool not in expected_tools and tool in tools:
            pass  # 幽灵期望出现不阻断
    obs = _case_observations(case, *maps)
    order = obs["order"]["order"] if obs["order"]["found"] else {}
    logistics = obs["logistics"]["logistics"] if obs["logistics"]["found"] else {}
    hits = obs["knowledge"]["hits"] if obs["knowledge"]["found"] else []
    answer = _render_answer(order, logistics, hits, {}, amount=True, doc_title=True)
    result = {"intent": intent, "plan_agents": agents, "plan_tools": tools,
              "expected_tools": expected_tools,
              "tool_gaps": [t for t in expected_tools if t not in tools]}
    result["fact_gaps"], result["keyword_gaps"] = _fact_and_keyword_gaps(case, answer)
    return result


def _stage3_check(case: dict, maps) -> dict:
    from types import SimpleNamespace
    from src.llm.mock_llm import IntentDetector, MockLlm  # noqa: E402
    from src.workflow.nodes import INTENT_TOOLS  # noqa: E402
    query = case["user_query"]
    intent = IntentDetector().detect(query)
    tool_names = list(INTENT_TOOLS.get(intent, ["query_order", "query_logistics"]))
    obs = _case_observations(case, *maps)
    order = obs["order"]["order"] if obs["order"]["found"] else {}
    logistics = obs["logistics"]["logistics"] if obs["logistics"]["found"] else {}
    hits = obs["knowledge"]["hits"] if obs["knowledge"]["found"] else []
    llm = MockLlm(SimpleNamespace(
        model_name="mock", mock_seed=42,
        intent_latency_ms=1, intent_jitter_ms=1,
        generation_latency_ms=1, generation_jitter_ms=1,
        clarification_latency_ms=1, clarification_jitter_ms=1,
        token_latency_ms=1, mock_rule_version="performance-v1.1"))
    answer = llm.generate_answer(query, {
        "order": obs["order"], "logistics": obs["logistics"],
    }, hits).content
    expected_tools = list(case.get("expected_tools") or [])
    # 阶段三判定口径（tests/performance/common.py）：expected_tools 的
    # search_knowledge 由 knowledge_retrieval 节点等价承载；
    # 知识类意图在图中必经检索节点，视为满足
    knowledge_intents = {"return_policy", "exchange_policy", "contact_info"}
    result = {"intent": intent, "plan_tools": tool_names,
              "expected_tools": expected_tools,
              "tool_gaps": [t for t in expected_tools
                            if t not in tool_names
                            and not (t == "search_knowledge"
                                     and intent in knowledge_intents)]}
    result["fact_gaps"], result["keyword_gaps"] = _fact_and_keyword_gaps(case, answer)
    # 阶段三路径与分支由运行时图决定，预检不静态判定，交由服务逐笔回放
    return result


_STAGE_CHECKERS = {1: _stage1_check, 2: _stage2_check, 3: _stage3_check}


def classify(case: dict, stage: int, check: dict) -> str:
    """把静态推演结果归类：只把"已知不可能"判为 unreachable。"""
    fault_profile = (case.get("perf_tags") or {}).get("fault_profile")
    if fault_profile and fault_profile != "none":
        return "fault_window"
    if check["tool_gaps"]:
        return "unreachable"
    if case.get("expected_facts") and check.get("fact_gaps"):
        return "unreachable"
    family = case.get("family", "")
    if family in ("policy", "contact") or check.get("intent") in (
            "return_policy", "exchange_policy", "contact_info"):
        return "retrieval_dependent"
    if family in ("clarification",) or check.get("intent") in ("clarification", "complaint"):
        return "behavior_only"
    if check.get("keyword_gaps"):
        # 静态模板＋铺底数据已确定缺少预期关键词（战役 A7 形态）：答复文本
        # 由确定性模板与固定种子数据决定，运行时检索不参与，判为不可达
        return "unreachable"
    return "static_pass"


def precheck_reachability(cases: list[dict] | None = None) -> dict:
    """对三阶段全部适用黄金用例做静态可达性预检，返回报告结构。"""
    maps = _base_maps()
    if cases is None:
        # 独立入口默认按 v1.1 派生规则取用例，与 generate/validate 保持同一口径
        cases = derive.derive_cases(_load(V1_DIR / "cases.json"))
    report = {"stages": {}, "unreachable_total": 0}
    for stage in (1, 2, 3):
        items = []
        counts: dict[str, int] = {}
        for case in cases:
            if stage not in (case.get("applicable_stages") or []):
                continue
            check = _STAGE_CHECKERS[stage](case, maps)
            category = classify(case, stage, check)
            counts[category] = counts.get(category, 0) + 1
            entry = {"case_id": case["case_id"], "family": case.get("family", ""),
                     "category": category, **check}
            if category != "static_pass":
                # 静态通过之外的条目保留缺口明细，供人工复核与回放对照
                entry["gaps"] = {"fact_gaps": check.get("fact_gaps", []),
                                 "keyword_gaps": check.get("keyword_gaps", []),
                                 "tool_gaps": check.get("tool_gaps", [])}
            items.append(entry)
        unreachable = counts.get("unreachable", 0)
        report["unreachable_total"] += unreachable
        report["stages"][str(stage)] = {
            "golden_count": len(items),
            "category_counts": dict(sorted(counts.items())),
            "unreachable": unreachable,
            "cases": items,
        }
    return report


if __name__ == "__main__":
    result = precheck_reachability()
    print(json.dumps({stage: {"golden_count": body["golden_count"],
                              "category_counts": body["category_counts"]}
                      for stage, body in result["stages"].items()},
                     ensure_ascii=False, indent=2))
    if result["unreachable_total"]:
        raise SystemExit(f"存在 {result['unreachable_total']} 条不可达黄金用例")
