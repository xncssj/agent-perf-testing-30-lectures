"""生成独立的 Agent 性能用例与铺底数据档（只写 --out 指定目录）。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
SCHEMA = "agent-performance-data-v1"
RULE = "agent-performance-rule-v1"
SEED = 42
PROFILES = {
    "reference": (1, 1),
    "db10": (10, 1),
    "kb10": (1, 10),
    "db100": (100, 1),
    "kb100": (1, 100),
    "combined10": (10, 10),
}
STATUS_ZH = {"pending": "待发货", "shipped": "已发货", "exception": "异常"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rag_index_version(knowledge: dict[str, list[dict]]) -> str:
    # 与 Workflow RagEngine._index_version 的 category:title + 正文口径相同。
    docs = [doc for rows in knowledge.values() for doc in rows]
    hashed = hashlib.sha256()
    for doc in sorted(docs, key=lambda item: f"{item['category']}:{item['title']}"):
        doc_id = f"{doc['category']}:{doc['title']}"
        hashed.update((doc_id + "\x00" + doc["content"]).encode("utf-8"))
    return hashed.hexdigest()[:8]


def base_data() -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    orders = read_json(BASE / "orders.json")
    logistics = read_json(BASE / "logistics.json")
    knowledge = {name: read_json(BASE / "knowledge-base" / f"{name}.json")
                 for name in ("faq", "policies", "products")}
    return orders, logistics, knowledge


def build_physical(profile: str) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    db_scale, kb_scale = PROFILES[profile]
    source_orders, source_logistics, source_kb = base_data()
    orders = copy.deepcopy(source_orders)
    logistics = copy.deepcopy(source_logistics)
    knowledge = copy.deepcopy(source_kb)
    target_orders = len(source_orders) * db_scale
    target_logistics = len(source_logistics) * db_scale
    product_items = [item for row in source_orders for item in row["items"]]

    # 保留旧记录与 ID；新增记录只用 8 开头的 13 位合成订单号。
    for seq in range(1, target_orders - len(source_orders) + 1):
        row = copy.deepcopy(source_orders[(seq - 1) % len(source_orders)])
        row["order_id"] = f"8{seq:012d}"
        row["user_id"] = f"PERF{seq:08d}"
        row["tracking_no"] = None
        row["carrier"] = None
        count = (1, 2, 4, 8)[(seq - 1) % 4]
        row["items"] = [copy.deepcopy(product_items[(seq + j) % len(product_items)])
                        for j in range(count)]
        row["total_amount"] = sum(int(item["price"]) * int(item["quantity"])
                                  for item in row["items"])
        orders.append(row)

    # 基准的 158/173 关系保持不变；新增物流只关联已发货订单。
    new_shipped = [row for row in orders[len(source_orders):] if row["status"] == "shipped"]
    needed = target_logistics - len(source_logistics)
    if len(new_shipped) < needed:
        raise ValueError("新增已发货订单不足以承载目标物流条数")
    for seq, order in enumerate(new_shipped[:needed], 1):
        row = copy.deepcopy(source_logistics[(seq - 1) % len(source_logistics)])
        tracking = f"SF{90_000_000 + seq:08d}"
        order["tracking_no"] = tracking
        order["carrier"] = row["carrier"]
        row["tracking_no"] = tracking
        row["order_id"] = order["order_id"]
        row["status"] = "异常件退回" if seq % 20 == 0 else "运输中"
        count = (1, 3, 8, 16)[(seq - 1) % 4]
        locations = [row["origin"]] + [f"合成中转站{j:02d}" for j in range(1, count)]
        row["trajectory"] = [
            {"time": f"202401{1 + j // 24:02d} {j % 24:02d}:00:00",
             "location": location, "status": "运输中"}
            for j, location in enumerate(locations)
        ]
        row["current_location"] = locations[-1]
        logistics.append(row)

    source_docs = [doc for name in ("faq", "policies", "products") for doc in source_kb[name]]
    target_docs = len(source_docs) * kb_scale
    for seq in range(1, target_docs - len(source_docs) + 1):
        source = source_docs[(seq - 1) % len(source_docs)]
        doc = copy.deepcopy(source)
        doc["id"] = 1_000_000 * ({"faq": 1, "policies": 2, "products": 3}[source["category"]]) + seq
        doc["title"] = f"合成性能文档{seq:05d}：{source['title']}"
        prefix = f"仅适用于合成商品 PERF-{seq:05d}。"
        length = (128, 512, 2048)[(seq - 1) % 3]
        doc["content"] = (prefix + source["content"] + " ") * (
            length // max(1, len(prefix + source["content"] + " ")) + 1)
        doc["content"] = doc["content"][:length]
        knowledge[source["category"]].append(doc)
    return orders, logistics, knowledge


def category(row: dict, logistics_by_order: dict[str, dict]) -> str:
    if not row.get("order_id"):
        return "none"
    if row["status"] == "pending":
        return "pending"
    log = logistics_by_order.get(row["order_id"])
    if log and log["status"] != "运输中":
        return "abnormal"
    return "shipped" if log else "order_only"


class KeyPicker:
    def __init__(self, orders: list[dict], logistics: list[dict], mode: str, rng: random.Random):
        by_order = {row["order_id"]: row for row in logistics}
        self.pools: dict[str, list[dict]] = {key: [] for key in
                                            ("pending", "abnormal", "shipped", "order_only")}
        for row in orders:
            self.pools[category(row, by_order)].append(row)
        self.mode, self.rng = mode, rng
        self.index: Counter = Counter()
        self.hot_hits = 0
        self.selected: Counter = Counter()

    def pick(self, data_class: str) -> dict:
        pool = self.pools[data_class]
        if not pool:
            raise ValueError(f"无可用的 {data_class} 订单")
        if self.mode == "broad":
            row = pool[self.index[data_class] % len(pool)]
            self.index[data_class] += 1
        elif self.mode == "hotspot_80_20":
            hot_size = max(1, len(pool) // 5)
            hot = self.rng.random() < 0.8
            candidates = pool[:hot_size] if hot else (pool[hot_size:] or pool)
            row = self.rng.choice(candidates)
            self.hot_hits += int(hot)
        else:
            row = self.rng.choice(pool)
        self.selected[row["order_id"]] += 1
        return row


def fit_ticket(case: dict, order: dict | None, logs: dict[str, dict],
               index: int, prefix: str = "LD") -> dict:
    ticket = copy.deepcopy(case)
    ticket["ticket_id"] = f"{prefix}{index:06d}"
    ticket["source_case_id"] = case["case_id"]
    if order is None:
        return ticket
    old_id, new_id = ticket["order_id"], order["order_id"]
    ticket["order_id"] = new_id
    ticket["user_id"] = order["user_id"]
    ticket["user_query"] = ticket["user_query"].replace(old_id, new_id)
    facts = ticket.get("expected_facts", {})
    if "order_id" in facts:
        facts["order_id"] = new_id
    if "status" in facts:
        facts["status"] = STATUS_ZH[order["status"]]
    if "total_amount" in facts:
        facts["total_amount"] = str(order["total_amount"])
    old_track = case.get("reference", {}).get("tracking_no")
    log = logs.get(new_id)
    if old_track and log:
        ticket["user_query"] = ticket["user_query"].replace(old_track, log["tracking_no"])
    if log:
        for key in ("current_location", "estimated_delivery"):
            if key in facts:
                facts[key] = log[key]
    ticket["reference"] = dict(ticket["reference"], order_id=new_id,
                                basis=f"所选数据档/orders.json#{new_id}",
                                tracking_no=log["tracking_no"] if log else None)
    ticket["reference"]["reference_answer"] = (
        "；".join([*ticket.get("expected_answer_keywords", []),
                  *(f"{key}={value}" for key, value in facts.items())]) + "。"
        if facts else ticket["reference"]["reference_answer"]
    )
    return ticket


def choose_cases(cases: list[dict], pool_size: int, mix: str,
                 rng: random.Random) -> list[dict]:
    if mix == "balanced":
        families = sorted({row["family"] for row in cases})
        by_family = {family: [row for row in cases if row["family"] == family]
                     for family in families}
        return [rng.choice(by_family[families[i % len(families)]]) for i in range(pool_size)]
    baseline = read_json(BASE / "tickets.json")
    intent_counts = Counter(row["expected_intent"] for row in baseline)
    by_intent = {intent: [row for row in cases if row["expected_intent"] == intent]
                 for intent in intent_counts}
    missing = sorted(intent for intent in intent_counts if not by_intent[intent])
    if missing:
        raise ValueError(f"当前构成缺少意图用例：{missing}")
    total = sum(intent_counts.values())
    quota = {intent: pool_size * count // total for intent, count in intent_counts.items()}
    remainder = pool_size - sum(quota.values())
    ranked = sorted(intent_counts, key=lambda intent:
                    (-(pool_size * intent_counts[intent] % total), intent))
    for intent in ranked[:remainder]:
        quota[intent] += 1
    selected = [rng.choice(by_intent[intent]) for intent in sorted(quota)
                for _ in range(quota[intent])]
    rng.shuffle(selected)
    return selected


def generate(profile: str, mix: str, access: str, pool_size: int,
             seed: int, out: Path) -> dict:
    if pool_size < 1:
        raise ValueError("pool-size 必须为正整数")
    resolved = out.resolve()
    allowed_in_repo = (HERE / "out").resolve()
    repo_root = next((parent for parent in HERE.parents if (parent / ".git").exists()), BASE)
    in_repo = resolved == repo_root or repo_root in resolved.parents
    in_allowed_out = resolved == allowed_in_repo or allowed_in_repo in resolved.parents
    if in_repo and not in_allowed_out:
        # 仓库内只允许写忽略的 out/；仓库外自定目录也可使用。
        raise ValueError("输出目录不得覆盖共享基准或性能数据源目录")
    from validate import validate_cases, validate_source_manifest  # 脚本目录在 sys.path 中
    validate_source_manifest()
    cases = read_json(HERE / "cases.json")
    source_orders, source_logs, source_kb = base_data()
    validate_cases(cases, source_orders, source_logs, source_kb)
    orders, logistics, knowledge = build_physical(profile)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "orders.json", orders)
    write_json(out / "logistics.json", logistics)
    for name, docs in knowledge.items():
        write_json(out / "knowledge-base" / f"{name}.json", docs)

    logs_by_order = {row["order_id"]: row for row in logistics}
    picker = KeyPicker(orders, logistics, access, random.Random(seed + 1))
    fixed_picker = KeyPicker(source_orders, source_logs, "uniform", random.Random(seed + 2))
    source_logs_by_order = {row["order_id"]: row for row in source_logs}
    per_stage: dict[str, dict] = {}
    for stage in (1, 2, 3):
        subset = [row for row in cases if stage in row["applicable_stages"]]
        golden = [copy.deepcopy(row) for row in subset]
        rng = random.Random(seed + stage * 1000)
        selected = choose_cases(subset, pool_size, mix, rng)
        load = []
        fixed = []
        for i, case in enumerate(selected, 1):
            data_class = case["perf_tags"]["data_class"]
            order = picker.pick(data_class) if data_class != "none" else None
            load.append(fit_ticket(case, order, logs_by_order, stage * 1_000_000 + i))
            fixed_order = fixed_picker.pick(data_class) if data_class != "none" else None
            fixed.append(fit_ticket(case, fixed_order, source_logs_by_order,
                                    stage * 1_000_000 + i, "FX"))
        write_json(out / f"golden-stage{stage}.json", golden)
        write_json(out / f"load-stage{stage}.json", load)
        write_json(out / f"fixed-stage{stage}.json", fixed)
        per_stage[str(stage)] = {
            "golden": len(golden), "load": len(load), "fixed": len(fixed),
            "family_counts": dict(sorted(Counter(row["family"] for row in load).items())),
            "intent_counts": dict(sorted(Counter(row["expected_intent"] for row in load).items())),
        }

    files = [out / rel for rel in (
        "orders.json", "logistics.json", "knowledge-base/faq.json",
        "knowledge-base/policies.json", "knowledge-base/products.json",
        *(f"{kind}-stage{stage}.json" for stage in (1, 2, 3)
          for kind in ("golden", "fixed", "load")),
    )]
    order_queries = sum(picker.selected.values())
    top_keys = sorted(picker.selected.items(), key=lambda item: (-item[1], item[0]))[:10]
    manifest = {
        "schema_version": SCHEMA, "rule_version": RULE, "profile_id": profile,
        "generator_sha256": digest(HERE / "generate.py"),
        "validator_sha256": digest(HERE / "validate.py"),
        "db_scale": PROFILES[profile][0], "kb_scale": PROFILES[profile][1],
        "seed": seed, "mix": mix, "access": access, "pool_size": pool_size,
        "counts": {"orders": len(orders), "logistics": len(logistics),
                   "knowledge": sum(map(len, knowledge.values()))},
        "expected_rag_index_version": rag_index_version(knowledge),
        "per_stage": per_stage,
        "access_actual": {"selected_order_ids": len(picker.selected),
                          "order_queries": order_queries,
                          "hotset_draws": picker.hot_hits,
                          "hotset_fraction": round(picker.hot_hits / order_queries, 6)
                          if access == "hotspot_80_20" and order_queries else None,
                          "top10_order_id_counts": top_keys},
        "source_hashes": {str(path.relative_to(BASE)): digest(path) for path in
                          [BASE / "tickets.json", BASE / "orders.json", BASE / "logistics.json",
                           *sorted((BASE / "knowledge-base").glob("*.json")),
                           HERE / "cases.json", HERE / "source-manifest.json"]
                          if path.is_relative_to(BASE)},
        "files_sha256": {str(path.relative_to(out)): digest(path) for path in files},
    }
    write_json(out / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="reference")
    parser.add_argument("--mix", choices=("balanced", "current_shape"), default="balanced")
    parser.add_argument("--access", choices=("uniform", "hotspot_80_20", "broad"), default="uniform")
    parser.add_argument("--pool-size", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = generate(args.profile, args.mix, args.access, args.pool_size,
                        args.seed, args.out)
    print(json.dumps({"profile": manifest["profile_id"], "counts": manifest["counts"],
                      "per_stage": {k: v["golden"] for k, v in manifest["per_stage"].items()}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
