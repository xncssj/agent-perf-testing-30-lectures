"""筛选知识/澄清/转人工三类分支样本，构造并静态核验缺失样本（P3-15 分支最低样本）。

字段契约完全沿用既有数据档，不发明新数据契约：

- 原集 ``testing/test-data/tickets.json``（245 条，字段见 test-data/MANIFEST.md）；
- v1.1 load 档 ``out/reference-balanced-uniform/load-stageN.json``（含 family、
  perf_tags、source_case_id、rule_version 等派生字段）；
- 分支判定：知识 = ``expected_path`` 含 ``knowledge_retrieval`` 或
  ``perf_tags.path_class == knowledge``；澄清 = ``expected_branch == clarification``；
  转人工 = ``expected_branch == human_transfer``。
- 选样优先级：原集真实工单 > load-stage3 > load-stage2 > load-stage1，每类取
  ``--per-class`` 条（默认 30，满足「每类 ≥30」）。真实样本不足时按模板构造样本，
  逐条标注 ``constructed=true`` 并写静态核验记录；服务端回放核验仍需执行人补做。

产物 JSON 落 ``--out``（默认 ``out/branch-samples/branch_samples.json``），
默认不覆盖（``--force`` 才覆盖）；输出路径限仓库内。

用法（任意目录执行）：
    python -X utf8 testing/test-data/performance-v1.1/branch_sample_filter.py
    python -X utf8 ...branch_sample_filter.py --per-class 40 --out <仓库内路径>
    python -X utf8 ...branch_sample_filter.py --no-construct   # 只用真实样本，允许报缺口
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent                      # testing/test-data
TICKETS_PATH = BASE / "tickets.json"
DEFAULT_PROFILE_DIR = HERE / "out" / "reference-balanced-uniform"
DEFAULT_OUT = HERE / "out" / "branch-samples" / "branch_samples.json"

SCHEMA = "branch-sample-selection-v1"
VERIFY_PROFILE = "branch-sample-static-v1"
DEFAULT_PER_CLASS = 30
MIN_PER_CLASS = 30
CLASS_ORDER = ("knowledge", "clarification", "human_transfer")
CLASS_LABEL = {"knowledge": "知识", "clarification": "澄清", "human_transfer": "转人工"}
CLASS_INTENTS = {
    "knowledge": {"return_policy", "exchange_policy", "contact_info"},
    "clarification": {"clarification"},
    "human_transfer": {"complaint"},
}
CLASS_BRANCH = {"knowledge": "auto_reply", "clarification": "clarification",
                "human_transfer": "human_transfer"}
CLARIFY_PATH = ["intent_recognition", "clarification"]
DATA_PATH = ["intent_recognition", "data_retrieval", "response_generation",
             "confidence_check"]
KNOWLEDGE_PATH = ["intent_recognition", "knowledge_retrieval",
                  "response_generation", "confidence_check"]
CLASS_PATH = {"knowledge": KNOWLEDGE_PATH, "clarification": CLARIFY_PATH,
              "human_transfer": DATA_PATH}
SOURCE_PRIORITY = {"original": 0, "load3": 1, "load2": 2, "load1": 3}
CLASS_CODE = {"knowledge": "KNW", "clarification": "CLR", "human_transfer": "HTF"}
CLASS_FAMILY = {"knowledge": "policy", "clarification": "clarification",
                "human_transfer": "workflow_rag"}
CLASS_STAGES = {"knowledge": [1, 2, 3], "clarification": [1, 2, 3],
                "human_transfer": [3]}
CONSTRUCT_PREFIX = "CT"


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    for parent in (HERE, *HERE.parents):
        if (parent / ".git").exists():
            return parent
    return HERE


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel_repo(path: Path) -> str:
    root = repo_root()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())


def ensure_in_repo(path: Path, what: str) -> Path:
    root = repo_root()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise SystemExit(f"{what}越出仓库：{resolved}（仓库根 {root}）")
    return resolved


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def display_width(text: str) -> int:
    """按终端显示宽度计（CJK 字符占 2 列），用于对齐中文计数表。"""
    return sum(2 if ord(char) >= 0x2E80 else 1 for char in text)


def pad(text: str, width: int, align: str = "left") -> str:
    space = max(0, width - display_width(text))
    return (" " * space + text) if align == "right" else (text + " " * space)


def write_json(path: Path, value) -> None:
    """按字节写 UTF-8（避免 Windows 文本模式把 \\n 转成 \\r\\n，保证产物可复现）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# 分支判定与底表
