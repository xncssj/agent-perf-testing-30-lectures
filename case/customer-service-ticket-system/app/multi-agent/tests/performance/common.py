"""压测公共模块（Multi-Agent）：工单加载、业务判定、聚合与编排覆盖。

口径对齐课程：
- 四级业务判定（请求成功 / 流程完成 / 业务完成 / 结果合格）
- 结果合格 = 全部业务要点词命中 AND 全部结构化事实出现在答复中
  （v2 数据契约，见 ../../../../testing/test-data/MANIFEST.md；v1 为任一关键词命中，已废弃）
- 业务 Goodput = 合格业务数 / 实测时长（单位：业务/秒，非百分比）
- 成功业务成本 = 总 token 成本 / 合格业务数（以合格业务为分母，课程第 04 讲口径）
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.metrics import business_levels, describe  # noqa: E402

# 三阶段共享基准数据集（单一事实源，见 MANIFEST.md）
SHARED_DATA_DIR = PROJECT_ROOT.parent.parent / "testing" / "test-data"

# token 单价（元/千 token）：mock 假设值，仅用于成本口径演示，不代表真实定价
DEFAULT_COST_PER_1K_TOKENS = 2.0


@dataclass
class Ticket:
    ticket_id: str
    user_query: str
    expected_intent: str = ""
    expected_tools: list[str] = field(default_factory=list)
    expected_answer_keywords: list[str] = field(default_factory=list)
    expected_behavior: str = ""
    difficulty: str = "easy"
    order_id: str = ""
    expected_facts: dict = field(default_factory=dict)
    expected_agents: list[str] = field(default_factory=list)


def load_tickets(path: Path | str) -> list[Ticket]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    tickets = []
    for item in data:
        tickets.append(
            Ticket(
                ticket_id=item.get("ticket_id", ""),
                user_query=item.get("user_query", ""),
                expected_intent=item.get("expected_intent", ""),
                expected_tools=list(item.get("expected_tools") or []),
                expected_answer_keywords=list(
                    item.get("expected_answer_keywords")
                    or item.get("expected_answer_contains")
                    or []
                ),
                expected_behavior=item.get("expected_behavior", ""),
                difficulty=item.get("difficulty", "easy"),
                order_id=item.get("order_id", ""),
                expected_facts=dict(item.get("expected_facts") or {}),
                expected_agents=list(item.get("expected_agents") or []),
            )
        )
    return tickets


def classify(
    ticket: Ticket,
    response: str,
    metrics: dict,
    slo_ms: float,
    request_ok: bool = True,
    status: str | None = None,
) -> dict:
    """对单笔业务做四级判定，返回 levels 与原始结果字典。

    v2 结果必须消费服务端终态：status 传入时 flow_done 要求 status=completed，
    运行完成不再直接等于流程完成（共享 Harness 设计说明 第 7.2 节）；
    status 缺省（v1 历史口径）时按答复文本推断，保持旧报告可复算。

    结果合格标准（AND 语义，缺一即不合格）：
    1. 全部 expected_answer_keywords 出现在答复中；
    2. 全部 expected_facts 取值出现在答复中（订单状态、当前位置、预计送达等）；
    3. expected_behavior（行为特征文本）出现在答复中；
    4. 关键词与事实均空的模糊工单回退为“答复非空且非致歉模板”。
    """
    if status is not None:
        flow_done = status == "completed"
    else:
        flow_done = bool(response)
    business_done = flow_done and not response.startswith("抱歉，我暂时无法回答")
    keywords = [k for k in ticket.expected_answer_keywords if k]
    facts = {
        str(k): str(v)
        for k, v in (ticket.expected_facts or {}).items()
        if v is not None and str(v) != ""
    }
    if keywords or facts:
        result_ok = all(k in response for k in keywords) and all(
            v in response for v in facts.values()
        )
    else:
        result_ok = business_done
    if ticket.expected_behavior:
        result_ok = result_ok and ticket.expected_behavior in response

    # 评测回填：逐笔写出要素命中/缺失明细，使质量维度可聚合，而不是只留
    # 一个布尔结论（2026-09-12 待办 2b）
    hit_keywords = [k for k in keywords if k in response]
    missed_keywords = [k for k in keywords if k not in response]
    hit_facts = {k: v for k, v in facts.items() if v in response}
    missed_facts = {k: v for k, v in facts.items() if v not in response}
    quality = {
        "keywords_expected": len(keywords),
        "keywords_hit": len(hit_keywords),
        "keywords_missed": missed_keywords,
        "facts_expected": len(facts),
        "facts_hit": len(hit_facts),
        "facts_missed": missed_facts,
        "behavior_expected": ticket.expected_behavior or "",
        "behavior_hit": (
            ticket.expected_behavior in response if ticket.expected_behavior else None
        ),
    }

    levels = business_levels(
        request_ok=request_ok,
        flow_done=flow_done,
        business_done=business_done,
        result_ok=result_ok,
        latency_ms=metrics.get("e2e_ms", 0.0) or 0.0,
        slo_ms=slo_ms,
        guardrail=metrics.get("guardrail"),
    )
    return {
        "ticket_id": ticket.ticket_id,
        "query": ticket.user_query,
        "expected_intent": ticket.expected_intent,
        "response": response,
        "metrics": metrics,
        "levels": levels,
        "quality": quality,
    }


def agent_path_ok(ticket: Ticket, metrics: dict) -> bool:
    """路径校验（案例二）：实际编排的 Agent 集合须完整覆盖工单的必需执行者。

    判定规则：
    - supervisor 为隐含执行者，不参与扇出清单比对；
    - 专业 Agent（order/logistics/knowledge）以其负责的工具为准：
      工单 expected_tools 含该工具时，该 Agent 必须出现在 plan_agents；
      expected_agents 中出现、但对应工具不在 expected_tools 中的条目视为幽灵期望，
      不强制要求（典型如 SF 单号工单的 order_agent）；
    - validation_agent 在执行校验步骤时满足，出现在 trace.validation 即可。
    """
    trace = metrics.get("trace") or {}
    plan = set(trace.get("plan_agents") or [])
    validation = trace.get("validation") or {}
    agent_tools = {
        "order_agent": "query_order",
        "logistics_agent": "query_logistics",
        "knowledge_agent": "search_knowledge",
    }
    expected_tools = set(ticket.expected_tools or [])
    for expected in (ticket.expected_agents or []):
        if expected == "supervisor":
            continue
        if expected == "validation_agent":
            if not validation.get("status"):
                return False
            continue
        tool = agent_tools.get(expected)
        if tool is not None and tool not in expected_tools:
            continue
        if expected not in plan:
            return False
    return True


def describe_degraded(businesses: list[dict]) -> dict:
    """统计一批业务的降级情况（Multi-Agent 可靠性观测）。"""
    total = len(businesses)
    degraded = sum(
        1 for b in businesses if (b.get("metrics") or {}).get("trace", {}).get("degraded")
    )
    skipped = sum(
        1
        for b in businesses
        for a in (b.get("metrics") or {}).get("trace", {}).get("agents") or []
        if a.get("status") in ("skipped_breaker",)
    )
    timed_out = sum(
        1
        for b in businesses
        for a in (b.get("metrics") or {}).get("trace", {}).get("agents") or []
        if a.get("status") in ("timeout", "error")
    )
    fan_sums = [
        (b.get("metrics") or {}).get("trace", {}).get("fanout", {}).get("efficiency")
        for b in businesses
    ]
    fan_sums = [v for v in fan_sums if v]
    return {
        "业务数": total,
        "降级业务": degraded,
        "熔断跳过调用": skipped,
        "超时/错误调用": timed_out,
        "平均并行效率": round(sum(fan_sums) / len(fan_sums), 4) if fan_sums else 0.0,
    }


def failed_business(ticket: Ticket, error: str) -> dict:
    """请求层失败（连接错误/超时/非 2xx）的业务记录。"""
    return {
        "ticket_id": ticket.ticket_id,
        "query": ticket.user_query,
        "expected_intent": ticket.expected_intent,
        "response": "",
        "metrics": {"e2e_ms": 0, "error": error},
        "levels": {
            "request_ok": False,
            "flow_done": False,
            "business_done": False,
            "result_ok": False,
            "slo_ok": False,
            "security_ok": None,
            "qualified": False,
        },
    }


def aggregate(
    businesses: list[dict],
    duration_s: float,
    cost_per_1k_tokens: float = DEFAULT_COST_PER_1K_TOKENS,
) -> dict:
    """聚合一批业务结果：分位数、合格率、Goodput 与成本（合格业务分母）。

    用量与成本口径（共享 Harness 设计说明 第 7.2 节）：
    - 任一业务用量缺失时，输出已知部分与用量覆盖率，
      并令完整总成本及成功业务成本为空（不显示零成本）；
    - 用量均为估算（usage_source=estimated）时可给出估算成本，但须标注口径。
    """
    e2e = [b["metrics"].get("e2e_ms") for b in businesses
           if b["metrics"].get("e2e_ms") is not None]
    ttft = [b["metrics"].get("ttft_client_sse_ms") for b in businesses
            if b["metrics"].get("ttft_client_sse_ms") is not None]
    total = len(businesses)
    qualified = sum(1 for b in businesses if b["levels"]["qualified"])
    flow_done = sum(1 for b in businesses if b["levels"]["flow_done"])
    result_ok = sum(1 for b in businesses if b["levels"]["result_ok"])
    goodput = (
        round(qualified / duration_s, 4) if duration_s > 0 else 0.0
    )

    # 用量覆盖率：缺失用量的业务保留在分母内，成本按缺失情况收敛
    usage_missing = 0
    known_tokens = 0
    sources: set[str] = set()
    for b in businesses:
        m = b.get("metrics") or {}
        tt = m.get("total_tokens")
        if tt is None:
            usage_missing += 1
        else:
            known_tokens += int(tt)
        if m.get("usage_source"):
            sources.add(m["usage_source"])
    coverage = round((total - usage_missing) / total * 100, 2) if total else 0.0

    total_cost = known_tokens / 1000.0 * cost_per_1k_tokens
    tokens_per_qualified = round(known_tokens / qualified, 2) if qualified else None
    cost_per_qualified = (
        round(total_cost / qualified, 4) if qualified else None
    )
    if usage_missing:
        # 用量缺失：完整总成本与成功业务成本为空，只输出已知部分
        total_cost = None
        cost_per_qualified = None
        cost_kind = "不可计算（存在用量缺失）"
    elif sources and sources <= {"estimated"}:
        cost_kind = "估算成本（usage_source=estimated，非实际账单）"
    elif sources and sources <= {"reported"}:
        cost_kind = "实际用量×假设单价（单价为 mock 假设值）"
    elif sources:
        cost_kind = "混合来源×假设单价（单价为 mock 假设值）"
    else:
        # v1 历史口径：无 usage_source 字段
        cost_kind = "v1 口径（未记录用量来源）"

    # 安全聚合（第 19 讲安全门禁口径）：逐笔护栏违规计数（课程正文对应段落）
    sec_violating = 0
    sec_violations = 0
    sec_missing = 0
    for b in businesses:
        g = (b.get("metrics") or {}).get("guardrail")
        if not isinstance(g, dict):
            sec_missing += 1
            continue
        n = len(g.get("violations") or [])
        sec_violations += n
        if n:
            sec_violating += 1

    # 质量聚合（评测回填的汇总）：要素命中率按"全体期望要素"为分母，
    # 缺数据的业务计入 missing 单独统计，不拉高命中率
    kw_exp = kw_hit = facts_exp = facts_hit = qual_missing = 0
    for b in businesses:
        q = b.get("quality")
        if not isinstance(q, dict):
            qual_missing += 1
            continue
        kw_exp += q.get("keywords_expected", 0)
        kw_hit += q.get("keywords_hit", 0)
        facts_exp += q.get("facts_expected", 0)
        facts_hit += q.get("facts_hit", 0)

    return {
        "请求数": total,
        "时延指标(mm)": {
            "E2E": describe(e2e),
            "TTFT": describe(ttft),
        },
        "合格率": {
            "合格业务": qualified,
            "业务完成": flow_done,
            "结果合格": result_ok,
            "合格业务百分比": round(qualified / total * 100, 2) if total else 0.0,
        },
        "质量指标": {
            "期望要素总数(关键词)": kw_exp,
            "命中要素数(关键词)": kw_hit,
            "要素命中率(关键词,%)": round(kw_hit / kw_exp * 100, 2) if kw_exp else None,
            "期望要素总数(事实)": facts_exp,
            "命中要素数(事实)": facts_hit,
            "要素命中率(事实,%)": round(facts_hit / facts_exp * 100, 2) if facts_exp else None,
            "质量数据缺失业务数": qual_missing,
        },
        "安全指标": {
            "违规业务数": sec_violating,
            "违规条数": sec_violations,
            "违规业务率(%)": round(sec_violating / total * 100, 2) if total else 0.0,
            "安全数据缺失业务数": sec_missing,
        },
        "业务Goodput(合格业务/秒)": goodput,
        "成本指标": {
            "总token(已知部分)": known_tokens,
            "用量缺失业务数": usage_missing,
            "用量覆盖率(%)": coverage,
            "每合格业务token": tokens_per_qualified,
            "成功业务成本(元/合格业务)": cost_per_qualified,
            "总成本(元)": total_cost,
            "成本口径": cost_kind,
            "token单价(元/千token,mock假设)": cost_per_1k_tokens,
        },
    }


def load_result_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)
