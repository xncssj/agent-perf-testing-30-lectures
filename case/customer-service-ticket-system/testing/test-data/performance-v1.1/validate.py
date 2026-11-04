"""核对 performance-v1.1 数据档：复用 v1 校验逻辑，按 v1.1 身份与附加字段判定。

v1.0 校验入口保持不动；本入口在 v1 结构校验之上额外核对：
- schema/rule/dataset_version/mock_rule_version 的 v1.1 身份；
- 生成器、校验器、预检脚本与数据档一致；
- 可达性预检报告存在、逐笔覆盖三阶段黄金用例且无 unreachable；
- reachability-report.json 的文件哈希被 files_sha256 收录。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
V1_DIR = HERE.parent / "performance-v1"
if str(V1_DIR) not in sys.path:
    sys.path.insert(0, str(V1_DIR))

import validate as v1  # noqa: E402
import derive  # noqa: E402

SCHEMA = "agent-performance-data-v1.1"
RULE = "agent-performance-rule-v1.1"
DATASET_VERSION = "v1.1"
MOCK_RULE_VERSION = "performance-v1.1"

load = v1.load
sha = v1.sha
BASE = v1.BASE


def _v1_fault_projection(case: dict) -> dict:
    """把 v1.1 差异化故障场景投影回 v1 结构口径，供 v1 结构校验复用。

    v1.check_ticket 把故障目标硬绑为 logistics_agent，并要求 agent_timeout/breaker
    与 mode/status 一一对应；v1.1 的 B4 差异化（order_agent / llm / 自愈）在那套口径下
    必然冲突。这里只做结构校验投影，真实 v1.1 语义由 validate_fault_scenarios_v1_1 校验。
    """
    item = copy.deepcopy(case)
    scenario = (item.get("reference") or {}).get("fault_scenario")
    if not scenario:
        return item
    profile = (item.get("perf_tags") or {}).get("fault_profile")
    scenario["required_env"] = dict(scenario.get("required_env") or {},
                                    AGENT_FAULT_TARGET="logistics_agent",
                                    AGENT_FAULT_MODE="timeout" if profile == "agent_timeout" else "error",
                                    AGENT_DEGRADATION="true")
    scenario["expected_trace"] = {
        "degraded": True,
        "degraded_agent": "logistics_agent",
        "agent_status": "timeout" if profile == "agent_timeout" else "skipped_breaker",
    }
    return item


def validate_fault_scenarios_v1_1(cases: list[dict]) -> None:
    """核对四场景差异化本身：参数齐备、可区分、边界判据指向真实事实键。"""
    fault_cases = [row for row in cases
                   if (row.get("perf_tags") or {}).get("fault_profile") != "none"]
    if len(fault_cases) != 4:
        raise ValueError(f"v1.1 故障场景应恰为 4 条，实际 {len(fault_cases)}")
    seen = set()
    for row in fault_cases:
        case_id = row["case_id"]
        scenario = (row.get("reference") or {}).get("fault_scenario") or {}
        env = scenario.get("required_env") or {}
        trace = scenario.get("expected_trace") or {}
        boundary = scenario.get("judge_boundary") or {}
        for key in ("AGENT_FAULT_ENABLED", "AGENT_FAULT_TARGET", "AGENT_FAULT_MODE",
                    "AGENT_FAULT_LATENCY_MS", "AGENT_MAX_RETRIES",
                    "AGENT_BREAKER_THRESHOLD", "AGENT_DEGRADATION"):
            if key not in env:
                raise ValueError(f"{case_id}: 故障参数缺 {key}")
        if trace.get("degraded") is not True or not trace.get("degraded_agent"):
            raise ValueError(f"{case_id}: 降级预期不完整")
        if not scenario.get("expected_outcome") or not scenario.get("scenario_id"):
            raise ValueError(f"{case_id}: 缺 expected_outcome 或 scenario_id")
        signature = (env["AGENT_FAULT_TARGET"], env["AGENT_FAULT_MODE"],
                     env.get("AGENT_FAULT_LIMIT", "0"), env["AGENT_BREAKER_THRESHOLD"])
        if signature in seen:
            raise ValueError(f"{case_id}: 与既有故障场景参数重复 {signature}")
        seen.add(signature)
        verified = boundary.get("verified_facts")
        if not isinstance(verified, list) or not verified:
            raise ValueError(f"{case_id}: judge_boundary.verified_facts 缺省")
        missing = [key for key in verified if key not in (row.get("expected_facts") or {})]
        if missing:
            raise ValueError(f"{case_id}: 边界事实键不在 expected_facts：{missing}")
        if not boundary.get("unverified_phrases"):
            raise ValueError(f"{case_id}: judge_boundary.unverified_phrases 缺省")


def validate_cases_v1_1(cases: list[dict], orders: list[dict], logistics: list[dict],
                        kb: dict[str, list[dict]]) -> None:
    """按 v1.1 规则版本校验：v1 结构投影 + v1.1 故障场景语义。"""
    validate_fault_scenarios_v1_1(cases)
    original_rule = v1.RULE
    try:
        v1.RULE = RULE
        v1.validate_cases([_v1_fault_projection(row) for row in cases],
                          orders, logistics, kb)
    finally:
        v1.RULE = original_rule


def validate_source_manifest() -> dict:
    """源清单仍是 v1 身份（v1.1 派生自同一事实源），原样校验。"""
    return v1.validate_source_manifest()


def validate_generated(root: Path) -> dict:
    source_manifest = validate_source_manifest()
    manifest = load(root / "manifest.json")
    if manifest.get("schema_version") != SCHEMA:
        raise ValueError("数据包 schema_version 不匹配（应为 v1.1）")
    if (manifest.get("rule_version") != RULE
            or manifest.get("dataset_version") != DATASET_VERSION
            or manifest.get("mock_rule_version") != MOCK_RULE_VERSION):
        raise ValueError("数据档 v1.1 身份字段不完整")
    if (manifest.get("generator_sha256") != sha(HERE / "generate.py")
            or manifest.get("validator_sha256") != sha(HERE / "validate.py")
            or manifest.get("precheck_sha256") != sha(HERE / "precheck.py")):
        raise ValueError("生成器/校验器/预检版本与数据档不一致")
    extends = manifest.get("extends") or {}
    if (extends.get("source_dataset") != "performance-v1"
            or extends.get("source_cases_sha256") != sha(V1_DIR / "cases.json")
            or extends.get("source_generator_sha256") != sha(V1_DIR / "generate.py")):
        raise ValueError("v1.1 派生来源与 v1 事实源不一致")
    profile = manifest.get("profile_id")
    if profile not in v1.PROFILES:
        raise ValueError("数据档身份无效")
    if manifest.get("mix") not in ("balanced", "current_shape") or manifest.get("access") not in (
            "uniform", "hotspot_80_20", "broad"):
        raise ValueError("类别配比或访问模式无效")
    for rel, expected in manifest["source_hashes"].items():
        source = V1_DIR.parent / rel
        if not source.is_file() or sha(source) != expected:
            raise ValueError(f"生成来源已变化：{rel}")
    expected_sources = set(source_manifest["source_sha256"]) | {
        "performance-v1/source-manifest.json"}
    if set(manifest["source_hashes"]) != expected_sources:
        raise ValueError("生成来源文件清单不完整")
    expected_outputs = {"orders.json", "logistics.json", "reachability-report.json",
                        "knowledge-base/faq.json",
                        "knowledge-base/policies.json", "knowledge-base/products.json"}
    expected_outputs |= {f"{kind}-stage{stage}.json" for stage in (1, 2, 3)
                         for kind in ("golden", "fixed", "load")}
    # 故障窗专用档（W1 补窗）2026-11-03：从 load-stage2 筛选四场景各 15 条，
    # 供 P2-11 故障注入轮使用；由派生脚本生成并登记哈希。
    expected_outputs |= {"fault05-stage2.json", "fault06-stage2.json",
                         "fault07-stage2.json", "fault08-stage2.json"}
    # 故障窗补测变体档（W1B）2026-11-03：fault08 限流近似（llm 持续拒绝，429 语义），
    # 场景 llm-reject-approx；与 fault08 同源筛选，仅 fault_scenario 字段不同。
    expected_outputs |= {"fault08b-stage2.json"}
    if set(manifest["files_sha256"]) != expected_outputs:
        raise ValueError("生成产物文件清单不完整")
    for rel, expected in manifest["files_sha256"].items():
        path = root / rel
        if not path.is_file() or sha(path) != expected:
            raise ValueError(f"文件缺失或哈希不一致：{rel}")

    # 可达性预检报告：逐笔覆盖且零 unreachable
    reach = load(root / "reachability-report.json")
    authored_cases = load(V1_DIR / "cases.json")
    if reach.get("unreachable_total") != 0:
        raise ValueError("可达性预检存在不可达用例")
    for stage in (1, 2, 3):
        body = reach["stages"][str(stage)]
        expected_golden = 48 if stage == 1 else 56
        if body["golden_count"] != expected_golden or body["unreachable"] != 0:
            raise ValueError(f"阶段 {stage} 预检覆盖或不可达数不符")
        if body["category_counts"].get("static_pass", 0) + \
                body["category_counts"].get("retrieval_dependent", 0) + \
                body["category_counts"].get("behavior_only", 0) + \
                body["category_counts"].get("fault_window", 0) != expected_golden:
            raise ValueError(f"阶段 {stage} 预检类别计数不完整")
        if {entry["case_id"] for entry in body["cases"]} != {
                case["case_id"] for case in authored_cases
                if stage in case["applicable_stages"]}:
            raise ValueError(f"阶段 {stage} 预检用例集与黄金用例不一致")
    reach_summary = manifest.get("reachability") or {}
    for stage in (1, 2, 3):
        if reach_summary.get(str(stage), {}).get("unreachable") != 0:
            raise ValueError(f"清单可达性摘要与预检报告不符：阶段 {stage}")

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
    if v1.rag_index_version(kb) != manifest.get("expected_rag_index_version"):
        raise ValueError("知识索引预期指纹与文档不符")
    db_scale, kb_scale = v1.PROFILES[profile]
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
    docs = v1.knowledge_docs(kb)
    if len(docs) != manifest["counts"]["knowledge"]:
        raise ValueError("知识文档身份重复")

    all_ids = set()
    selected_orders: Counter = Counter()
    v1_cases = load(V1_DIR / "cases.json")
    for stage in (1, 2, 3):
        golden = load(root / f"golden-stage{stage}.json")
        load_pool = load(root / f"load-stage{stage}.json")
        fixed_pool = load(root / f"fixed-stage{stage}.json")
        expected_golden = 48 if stage == 1 else 56
        expected_v11 = [case for case in derive.derive_cases(v1_cases)
                        if stage in case["applicable_stages"]]
        if golden != expected_v11:
            raise ValueError(f"阶段 {stage} 黄金用例与 v1.1 派生规则不一致")
        if (len(golden) != expected_golden or len(load_pool) != manifest["pool_size"]
                or len(fixed_pool) != manifest["pool_size"]):
            raise ValueError(f"阶段 {stage} 的用例条数不符")
        for row in golden + load_pool + fixed_pool:
            if row["rule_version"] != RULE:
                raise ValueError(f"{row['ticket_id']}: 规则版本非 v1.1")
            if stage not in row["applicable_stages"]:
                raise ValueError(f"{row['ticket_id']}: 阶段不适用")
            with_v1_rule = _v1_fault_projection({**row, "rule_version": v1.RULE})
            v1.check_ticket(with_v1_rule, order_map, log_map, docs)
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
    v1_cases = load(V1_DIR / "cases.json")
    derived = derive.derive_cases(v1_cases)
    validate_cases_v1_1(derived, original_orders, original_logs, original_kb)
    if args.data_dir:
        result = validate_generated(args.data_dir)
        print(f"校验通过：{result['profile_id']} / {result['counts']} / "
              f"{result['mock_rule_version']}")
    else:
        print("64 条黄金用例（v1.1 规则派生）校验通过")


if __name__ == "__main__":
    main()