# ---------------------------------------------------------------------------

def classify_row(row: dict) -> str | None:
    """按既有字段判定分支类别；先看 expected_branch，再看知识检索路径。"""
    branch = row.get("expected_branch")
    if branch == "human_transfer":
        return "human_transfer"
    if branch == "clarification":
        return "clarification"
    path_class = (row.get("perf_tags") or {}).get("path_class")
    if path_class == "knowledge" or "knowledge_retrieval" in (row.get("expected_path") or []):
        return "knowledge"
    return None


def load_orders() -> tuple[set[str], list[dict]]:
    rows = load_json(BASE / "orders.json")
    return {row["order_id"] for row in rows}, rows


def load_knowledge() -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for name in ("faq", "policies", "products"):
        for doc in load_json(BASE / "knowledge-base" / f"{name}.json"):
            docs[f"{doc['category']}:{doc['title']}"] = doc
    return docs


def annotate(row: dict, *, source_type: str, file_path: Path, row_index: int,
             file_sha: str, stage: int | None = None) -> dict:
    """保留原始字段，附加来源与核验标注（不删改既有字段）。"""
    sample = dict(row)
    sample["branch_class"] = classify_row(row)
    sample["constructed"] = False
    sample["source"] = {
        "type": source_type,
        "file": rel_repo(file_path),
        "sha256": file_sha,
        "row_index": row_index,
        "stage": stage,
        "ticket_id": row["ticket_id"],
        "case_id": row.get("source_case_id") or row.get("case_id"),
    }
    return sample


def build_candidates(profile_dir: Path) -> tuple[dict[str, list[dict]], dict]:
    """汇集原集与 load 档候选，按类分组、按来源优先级与行序排序。"""
    candidates: dict[str, list[dict]] = {cls: [] for cls in CLASS_ORDER}
    inputs: dict = {}
    # 1) 原集 245 条（真实业务工单，最高优先级）
    tickets_sha = digest(TICKETS_PATH)
    original = load_json(TICKETS_PATH)
    for index, row in enumerate(original):
        cls = classify_row(row)
        if cls is None:
            continue
        candidates[cls].append(annotate(
            row, source_type="original", file_path=TICKETS_PATH,
            row_index=index, file_sha=tickets_sha))
    # 2) v1.1 load 档三阶段（stage3 优先，因为只有它含 human_transfer 分支）
    profile_manifest_path = profile_dir / "manifest.json"
    if not profile_manifest_path.is_file():
        raise SystemExit(f"数据档目录清单缺失：{profile_manifest_path}")
    profile_manifest = load_json(profile_manifest_path)
    files_sha = profile_manifest.get("files_sha256") or {}
    load_meta: dict[str, dict] = {}
    for stage in (3, 2, 1):
        path = profile_dir / f"load-stage{stage}.json"
        if not path.is_file():
            raise SystemExit(f"load 档缺失：{path}（先跑 generate.py 生成 v1.1 数据档）")
        file_sha = digest(path)
        registered = files_sha.get(path.name)
        if registered and registered != file_sha:
            raise SystemExit(f"load 档与父档清单登记指纹不一致：{path}")
        rows = load_json(path)
        load_meta[path.name] = {"sha256": file_sha, "count": len(rows),
                                "registered_sha256": registered}
        source_type = f"load{stage}"
        for index, row in enumerate(rows):
            cls = classify_row(row)
            if cls is None:
                continue
            candidates[cls].append(annotate(
                row, source_type=source_type, file_path=path,
                row_index=index, file_sha=file_sha, stage=stage))
    for cls in CLASS_ORDER:
        candidates[cls].sort(key=lambda sample: (
            SOURCE_PRIORITY[sample["source"]["type"]],
            sample["source"]["row_index"]))
    inputs = {
        "tickets": {"path": rel_repo(TICKETS_PATH), "sha256": tickets_sha,
                    "count": len(original)},
        "data_profile": {
            "dir": rel_repo(profile_dir),
            "manifest_sha256": digest(profile_manifest_path),
            "profile_id": profile_manifest.get("profile_id"),
            "dataset_version": profile_manifest.get("dataset_version"),
            "mock_rule_version": profile_manifest.get("mock_rule_version"),
            "rule_version": profile_manifest.get("rule_version"),
            "load_files": load_meta,
        },
    }
    return candidates, inputs


