"""核对性能用例的事实依据、生成档位关系与文件指纹。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
CORE_FAMILIES = ("order", "logistics", "pending", "abnormal", "policy",
                 "contact", "clarification", "compound")
STAGES = {1, 2, 3}
STATUS_ZH = {"pending": "待发货", "shipped": "已发货", "exception": "异常"}
PROFILES = {"reference": (1, 1), "db10": (10, 1), "kb10": (1, 10),
            "db100": (100, 1), "kb100": (1, 100), "combined10": (10, 10)}
RULE = "agent-performance-rule-v1"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_source_manifest() -> dict:
    source = load(HERE / "source-manifest.json")
    if (source.get("schema_version") != "agent-performance-data-v1"
            or source.get("rule_version") != RULE
            or source.get("golden_case_count") != 64
            or source.get("profiles") != {name: list(scale) for name, scale in PROFILES.items()}):
        raise ValueError("源版本清单与数据契约不一致")
    for rel, expected in source["source_sha256"].items():
        path = BASE / rel
        if not path.is_file() or sha(path) != expected:
            raise ValueError(f"源版本清单文件哈希不匹配：{rel}")
    return source


def rag_index_version(kb: dict[str, list[dict]]) -> str:
    docs = [doc for rows in kb.values() for doc in rows]
    hashed = hashlib.sha256()
    for doc in sorted(docs, key=lambda item: f"{item['category']}:{item['title']}"):
        hashed.update((f"{doc['category']}:{doc['title']}\x00" + doc["content"]).encode("utf-8"))
    return hashed.hexdigest()[:8]


def knowledge_docs(kb: dict[str, list[dict]]) -> dict[str, dict]:
    return {f"{doc['category']}:{doc['title']}": doc
            for docs in kb.values() for doc in docs}


def check_ticket(row: dict, orders: dict[str, dict], logs: dict[str, dict],
                 docs: dict[str, dict]) -> None:
    case_id = row.get("case_id", row.get("ticket_id", "?"))
    required = ("ticket_id", "user_query", "expected_intent", "expected_tools",
                "expected_facts", "expected_agents", "expected_path", "expected_branch",
                "applicable_stages", "reference", "perf_tags", "rule_version")
    for key in required:
        if key not in row:
            raise ValueError(f"{case_id}: 缺少 {key}")
    if not row["user_query"] or not row["reference"].get("basis"):
        raise ValueError(f"{case_id}: 输入或参考依据为空")
    length = len(row["user_query"])
    input_size = "long" if length >= 121 else ("medium" if length >= 50 else "short")
    if row["perf_tags"].get("input_size") != input_size:
        raise ValueError(f"{case_id}: 输入长度标签与实际不符")
    if row["rule_version"] != RULE or not row["reference"].get("reference_answer"):
        raise ValueError(f"{case_id}: 规则版本或参考答复缺失")
    if not row.get("expected_answer_keywords") and not row.get("expected_behavior"):
        raise ValueError(f"{case_id}: 缺少答复或行为判定")
    answer = row["reference"]["reference_answer"]
    if any(keyword not in answer for keyword in row.get("expected_answer_keywords", [])):
        raise ValueError(f"{case_id}: 答复要点无法从参考依据核对")
    if row.get("expected_behavior") and row["expected_behavior"] not in answer:
        raise ValueError(f"{case_id}: 预期行为无法从参考依据核对")
    stages = row["applicable_stages"]
    if not stages or set(stages) - STAGES:
        raise ValueError(f"{case_id}: 适用阶段错误")
    fault_profile = row["perf_tags"].get("fault_profile")
    if fault_profile not in ("none", "agent_timeout", "breaker"):
        raise ValueError(f"{case_id}: 故障类型无效")
    if fault_profile != "none":
        scenario = row["reference"].get("fault_scenario") or {}
        env = scenario.get("required_env") or {}
        expected = scenario.get("expected_trace") or {}
        if (stages != [2] or row.get("family") != "multi_agent"
                or env.get("AGENT_FAULT_ENABLED") != "true"
                or env.get("AGENT_FAULT_TARGET") != "logistics_agent"
                or env.get("AGENT_DEGRADATION") != "true"
                or expected.get("degraded") is not True
                or expected.get("degraded_agent") != "logistics_agent"
                or not scenario.get("expected_outcome")):
            raise ValueError(f"{case_id}: 故障预条件或降级预期不完整")
        mode, status = (("timeout", "timeout") if fault_profile == "agent_timeout"
                        else ("error", "skipped_breaker"))
        if (env.get("AGENT_FAULT_MODE"), expected.get("agent_status")) != (mode, status):
            raise ValueError(f"{case_id}: 故障类型与执行预期不一致")
    if 3 in stages and not row["expected_path"]:
        raise ValueError(f"{case_id}: 阶段三缺少路径证据")
    if row["expected_branch"] not in ("auto_reply", "clarification", "human_transfer"):
        raise ValueError(f"{case_id}: 分支值无效")
    order_id = row.get("order_id") or ""
    data_class = row["perf_tags"].get("data_class")
    if order_id:
        order = orders.get(order_id)
        if not order:
            raise ValueError(f"{case_id}: 引用订单不存在 {order_id}")
        if row["reference"].get("order_id") != order_id:
            raise ValueError(f"{case_id}: 参考订单与请求不一致")
        if order_id not in row["user_query"]:
            raise ValueError(f"{case_id}: 问题未包含引用订单")
        facts = row["expected_facts"]
        expected = {"order_id": order_id, "status": STATUS_ZH[order["status"]],
                    "total_amount": str(order["total_amount"])}
        log = logs.get(order_id)
        if log:
            expected.update(current_location=log["current_location"],
                            estimated_delivery=log["estimated_delivery"])
        for key, value in facts.items():
            if key not in expected or str(value) != str(expected[key]):
                raise ValueError(f"{case_id}: 事实 {key} 与铺底数据不符")
            if str(value) not in answer:
                raise ValueError(f"{case_id}: 参考答复缺少事实 {key}")
        if data_class == "pending" and order["status"] != "pending":
            raise ValueError(f"{case_id}: 待发货分类不符")
        if data_class == "shipped" and (order["status"] != "shipped" or not log
                                        or log["status"] != "运输中"):
            raise ValueError(f"{case_id}: 正常物流分类不符")
        if data_class == "abnormal" and (not log or log["status"] == "运输中"):
            raise ValueError(f"{case_id}: 异常物流分类不符")
        if data_class == "order_only" and log:
            raise ValueError(f"{case_id}: 纯订单分类有物流记录")
        track = row["reference"].get("tracking_no")
        if track and (not log or track != log["tracking_no"]):
            raise ValueError(f"{case_id}: 运单引用不一致")
    elif data_class != "none":
        raise ValueError(f"{case_id}: 无订单用例的数据分类错误")
    if row.get("family") == "abnormal" and not order_id:
        missing_ids = re.findall(r"(?<!\d)\d{13}(?!\d)", row["user_query"])
        if not missing_ids or any(value in orders for value in missing_ids):
            raise ValueError(f"{case_id}: 缺失订单用例的订单号实际存在")
    for doc_id in row["reference"].get("knowledge_doc_ids", []):
        if doc_id not in docs:
            raise ValueError(f"{case_id}: 知识文档不存在 {doc_id}")
        if docs[doc_id]["content"] not in answer:
            raise ValueError(f"{case_id}: 参考答复未引用知识文档 {doc_id}")


def validate_cases(cases: list[dict], orders: list[dict], logistics: list[dict],
                   kb: dict[str, list[dict]]) -> None:
    if len(cases) != 64:
        raise ValueError(f"黄金用例必须为 64 条，实际 {len(cases)}")
    ids = [row.get("case_id") for row in cases]
    if len(set(ids)) != 64 or any(not value for value in ids):
        raise ValueError("黄金用例 ID 重复或为空")
    counts = Counter(row["family"] for row in cases)
    for family in CORE_FAMILIES:
        if counts[family] != 6:
            raise ValueError(f"{family}: 应有 6 条，实际 {counts[family]}")
    if counts["multi_agent"] != 8 or counts["workflow_rag"] != 8:
        raise ValueError("两类阶段专项用例应各有 8 条")
    multi_faults = Counter(row["perf_tags"].get("fault_profile") for row in cases
                           if row["family"] == "multi_agent")
    if multi_faults != {"none": 4, "agent_timeout": 2, "breaker": 2}:
        raise ValueError("Multi-Agent 专项须含 4 条扇出、2 条超时、2 条熔断用例")
    order_map = {row["order_id"]: row for row in orders}
    log_map = {row["order_id"]: row for row in logistics}
    docs = knowledge_docs(kb)
    for row in cases:
        if row.get("ticket_id") != row["case_id"]:
            raise ValueError(f"{row['case_id']}: 黄金用例 ticket_id 应等于 case_id")
        if row["family"] in CORE_FAMILIES and row["applicable_stages"] != [1, 2, 3]:
            raise ValueError(f"{row['case_id']}: 共用用例须覆盖三阶段")
        if row["family"] == "multi_agent" and row["applicable_stages"] != [2]:
            raise ValueError(f"{row['case_id']}: Multi-Agent 专项阶段错误")
        if row["family"] == "workflow_rag" and row["applicable_stages"] != [3]:
            raise ValueError(f"{row['case_id']}: Workflow 专项阶段错误")
        check_ticket(row, order_map, log_map, docs)


def validate_generated(root: Path) -> dict:
    source_manifest = validate_source_manifest()
    manifest = load(root / "manifest.json")
    if manifest.get("schema_version") != "agent-performance-data-v1":
        raise ValueError("数据包 schema_version 不匹配")
    if (manifest.get("generator_sha256") != sha(HERE / "generate.py")
            or manifest.get("validator_sha256") != sha(HERE / "validate.py")):
        raise ValueError("生成器或校验器版本与数据档不一致")
    profile = manifest.get("profile_id")
    if profile not in PROFILES or manifest.get("rule_version") != RULE:
        raise ValueError("数据档或规则版本不匹配")
    if manifest.get("mix") not in ("balanced", "current_shape") or manifest.get("access") not in (
            "uniform", "hotspot_80_20", "broad"):
        raise ValueError("类别配比或访问模式无效")
    for rel, expected in manifest["source_hashes"].items():
        source = BASE / rel
        if not source.is_file() or sha(source) != expected:
            raise ValueError(f"生成来源已变化：{rel}")
    expected_sources = set(source_manifest["source_sha256"]) | {
        "performance-v1/source-manifest.json"}
    if set(manifest["source_hashes"]) != expected_sources:
        raise ValueError("生成来源文件清单不完整")
    expected_outputs = {"orders.json", "logistics.json", "knowledge-base/faq.json",
                        "knowledge-base/policies.json", "knowledge-base/products.json"}
    expected_outputs |= {f"{kind}-stage{stage}.json" for stage in (1, 2, 3)
                         for kind in ("golden", "fixed", "load")}
    if set(manifest["files_sha256"]) != expected_outputs:
        raise ValueError("生成产物文件清单不完整")
    for rel, expected in manifest["files_sha256"].items():
        path = root / rel
        if not path.is_file() or sha(path) != expected:
            raise ValueError(f"文件缺失或哈希不一致：{rel}")
    orders = load(root / "orders.json")
    logistics = load(root / "logistics.json")
    kb = {name: load(root / "knowledge-base" / f"{name}.json")
          for name in ("faq", "policies", "products")}
    original_orders = load(BASE / "orders.json")
    original_logs = load(BASE / "logistics.json")
    if orders[:len(original_orders)] != original_orders or logistics[:len(original_logs)] != original_logs:
        raise ValueError("共享基准订单或物流被改变")
    for name, docs in kb.items():
        original = load(BASE / "knowledge-base" / f"{name}.json")
        if docs[:len(original)] != original:
            raise ValueError(f"共享基准知识文档被改变：{name}")
    if len(orders) != manifest["counts"]["orders"] or len(logistics) != manifest["counts"]["logistics"]:
        raise ValueError("物理数据条数与清单不符")
    if sum(map(len, kb.values())) != manifest["counts"]["knowledge"]:
        raise ValueError("知识文档条数与清单不符")
    if rag_index_version(kb) != manifest.get("expected_rag_index_version"):
        raise ValueError("知识索引预期指纹与文档不符")
    db_scale, kb_scale = PROFILES[profile]
    if (manifest.get("db_scale"), manifest.get("kb_scale")) != (db_scale, kb_scale):
        raise ValueError("档位倍数与档位身份不符")
    expected_counts = {"orders": len(original_orders) * db_scale,
                       "logistics": len(original_logs) * db_scale,
                       "knowledge": sum(len(load(BASE / "knowledge-base" / f"{name}.json"))
                                        for name in ("faq", "policies", "products")) * kb_scale}
    if manifest["counts"] != expected_counts:
        raise ValueError(f"数据档规模不符：{manifest['counts']} != {expected_counts}")
    order_map = {row["order_id"]: row for row in orders}
    log_map = {row["order_id"]: row for row in logistics}
    if len(order_map) != len(orders) or len(log_map) != len(logistics):
        raise ValueError("订单或物流的关联 ID 重复")
    if len({row["tracking_no"] for row in logistics}) != len(logistics):
        raise ValueError("运单号重复")
    for log in logistics:
        order = order_map.get(log["order_id"])
        if not order or order["tracking_no"] != log["tracking_no"]:
            raise ValueError(f"物流与订单不一致：{log['order_id']}")
    docs = knowledge_docs(kb)
    if len(docs) != manifest["counts"]["knowledge"]:
        raise ValueError("知识文档身份重复")
    numeric_doc_ids = [doc["id"] for doc in docs.values() if "id" in doc]
    if len(set(numeric_doc_ids)) != len(numeric_doc_ids):
        raise ValueError("合成知识文档数字 ID 重复")
    all_ids = set()
    selected_orders: Counter = Counter()
    authored_cases = load(HERE / "cases.json")
    for stage in (1, 2, 3):
        golden = load(root / f"golden-stage{stage}.json")
        load_pool = load(root / f"load-stage{stage}.json")
        fixed_pool = load(root / f"fixed-stage{stage}.json")
        expected_golden = 48 if stage == 1 else 56
        if golden != [case for case in authored_cases if stage in case["applicable_stages"]]:
            raise ValueError(f"阶段 {stage} 黄金用例与源文件不一致")
        if (len(golden) != expected_golden or len(load_pool) != manifest["pool_size"]
                or len(fixed_pool) != manifest["pool_size"]):
            raise ValueError(f"阶段 {stage} 的用例条数不符")
        for row in golden + load_pool + fixed_pool:
            if stage not in row["applicable_stages"]:
                raise ValueError(f"{row['ticket_id']}: 阶段不适用")
            check_ticket(row, order_map, log_map, docs)
        for row in load_pool + fixed_pool:
            if row["ticket_id"] in all_ids:
                raise ValueError(f"负载任务 ID 重复：{row['ticket_id']}")
            if row.get("source_case_id") not in {case["case_id"] for case in golden}:
                raise ValueError(f"负载任务来源无效：{row['ticket_id']}")
            all_ids.add(row["ticket_id"])
        selected_orders.update(row["order_id"] for row in load_pool if row.get("order_id"))
        expected_stage = manifest["per_stage"][str(stage)]
        if (expected_stage["golden"] != len(golden) or expected_stage["load"] != len(load_pool)
                or expected_stage["fixed"] != len(fixed_pool)
                or expected_stage["family_counts"] != dict(sorted(Counter(row["family"] for row in load_pool).items()))
                or expected_stage["intent_counts"] != dict(sorted(Counter(row["expected_intent"] for row in load_pool).items()))):
            raise ValueError(f"阶段 {stage} 的实际分布与清单不符")
    access_actual = manifest["access_actual"]
    top_keys = sorted(selected_orders.items(), key=lambda item: (-item[1], item[0]))[:10]
    if (access_actual["selected_order_ids"] != len(selected_orders)
            or access_actual["order_queries"] != sum(selected_orders.values())
            or access_actual["top10_order_id_counts"] != [list(item) for item in top_keys]):
        raise ValueError("实际访问键分布与清单不符")
    expected_hot_fraction = (round(access_actual["hotset_draws"] /
                                   access_actual["order_queries"], 6)
                             if manifest["access"] == "hotspot_80_20"
                             and access_actual["order_queries"] else None)
    if access_actual.get("hotset_fraction") != expected_hot_fraction:
        raise ValueError("热键实际比例与清单不符")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    validate_source_manifest()
    original_orders = load(BASE / "orders.json")
    original_logs = load(BASE / "logistics.json")
    original_kb = {name: load(BASE / "knowledge-base" / f"{name}.json")
                   for name in ("faq", "policies", "products")}
    validate_cases(load(HERE / "cases.json"), original_orders, original_logs, original_kb)
    if args.data_dir:
        result = validate_generated(args.data_dir)
        print(f"校验通过：{result['profile_id']} / {result['counts']}")
    else:
        print("64 条黄金用例校验通过")


if __name__ == "__main__":
    main()
