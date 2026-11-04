"""压测公共模块（案例三 Workflow+RAG）：工单加载、四级判定、聚合。

判定口径对齐课程与 MANIFEST：
- 结果合格（AND 语义，缺一即不合格）：
  1. 全部 expected_answer_keywords 出现在答复中
  2. 全部 expected_facts 取值出现在答复中
  3. expected_behavior 出现在答复中（澄清分支：提供订单号）
  4. 实际调用工具 ⊇ expected_tools（search_knowledge 对应 knowledge_retrieval 节点）
  5. 遍历路径 == expected_path
  6. 分支 == expected_branch
- 业务 Goodput = 合格业务数 / 实测时长（业务/秒）
- 成本 = 总 token × token_price_per_1k(0.15元/千token) / 合格业务数
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

SHARED_DATA_DIR = PROJECT_ROOT.parent.parent / "testing" / "test-data"

MAIN_PATH = ["intent_recognition", "data_retrieval", "response_generation", "confidence_check"]

# rule 回退的意图修正映射（与 IntentDetector 对齐）
_TOOL_OF_PATH = {"data_retrieval": "query_order", "knowledge_retrieval": "search_knowledge"}


@dataclass
class Ticket:
    ticket_id: str
    user_query: str
    expected_intent: str = ""
    expected_tools: list[str] = field(default_factory=list)
    expected_path: list[str] = field(default_factory=list)
    expected_branch: str = ""
    expected_answer_keywords: list[str] = field(default_factory=list)
    expected_behavior: str = ""
    expected_facts: dict = field(default_factory=dict)
    difficulty: str = "easy"
    order_id: str = ""


def load_tickets(path: Path | str) -> list[Ticket]:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    tickets = []
    for item in data:
        tickets.append(
            Ticket(
                ticket_id=item.get("ticket_id", ""),
                user_query=item.get("user_query", ""),
                expected_intent=item.get("expected_intent", ""),
                expected_tools=list(item.get("expected_tools") or []),
                expected_path=list(item.get("expected_path") or []),
                expected_branch=item.get("expected_branch", ""),
                expected_answer_keywords=list(
                    item.get("expected_answer_keywords")
                    or item.get("expected_answer_contains")
                    or []
                ),
                expected_behavior=item.get("expected_behavior", ""),
                expected_facts=dict(item.get("expected_facts") or {}),
                difficulty=item.get("difficulty", "easy"),
                order_id=item.get("order_id", ""),
            )
        )
    return tickets


def tools_matched(ticket: Ticket, trace_tools: list[str], path: list[str]) -> bool:
    """工具覆盖校验：search_knowledge 以 knowledge_retrieval 路径节点等效满足。"""
    expected = set(ticket.expected_tools or [])
    actual = set(trace_tools or [])
    if "search_knowledge" not in expected:
        return expected <= actual
    # 知识类：路径出现 knowledge_retrieval 即等效工具命中
    return "knowledge_retrieval" in (path or [])


def classify(ticket: Ticket, result: dict, slo_ms: float, request_ok: bool = True,
             first_answer_slo_ms: float | None = None) -> dict:
    """四级判定（v2）：同时兼容 v1 执行器结果与 v2 RunResult 两种形态。

    v2 必须消费服务端终态：status 传入时 flow_done 要求 status=completed，
    运行完成不再直接等于流程完成（共享 Harness 设计说明 第 7.2 节）。
    时延为双指标口径（课程正文对应段落）：slo_ms 管完整答复 E2E；
    first_answer_slo_ms 管首字（TTFT 口径），仅当结果携带 first_answer_ms
    （v2 入口）时参与判定，v1 入口无该字段自动跳过。
    """
    response = result.get("response") or ""
    trace = result.get("trace") or {}
    metrics = result.get("metrics") or {}
    path = list(result.get("path") or trace.get("path") or [])
    branch = result.get("branch") or trace.get("branch") or ""
    trace_tools = [tc.get("tool") for tc in (result.get("tool_calls")
                                             or trace.get("tool_calls") or [])]
    first_answer_ms = metrics.get("first_answer_ms")

    status = result.get("status")
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

    path_ok = True
    if ticket.expected_path:
        path_ok = path == ticket.expected_path
    branch_ok = True
    if ticket.expected_branch:
        branch_ok = branch == ticket.expected_branch
    tool_ok = tools_matched(ticket, trace_tools, path)
    intent_ok = True
    if ticket.expected_intent:
        intent_ok = (result.get("intent") or "") == ticket.expected_intent

    result_ok = result_ok and path_ok and branch_ok and tool_ok and intent_ok

    e2e_ms = float(result.get("e2e_ms")
                   or (result.get("metrics") or {}).get("e2e_ms") or 0.0)
    levels = business_levels(
        request_ok=request_ok,
        flow_done=flow_done,
        business_done=business_done,
        result_ok=result_ok,
        latency_ms=e2e_ms,
        slo_ms=slo_ms,
        first_answer_ms=first_answer_ms,
        first_answer_slo_ms=first_answer_slo_ms,
        guardrail=trace.get("guardrail"),
    )
    total_tokens = result.get("total_tokens")
    if total_tokens is None:
        total_tokens = metrics.get("total_tokens")
    return {
        "ticket_id": ticket.ticket_id,
        "query": ticket.user_query,
        "expected_intent": ticket.expected_intent,
        "response": response,
        "status": status,
        "stop_reason": result.get("stop_reason", ""),
        "run_id": result.get("run_id", ""),
        "task_id": result.get("task_id", ""),
        "batch_id": result.get("batch_id", ""),
        "metrics": {
            "e2e_ms": e2e_ms,
            "first_answer_ms": first_answer_ms,
            "total_tokens": total_tokens,
            "usage_source": (result.get("metrics") or {}).get("usage_source"),
            "evidence_kind": (result.get("metrics") or {}).get("evidence_kind"),
            "path": path,
            "branch": branch,
            "tool_calls": trace_tools,
            "trace": trace,
        },
        "levels": levels,
        "quality": quality,
        "path_ok": path_ok,
        "branch_ok": branch_ok,
        "tool_ok": tool_ok,
        "intent_ok": intent_ok,
    }


def failed_business(ticket: Ticket, error: str) -> dict:
    return {
        "ticket_id": ticket.ticket_id,
        "query": ticket.user_query,
        "expected_intent": ticket.expected_intent,
        "response": "",
        "metrics": {"e2e_ms": 0, "total_tokens": 0, "error": error},
        "levels": {
            "request_ok": False,
            "flow_done": False,
            "business_done": False,
            "result_ok": False,
            "slo_ok": False,
            "first_answer_ok": None,
            "security_ok": None,
            "qualified": False,
        },
    }


def aggregate(businesses: list[dict], duration_s: float, cost_per_1k: float = 0.15) -> dict:
    """聚合（v2）：分位数、合格率、Goodput 与用量覆盖口径。

    任一业务用量缺失时输出已知部分与覆盖率，完整总成本及成功业务成本为空；
    用量均为估算时可给出估算成本并标注口径（共享 Harness 设计说明 第 7.2 节）。
    """
    e2e = [b["metrics"].get("e2e_ms") for b in businesses
           if b["metrics"].get("e2e_ms") is not None]
    total = len(businesses)
    qualified = sum(1 for b in businesses if b["levels"]["qualified"])
    flow_done = sum(1 for b in businesses if b["levels"]["flow_done"])
    result_ok = sum(1 for b in businesses if b["levels"]["result_ok"])
    goodput = round(qualified / duration_s, 4) if duration_s > 0 else 0.0

    usage_missing = 0
    known_tokens = 0
    sources: set[str] = set()
    for b in businesses:
        tt = (b.get("metrics") or {}).get("total_tokens")
        if tt is None:
            usage_missing += 1
        else:
            known_tokens += int(tt)
        if (b.get("metrics") or {}).get("usage_source"):
            sources.add(b["metrics"]["usage_source"])
    coverage = round((total - usage_missing) / total * 100, 2) if total else 0.0

    total_cost = known_tokens / 1000.0 * cost_per_1k
    tokens_per_qualified = round(known_tokens / qualified, 2) if qualified else None
    cost_per_qualified = round(total_cost / qualified, 4) if qualified else None
    if usage_missing:
        total_cost = None
        cost_per_qualified = None
        cost_kind = "不可计算（存在用量缺失）"
    elif sources and sources <= {"estimated"}:
        cost_kind = "估算成本（usage_source=estimated，非实际账单）"
    elif sources and sources <= {"reported"}:
        cost_kind = "实际用量×假设单价"
    elif sources:
        cost_kind = "混合来源×假设单价"
    else:
        cost_kind = "v1 口径（未记录用量来源）"

    first_answer = [b["metrics"].get("first_answer_ms") for b in businesses
                    if b["metrics"].get("first_answer_ms") is not None]
    # 安全聚合（第 19 讲安全门禁口径）：逐笔护栏违规计数，替代门禁里
    # 原先硬编码的 0。违规业务 = 护栏拦下的业务；数据缺失笔数单独统计，
    # 不与"无违规"混同（课程正文对应段落）。
    sec_violating = 0
    sec_violations = 0
    sec_missing = 0
    for b in businesses:
        g = (b.get("metrics") or {}).get("trace") or {}
        g = g.get("guardrail")
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
        "时延指标(mm)": {"E2E": describe(e2e), "首字": describe(first_answer)},
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
            "token单价(元/千token,mock假设)": cost_per_1k,
        },
    }


def load_result_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)

def dataset_sha256(path: Path | str) -> str:
    """计算共享工单集 SHA-256（同轮评测批次的数据集指纹）。"""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stamp_and_write(
    settings,
    test_type: str,
    payload: dict,
    started_at: str,
    ended_at: str,
    data_path: Path | str | None = None,
) -> Path:
    """为专项报告补齐同轮评测批次所需的运行身份并落盘（006 契约 §3）。

    每份子报告保留自己的 run_id 与有效计量窗口：
    - run_id 缺失时按 ``{test_type}-{时间戳}`` 生成，已有则保留；
    - started_at/ended_at 记录脚本真实起止（ISO8601），文件名时间戳
      不再充当有效窗口；
    - data_path 给出时把该数据文件的实际 SHA-256 写入 dataset_sha256。
    """
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    payload.setdefault("run_id", f"{test_type}-{stamp}")
    payload["started_at"] = started_at
    payload["ended_at"] = ended_at
    if data_path is not None:
        payload["dataset_sha256"] = dataset_sha256(data_path)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    out = settings.reports_dir / f"{test_type}_workflow_{stamp}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