# ---------------------------------------------------------------------------
# 静态核验（真实样本与构造样本同一口径）
# ---------------------------------------------------------------------------

def _path_ok(row: dict, cls: str) -> bool:
    path = list(row.get("expected_path") or [])
    if cls == "knowledge":
        return "knowledge_retrieval" in path
    return path == CLASS_PATH[cls]


def _order_ok(row: dict, order_ids: set[str]) -> bool:
    order_id = row.get("order_id") or ""
    return (not order_id) or order_id in order_ids


def verify_sample(sample: dict, cls: str, order_ids: set[str],
                  kb_docs: dict[str, dict]) -> dict:
    """按既有字段口径做静态核验；返回核验记录，缺口即抛错（不放过坏样本）。"""
    checks: dict[str, bool] = {}
    problems: list[str] = []
    required = ("ticket_id", "user_query", "expected_intent", "expected_tools",
                "expected_path", "expected_branch")
    for field in required:
        present = field in sample and sample[field] not in (None, "")
        checks[field] = present
        if not present:
            problems.append(f"缺字段或为空：{field}")
    reclassified = classify_row(sample)
    checks["branch_class"] = reclassified == cls
    if reclassified != cls:
        problems.append(f"分支判定不一致：{reclassified} != {cls}")
    intent_ok = sample.get("expected_intent") in CLASS_INTENTS[cls]
    checks["expected_intent"] = intent_ok
    if not intent_ok:
        problems.append(f"意图与分支类不符：{sample.get('expected_intent')}")
    branch_ok = sample.get("expected_branch") == CLASS_BRANCH[cls]
    checks["expected_branch"] = branch_ok
    if not branch_ok:
        problems.append(f"分支值与分支类不符：{sample.get('expected_branch')}")
    path_ok = _path_ok(sample, cls)
    checks["expected_path"] = path_ok
    if not path_ok:
        problems.append(f"路径与分支类不符：{sample.get('expected_path')}")
    if sample.get("expected_tools") is not None and not isinstance(
            sample["expected_tools"], list):
        checks["expected_tools_type"] = False
        problems.append("expected_tools 不是数组")
    if sample.get("perf_tags") is not None and not isinstance(sample["perf_tags"], dict):
        checks["perf_tags_type"] = False
        problems.append("perf_tags 不是对象")
    order_ok = _order_ok(sample, order_ids)
    checks["order_id"] = order_ok
    if not order_ok:
        problems.append(f"order_id 不在 orders.json：{sample.get('order_id')}")
    if cls in ("knowledge", "human_transfer"):
        keywords_ok = bool(sample.get("expected_answer_keywords"))
        checks["expected_answer_keywords"] = keywords_ok
        if not keywords_ok:
            problems.append("知识/转人工样本缺少答复关键词")
    else:
        behavior_ok = sample.get("expected_behavior") == "提供订单号"
        checks["expected_behavior"] = behavior_ok
        if not behavior_ok:
            problems.append(f"澄清样本行为特征不符：{sample.get('expected_behavior')}")
    if cls == "knowledge":
        agents_ok = "knowledge_agent" in (sample.get("expected_agents") or [])
        checks["expected_agents"] = agents_ok
        if not agents_ok:
            problems.append("知识样本缺少 knowledge_agent")
    elif cls == "human_transfer":
        agents_ok = "supervisor" in (sample.get("expected_agents") or [])
        checks["expected_agents"] = agents_ok
        if not agents_ok:
            problems.append("转人工样本缺少 supervisor")
    reference = sample.get("reference")
    if reference:
        doc_ids = reference.get("knowledge_doc_ids") or []
        doc_ok = all(doc_id in kb_docs for doc_id in doc_ids)
        checks["reference_knowledge_doc_ids"] = doc_ok
        if not doc_ok:
            problems.append("reference.knowledge_doc_ids 不在知识库内")
        order_ref = reference.get("order_id")
        if order_ref and order_ref not in order_ids:
            checks["reference_order_id"] = False
            problems.append(f"reference.order_id 不在 orders.json：{order_ref}")
    if problems:
        raise SystemExit(
            f"分支样本静态核验失败（{sample.get('ticket_id')} / {cls}）：\n- "
            + "\n- ".join(problems))
    return {
        "profile": VERIFY_PROFILE,
        "status": "pass",
        "fields_checked": sorted(checks),
        "checks": checks,
        "service_replay": "not_run",
    }


