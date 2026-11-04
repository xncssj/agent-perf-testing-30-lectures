"""生成 performance-v1.1 数据档：独立版本入口，物理构建逻辑复用 v1 生成器。

007 数据档战役整改 R03：
- schema/rule 升为 v1.1，与 v1.0 分库存放；v1.0 生成器、清单与校验入口不动；
- 生成前先跑静态可达性预检（precheck.py），存在不可达黄金用例即拒绝生成；
- 清单附加 dataset_version、mock_rule_version 与可达性摘要，
  并落盘 reachability-report.json 供逐笔复核。
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
V1_DIR = HERE.parent / "performance-v1"
if str(V1_DIR) not in sys.path:
    sys.path.insert(0, str(V1_DIR))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import derive  # noqa: E402
import generate as v1gen  # noqa: E402
import precheck  # noqa: E402

SCHEMA = "agent-performance-data-v1.1"
RULE = "agent-performance-rule-v1.1"
DATASET_VERSION = "v1.1"
MOCK_RULE_VERSION = "performance-v1.1"
SEED = v1gen.SEED
PROFILES = v1gen.PROFILES


def digest(path: Path) -> str:
    return v1gen.digest(path)


def derive_cases() -> list[dict]:
    """从 v1 用例单一事实源派生 v1.1 黄金用例（规则版本 + 故障场景差异化）。"""
    return derive.derive_cases(v1gen.read_json(V1_DIR / "cases.json"))


def generate(profile: str, mix: str, access: str, pool_size: int,
             seed: int, out: Path) -> dict:
    if pool_size < 1:
        raise ValueError("pool-size 必须为正整数")
    resolved = out.resolve()
    repo_root = next((parent for parent in HERE.parents if (parent / ".git").exists()), HERE)
    allowed_out = (HERE / "out").resolve()
    in_repo = resolved == repo_root or repo_root in resolved.parents
    in_allowed = resolved == allowed_out or allowed_out in resolved.parents
    if in_repo and not in_allowed:
        raise ValueError("输出目录不得覆盖共享基准或性能数据源目录")

    # 派生用例先于可达性预检：预检按 v1.1 故障场景分类，不能用源档口径冒算
    cases = derive_cases()

    # 生成前门禁：静态可达性预检发现不可达黄金用例即拒绝生成
    reachability = precheck.precheck_reachability(cases)
    if reachability["unreachable_total"]:
        raise ValueError(
            f"静态预检发现 {reachability['unreachable_total']} 条不可达黄金用例，"
            "拒绝生成 v1.1 数据档；先修复 Mock 规则或用例预期"
        )

    # 源自洽校验：源清单与用例结构必须有效（校验器入口见 validate.py）。
    # 用 importlib 显式加载本目录校验器，避免与 sys.path 中 v1 的 validate 同名冲突
    import importlib.util
    _spec = importlib.util.spec_from_file_location("dataset_validate_v1_1", HERE / "validate.py")
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    _module.validate_source_manifest()
    source_orders, source_logs, source_kb = v1gen.base_data()
    _module.validate_cases_v1_1(cases, source_orders, source_logs, source_kb)

    orders, logistics, knowledge = v1gen.build_physical(profile)
    out.mkdir(parents=True, exist_ok=True)
    v1gen.write_json(out / "orders.json", orders)
    v1gen.write_json(out / "logistics.json", logistics)
    for name, docs in knowledge.items():
        v1gen.write_json(out / "knowledge-base" / f"{name}.json", docs)

    logs_by_order = {row["order_id"]: row for row in logistics}
    picker = v1gen.KeyPicker(orders, logistics, access, random.Random(seed + 1))
    fixed_picker = v1gen.KeyPicker(source_orders, source_logs, "uniform",
                                   random.Random(seed + 2))
    source_logs_by_order = {row["order_id"]: row for row in source_logs}
    per_stage: dict[str, dict] = {}
    for stage in (1, 2, 3):
        subset = [row for row in cases if stage in row["applicable_stages"]]
        golden = [copy.deepcopy(row) for row in subset]
        rng = random.Random(seed + stage * 1000)
        selected = v1gen.choose_cases(subset, pool_size, mix, rng)
        load = []
        fixed = []
        for i, case in enumerate(selected, 1):
            data_class = case["perf_tags"]["data_class"]
            order = picker.pick(data_class) if data_class != "none" else None
            load.append(v1gen.fit_ticket(case, order, logs_by_order, stage * 1_000_000 + i))
            fixed_order = fixed_picker.pick(data_class) if data_class != "none" else None
            fixed.append(v1gen.fit_ticket(case, fixed_order, source_logs_by_order,
                                          stage * 1_000_000 + i, "FX"))
        v1gen.write_json(out / f"golden-stage{stage}.json", golden)
        v1gen.write_json(out / f"load-stage{stage}.json", load)
        v1gen.write_json(out / f"fixed-stage{stage}.json", fixed)
        per_stage[str(stage)] = {
            "golden": len(golden), "load": len(load), "fixed": len(fixed),
            "family_counts": dict(sorted(Counter(row["family"] for row in load).items())),
            "intent_counts": dict(sorted(Counter(row["expected_intent"] for row in load).items())),
        }

    reach_path = out / "reachability-report.json"
    v1gen.write_json(reach_path, reachability)
    files = [out / rel for rel in (
        "orders.json", "logistics.json", "knowledge-base/faq.json",
        "knowledge-base/policies.json", "knowledge-base/products.json",
        "reachability-report.json",
        *(f"{kind}-stage{stage}.json" for stage in (1, 2, 3)
          for kind in ("golden", "fixed", "load")),
    )]
    order_queries = sum(picker.selected.values())
    top_keys = sorted(picker.selected.items(), key=lambda item: (-item[1], item[0]))[:10]
    access_actual = {"selected_order_ids": len(picker.selected),
                     "order_queries": order_queries,
                     "hotset_draws": picker.hot_hits,
                     "hotset_fraction": round(picker.hot_hits / order_queries, 6)
                     if access == "hotspot_80_20" and order_queries else None,
                     "top10_order_id_counts": top_keys}
    manifest = {
        "schema_version": SCHEMA, "rule_version": RULE,
        "dataset_version": DATASET_VERSION, "mock_rule_version": MOCK_RULE_VERSION,
        "profile_id": profile,
        "generator_sha256": digest(HERE / "generate.py"),
        "validator_sha256": digest(HERE / "validate.py"),
        "precheck_sha256": digest(HERE / "precheck.py"),
        "db_scale": PROFILES[profile][0], "kb_scale": PROFILES[profile][1],
        "seed": seed, "mix": mix, "access": access, "pool_size": pool_size,
        "counts": {"orders": len(orders), "logistics": len(logistics),
                   "knowledge": sum(map(len, knowledge.values()))},
        "expected_rag_index_version": v1gen.rag_index_version(knowledge),
        "per_stage": per_stage,
        "access_actual": access_actual,
        "reachability": {stage: {"golden_count": body["golden_count"],
                                 "category_counts": body["category_counts"],
                                 "unreachable": body["unreachable"]}
                         for stage, body in reachability["stages"].items()},
        "extends": {"source_dataset": "performance-v1",
                    "source_cases_sha256": digest(V1_DIR / "cases.json"),
                    "source_generator_sha256": digest(V1_DIR / "generate.py")},
        "source_hashes": {path.relative_to(V1_DIR.parent).as_posix(): digest(path) for path in
                          [V1_DIR.parent / "tickets.json", V1_DIR.parent / "orders.json",
                           V1_DIR.parent / "logistics.json",
                           *sorted((V1_DIR.parent / "knowledge-base").glob("*.json")),
                           V1_DIR / "cases.json", V1_DIR / "source-manifest.json"]
                          if path.is_relative_to(V1_DIR.parent)},
        "files_sha256": {path.relative_to(out).as_posix(): digest(path) for path in files},
    }
    v1gen.write_json(out / "manifest.json", manifest)
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
                      "mock_rule_version": manifest["mock_rule_version"],
                      "reachability": manifest["reachability"],
                      "per_stage": {k: v["golden"] for k, v in manifest["per_stage"].items()}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