# ---------------------------------------------------------------------------
# 构造样本（真实样本不足时的兜底，逐条标注 constructed=true）
# ---------------------------------------------------------------------------

CONSTRUCT_HANDOFF = [
    "订单 {order_id} 的服务问题我要投诉，请安排人工。",
    "我要投诉订单 {order_id} 一直没有解决的问题。",
    "订单 {order_id} 的售后处理我不满意，请转人工客服核实。",
    "订单 {order_id} 的处理结果我要投诉，请人工客服跟进。",
    "我要投诉订单 {order_id} 的服务态度问题，请安排人工处理。",
    "订单 {order_id} 的争议需要人工介入，请转人工客服。",
]
CONSTRUCT_CLARIFY = [
    "我的包裹一直没到，能帮我查一下吗？",
    "查一下我买的商品什么时候能送到。",
    "帮我看看我的单现在是什么情况。",
    "快递有几天没更新了，麻烦跟进一下。",
    "我买的东西到哪一步了？",
    "这件商品的配送进度麻烦查一下。",
    "我的订单好像有问题，帮我看下。",
    "物流信息不更新了，麻烦处理一下。",
]
CONSTRUCT_KNOWLEDGE = [
    ("退货申请提交后多久能收到退款？", "return_policy", ["退款"]),
    ("换货需要满足什么条件？", "exchange_policy", ["换货"]),
    ("人工客服的服务时间是什么时候？", "contact_info", ["客服"]),
    ("商品有质量问题可以退货吗？", "return_policy", ["退货"]),
    ("退款审核一般多久完成？", "return_policy", ["退款"]),
    ("如何联系在线人工客服？", "contact_info", ["客服"]),
    ("七天无理由退货怎么操作？", "return_policy", ["无理由"]),
    ("换货流程需要几步？", "exchange_policy", ["换货"]),
]
# 构造样本的服务端可走通性复核要点（脚本内无法执行服务回放，交执行人按此核对）
REVIEW_CHECKLISTS = {
    "knowledge": ["阶段三 knowledge_retrieval 节点命中",
                  "search_knowledge 由检索节点等价承载",
                  "答复命中 expected_answer_keywords"],
    "clarification": ["阶段三 clarification 分支命中",
                      "答复包含 expected_behavior 文本（提供订单号）"],
    "human_transfer": ["阶段三 human_transfer 分支命中",
                       "实际执行轨迹出现 query_order",
                       "答复包含 expected_answer_keywords（人工客服）"],
}


def _next_constructed_id(cls: str, seq: int) -> str:
    return f"{CONSTRUCT_PREFIX}-{CLASS_CODE[cls]}-{seq:03d}"


def construct_samples(cls: str, count: int, *, rule_version: str,
                      order_ids: set[str], orders: list[dict],
                      used_queries: set[str]) -> list[dict]:
    """按类模板构造样本；返回逐条标注 constructed=true 的样本数组。"""
    samples: list[dict] = []
    shipped = sorted((row for row in orders if row.get("status") == "shipped"),
                     key=lambda row: row["order_id"])
    seq = 0
    while len(samples) < count:
        seq += 1
        ticket_id = _next_constructed_id(cls, seq)
        if cls == "human_transfer":
            order = shipped[(seq - 1) % len(shipped)]
            template = CONSTRUCT_HANDOFF[(seq - 1) % len(CONSTRUCT_HANDOFF)]
            query = template.format(order_id=order["order_id"])
            sample = {
                "case_id": ticket_id,
                "ticket_id": ticket_id,
                "family": CLASS_FAMILY[cls],
                "applicable_stages": list(CLASS_STAGES[cls]),
                "user_id": order["user_id"],
                "user_query": query,
                "order_id": order["order_id"],
                "expected_intent": "complaint",
                "expected_tools": ["query_order"],
                "expected_answer_keywords": ["人工客服"],
                "expected_facts": {},
                "expected_agents": ["supervisor", "order_agent", "validation_agent"],
                "expected_path": list(DATA_PATH),
                "expected_branch": "human_transfer",
                "difficulty": "medium",
                "reference": {
                    "order_id": order["order_id"],
                    "tracking_no": order.get("tracking_no"),
                    "knowledge_doc_ids": [],
                    "basis": "构造样本：静态核验；服务端回放待执行",
                    "reference_answer": "投诉问题建议联系人工客服；系统未实现真正转交时，"
                                        "不得声称已经转交。",
                },
                "perf_tags": {"data_class": "shipped", "path_class": "one_tool",
                              "input_size": "short", "fault_profile": "none"},
                "rule_version": rule_version,
            }
        elif cls == "clarification":
            template = CONSTRUCT_CLARIFY[(seq - 1) % len(CONSTRUCT_CLARIFY)]
            query = template if seq <= len(CONSTRUCT_CLARIFY) else (
                f"{template}（补充样本 {seq:03d}）")
            sample = {
                "case_id": ticket_id,
                "ticket_id": ticket_id,
                "family": CLASS_FAMILY[cls],
                "applicable_stages": list(CLASS_STAGES[cls]),
                "user_id": f"UCT{seq:08d}",
                "user_query": query,
                "order_id": "",
                "expected_intent": "clarification",
                "expected_tools": [],
                "expected_answer_keywords": [],
                "expected_behavior": "提供订单号",
                "expected_facts": {},
                "expected_agents": ["supervisor"],
                "expected_path": list(CLARIFY_PATH),
                "expected_branch": "clarification",
                "difficulty": "hard",
                "reference": {"knowledge_doc_ids": [],
                              "basis": "构造样本：静态核验；服务端回放待执行"},
                "perf_tags": {"data_class": "none", "path_class": "clarify",
                              "input_size": "short", "fault_profile": "none"},
                "rule_version": rule_version,
            }
        else:
            query_tmpl, intent, keywords = CONSTRUCT_KNOWLEDGE[
                (seq - 1) % len(CONSTRUCT_KNOWLEDGE)]
            query = query_tmpl if seq <= len(CONSTRUCT_KNOWLEDGE) else (
                f"{query_tmpl}（补充样本 {seq:03d}）")
            sample = {
                "case_id": ticket_id,
                "ticket_id": ticket_id,
                "family": CLASS_FAMILY[cls],
                "applicable_stages": list(CLASS_STAGES[cls]),
                "user_id": f"UCT{seq:08d}",
                "user_query": query,
                "order_id": "",
                "expected_intent": intent,
                "expected_tools": ["search_knowledge"],
                "expected_answer_keywords": keywords,
                "expected_facts": {},
                "expected_agents": ["supervisor", "knowledge_agent",
                                    "validation_agent"],
                "expected_path": list(KNOWLEDGE_PATH),
                "expected_branch": "auto_reply",
                "difficulty": "medium",
                "reference": {"knowledge_doc_ids": [],
                              "basis": "构造样本：静态核验；服务端回放待执行"},
                "perf_tags": {"data_class": "none", "path_class": "knowledge",
                              "input_size": "short", "fault_profile": "none"},
                "rule_version": rule_version,
            }
        if sample["user_query"] in used_queries:
            continue
        used_queries.add(sample["user_query"])
        sample["branch_class"] = cls
        sample["constructed"] = True
        sample["source"] = {
            "type": "constructed",
            "file": None,
            "sha256": None,
            "row_index": None,
            "stage": 3 if cls == "human_transfer" else None,
            "ticket_id": ticket_id,
            "case_id": ticket_id,
            "built_by": "branch_sample_filter.py 构造模板",
        }
        samples.append(sample)
    return samples


# ---------------------------------------------------------------------------
# 汇总与产物
# ---------------------------------------------------------------------------

def select_quota(pool: list[dict], per_class: int) -> list[dict]:
    """类内按 expected_intent 轮转取前 N 条，避免单类样本被某一种意图占满。

    候选已按「原集 > load-stage3 > load-stage2 > load-stage1 + 行序」排好；分组
    轮转后仍保证同类内原集优先，同时让知识类的 return_policy / exchange_policy /
    contact_info 三种意图都进入样本。
    """
    groups: dict[str, list[dict]] = {}
    for sample in pool:
        groups.setdefault(str(sample.get("expected_intent") or "(无意图)"), []).append(sample)
    intents = sorted(groups)
    chosen: list[dict] = []
    round_no = 0
    longest = max(len(rows) for rows in groups.values())
    while len(chosen) < per_class and round_no < longest:
        for intent in intents:
            if len(chosen) >= per_class:
                break
            rows = groups[intent]
            if round_no < len(rows):
                chosen.append(rows[round_no])
        round_no += 1
    return chosen


def collect(*, profile_dir: Path, per_class: int,
            construct_missing: bool = True) -> dict:
    """执行三类分支筛选与构造，返回产物结构（不写盘，供 C3 复用）。"""
    if per_class < MIN_PER_CLASS:
        raise SystemExit(f"--per-class 不得低于 {MIN_PER_CLASS}（逐类最低样本 30）")
    if not TICKETS_PATH.is_file():
        raise SystemExit(f"原集缺失：{TICKETS_PATH}")
    order_ids, orders = load_orders()
    kb_docs = load_knowledge()
    candidates, inputs = build_candidates(profile_dir)
    rule_version = str(inputs["data_profile"].get("rule_version") or "")
    selected_by_class: dict[str, list[dict]] = {}
    class_rows: list[dict] = []
    used_queries: set[str] = set()
    used_tickets: set[str] = set()
    constructed_total = 0
    for cls in CLASS_ORDER:
        real_pool = candidates[cls]
        real_take = select_quota(real_pool, per_class)
        for sample in real_take:
            sample["verification"] = verify_sample(sample, cls, order_ids, kb_docs)
            if sample["ticket_id"] in used_tickets:
                raise SystemExit(f"样本 ticket_id 重复：{sample['ticket_id']}")
            used_tickets.add(sample["ticket_id"])
            used_queries.add(sample["user_query"])
        selected = list(real_take)
        shortfall = max(0, per_class - len(selected))
        constructed: list[dict] = []
        if shortfall and construct_missing:
            constructed = construct_samples(
                cls, shortfall, rule_version=rule_version,
                order_ids=order_ids, orders=orders, used_queries=used_queries)
            for sample in constructed:
                sample["verification"] = verify_sample(
                    sample, cls, order_ids, kb_docs)
                # 构造样本的核验记录额外标注人工/服务端核验待办
                sample["verification"].update({
                    "constructed": True,
                    "service_replay": "pending",
                    "manual_review": "required",
                })
                sample["construction"] = {
                    "template_class": cls,
                    "basis": "按既有字段契约构造，静态核验通过；"
                             "服务端可走通性回放待执行",
                    "review_checklist": REVIEW_CHECKLISTS[cls],
                }
                if sample["ticket_id"] in used_tickets:
                    raise SystemExit(f"构造样本 ticket_id 重复：{sample['ticket_id']}")
                used_tickets.add(sample["ticket_id"])
            selected.extend(constructed)
            constructed_total += len(constructed)
        selected_by_class[cls] = selected
        class_rows.append({
            "class": cls,
            "label_zh": CLASS_LABEL[cls],
            "target": per_class,
            "available_real": len(real_pool),
            "available_by_source": dict(sorted(Counter(
                sample["source"]["type"] for sample in real_pool).items())),
            "selected": len(selected),
            "selected_real": len(real_take),
            "selected_original": sum(1 for sample in real_take
                                     if sample["source"]["type"] == "original"),
            "selected_intent_counts": dict(sorted(Counter(
                sample["expected_intent"] for sample in selected).items())),
            "constructed": len(constructed),
            "shortfall": shortfall,
            "quota_pass": len(selected) >= per_class,
            "detection": {
                "knowledge": "expected_path 含 knowledge_retrieval 或 "
                             "perf_tags.path_class=knowledge",
                "clarification": "expected_branch=clarification",
                "human_transfer": "expected_branch=human_transfer",
            }[cls],
        })
    samples = [sample for cls in CLASS_ORDER for sample in selected_by_class[cls]]
    counts_by_source = Counter(sample["source"]["type"] for sample in samples)
    total = len(samples)
    return {
        "schema": SCHEMA,
        "dataset_version": inputs["data_profile"].get("dataset_version"),
        "mock_rule_version": inputs["data_profile"].get("mock_rule_version"),
        "generated_at": utc_now(),
        "generator": "branch_sample_filter.py",
        "generator_sha256": digest(HERE / "branch_sample_filter.py"),
        "inputs": {**inputs, "per_class": per_class,
                   "construct_missing": construct_missing,
                   "class_order": list(CLASS_ORDER)},
        "verification_profile": VERIFY_PROFILE,
        "counts": {
            "target_per_class": per_class,
            "expected_total": per_class * len(CLASS_ORDER),
            "total": total,
            "quota_pass": total >= per_class * len(CLASS_ORDER),
            "by_class": {row["class"]: row["selected"] for row in class_rows},
            "by_source": dict(sorted(counts_by_source.items())),
            "real": total - constructed_total,
            "constructed": constructed_total,
        },
        "classes": class_rows,
        "samples": samples,
        "notes": [
            "原字段未删改；branch_class/constructed/source/verification 为标注字段。",
            "服务端回放核验未在脚本内执行，verification.service_replay 如实记录。",
            "冻结进回归集时按「原集优先 + load 档补充 + 构造样本单独标注」使用。",
        ],
    }


def format_count_table(payload: dict) -> str:
    """输出逐类计数表（人工可读，按全角显示宽度对齐）。"""
    header = (pad("类别", 20) + pad("目标", 6, "right") + pad("真实可用", 10, "right")
              + pad("采用", 6, "right") + pad("构造", 6, "right")
              + pad("缺口", 6, "right"))
    lines = [f"分支样本计数（每类目标 ≥{payload['counts']['target_per_class']}）",
             header, "-" * 62]
    for row in payload["classes"]:
        label = f"{row['label_zh']}({row['class']})"
        lines.append(pad(label, 20) + pad(str(row["target"]), 6, "right")
                     + pad(str(row["available_real"]), 10, "right")
                     + pad(str(row["selected"]), 6, "right")
                     + pad(str(row["constructed"]), 6, "right")
                     + pad(str(row["shortfall"]), 6, "right"))
    counts = payload["counts"]
    lines.append("-" * 62)
    lines.append(pad("合计", 20) + pad(str(counts["expected_total"]), 6, "right")
                 + pad(str(sum(row["available_real"] for row in payload["classes"])),
                       10, "right")
                 + pad(str(counts["total"]), 6, "right")
                 + pad(str(counts["constructed"]), 6, "right")
                 + pad(str(max(0, counts["expected_total"] - counts["total"])),
                       6, "right"))
    return "\n".join(lines)


def main() -> None:
    global TICKETS_PATH
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile-dir", type=Path, default=None,
                        help="v1.1 数据档目录（缺省 out/reference-balanced-uniform）")
    parser.add_argument("--tickets", type=Path, default=None,
                        help=f"原集工单文件（缺省 {rel_repo(TICKETS_PATH)}）")
    parser.add_argument("--per-class", type=int, default=DEFAULT_PER_CLASS,
                        help=f"每类目标条数（缺省 {DEFAULT_PER_CLASS}，须 ≥30）")
    parser.add_argument("--out", type=Path, default=None,
                        help="产物 JSON 路径（缺省 out/branch-samples/branch_samples.json）")
    parser.add_argument("--no-construct", dest="construct", action="store_false",
                        help="真实样本不足时不构造，只记录缺口（退出码 1）")
    parser.set_defaults(construct=True)
    parser.add_argument("--force", action="store_true",
                        help="覆盖已存在的产物文件")
    args = parser.parse_args()

    if args.tickets is not None:
        TICKETS_PATH = args.tickets.resolve()
    profile_dir = (args.profile_dir or DEFAULT_PROFILE_DIR).resolve()
    if not profile_dir.is_dir():
        raise SystemExit(f"数据档目录不存在：{profile_dir}")
    if args.per_class < MIN_PER_CLASS:
        raise SystemExit(f"--per-class 不得低于 {MIN_PER_CLASS}（逐类最低样本 30）")
    out_path = ensure_in_repo(args.out.resolve() if args.out else DEFAULT_OUT,
                              "产物路径")
    if out_path.exists() and not args.force:
        raise SystemExit(f"产物已存在，未覆盖（--force 才覆盖）：{out_path}")

    payload = collect(profile_dir=profile_dir, per_class=args.per_class,
                      construct_missing=args.construct)
    payload["inputs"]["out"] = rel_repo(out_path)
    write_json(out_path, payload)
    print(format_count_table(payload))
    print(json.dumps({
        "out": rel_repo(out_path),
        "total": payload["counts"]["total"],
        "by_class": payload["counts"]["by_class"],
        "by_source": payload["counts"]["by_source"],
        "constructed": payload["counts"]["constructed"],
        "quota_pass": payload["counts"]["quota_pass"],
    }, ensure_ascii=False, indent=2))
    if not payload["counts"]["quota_pass"]:
        print("警告：逐类配额未达标，产物已落盘供复盘；"
              "请补充真实样本或允许构造样本后重跑。", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
