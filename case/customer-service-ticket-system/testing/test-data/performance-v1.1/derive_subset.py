"""派生 performance-v1.1 公共子集数据档（三阶段配对 b0 用，只读父档）。

用途：从 ``reference-balanced-uniform`` 档的 ``load-stageN.json`` 派生一批三阶段
共有的请求子集，供 P1-03 / P2-14 / P3-13 公共子集 b0 配对使用：

- ``subset-stageN.json`` 只保存选中工单数组，不改父档、不内嵌自身哈希；
- ``subset-stageN.manifest.json`` 记录父档指纹、选中 ticket_id 与顺序、条数、
  子集文件 SHA-256（对子集原始字节计算，清单自身不参与），供 run-isolated.sh
  开跑前核对；
- 缺省按三阶段 load 档共有的 ``user_query`` 选样，保证同一批请求跨阶段逐字节
  相同；``--ids`` 可指定 ticket_id / case_id / user_query 清单。

用法（任意目录执行）：
    python -X utf8 testing/test-data/performance-v1.1/derive_subset.py --stage 2
    python -X utf8 ...derive_subset.py --stage 3 --samples 50 --out-dir <仓库内目录>
    python -X utf8 ...derive_subset.py --stage 1 --ids subset_ids.txt

硬约束：父档只读、不重新生成数据档、输出路径限仓库内、默认不覆盖已有产物
（已存在报错，``--force`` 才覆盖）。父档须先通过 validate.py / precheck.py 校验。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = HERE / "out" / "reference-balanced-uniform"
STAGES = (1, 2, 3)
SUBSET_SCHEMA = "agent-performance-subset-v1"
DEFAULT_SEED = 42
DEFAULT_SAMPLES = 50
# 派生输出只允许这两个文件名，避免误覆盖数据档与生成器。
LOAD_FILE_TMPL = "load-stage{stage}.json"
SUBSET_FILE_TMPL = "subset-stage{stage}.json"
SUBSET_MANIFEST_TMPL = "subset-stage{stage}.manifest.json"


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    """按脚本位置向上找 .git，避免依赖作者机器目录。"""
    for parent in (HERE, *HERE.parents):
        if (parent / ".git").exists():
            return parent
    return HERE


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    """文件原始字节 SHA-256（与 v1.1 generate.py 的 digest 同口径）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rel_repo(path: Path) -> str:
    """仓库内相对路径（posix 分隔符），便于跨机器核对。"""
    root = repo_root()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())


def ensure_in_repo(path: Path, what: str) -> Path:
    """输出路径必须落在仓库内，防止把数据档写到仓库外或被误引。"""
    root = repo_root()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise SystemExit(f"{what}越出仓库：{resolved}（仓库根 {root}）")
    return resolved


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, value) -> bytes:
    """与 v1.1 generate.py 同格式落盘（UTF-8、中文不转义、缩进 2）。

    显式按字节写入：避免 Windows 文本模式把 \\n 转成 \\r\\n，保证跨平台
    字节级可复现（子集 SHA-256 直接依赖字节）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return data


# ---------------------------------------------------------------------------
# 父档读取与校验
# ---------------------------------------------------------------------------

def load_parent(profile_dir: Path, stage: int):
    """读取父档 load-stageN.json 并做结构校验，返回（工单行, 路径）。"""
    parent_path = profile_dir / LOAD_FILE_TMPL.format(stage=stage)
    if not parent_path.is_file():
        raise SystemExit(f"父档缺失：{parent_path}（先跑 generate.py 生成 v1.1 数据档）")
    rows = load_json(parent_path)
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"父档不是非空工单数组：{parent_path}")
    required = ("ticket_id", "user_query", "expected_intent", "expected_tools",
                "expected_path", "expected_branch", "applicable_stages")
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SystemExit(f"父档第 {index} 行不是对象：{parent_path}")
        missing = [key for key in required
                   if key not in row or row[key] in (None, "")]
        if missing:
            raise SystemExit(f"父档第 {index} 行缺字段 {missing}：{parent_path}")
        ticket_id = row["ticket_id"]
        if ticket_id in seen:
            raise SystemExit(f"父档 ticket_id 重复：{ticket_id}：{parent_path}")
        seen.add(ticket_id)
        if stage not in row["applicable_stages"]:
            raise SystemExit(
                f"父档 {parent_path.name} 含阶段不适用工单 {ticket_id}"
                f"（applicable_stages={row['applicable_stages']}）")
    return rows, parent_path


def load_parent_manifest(profile_dir: Path, parent_path: Path) -> tuple[dict, Path, str]:
    """读取父档目录清单，核对父档登记指纹；返回（清单, 清单路径, 清单 SHA-256）。"""
    manifest_path = profile_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"父档目录清单缺失：{manifest_path}")
    manifest = load_json(manifest_path)
    manifest_sha = digest(manifest_path)
    registered = (manifest.get("files_sha256") or {}).get(parent_path.name)
    if not registered:
        raise SystemExit(
            f"父档目录清单未登记 {parent_path.name}，拒绝派生"
            "（先跑 validate.py 确认数据档有效）")
    actual = digest(parent_path)
    if registered != actual:
        raise SystemExit(
            f"父档与登记指纹不一致：{parent_path.name} 实际 {actual} != 清单 {registered}"
            "（父档已变化，先跑 validate.py）")
    return manifest, manifest_path, manifest_sha


# ---------------------------------------------------------------------------
# 选样：三阶段 query 交集 + 种子 / ids 清单
# ---------------------------------------------------------------------------

def _case_id(row: dict) -> str:
    return str(row.get("source_case_id") or row.get("case_id") or "")


def build_candidates(profile_dir: Path, parent_rows: list[dict], stage: int):
    """构造候选 user_query；缺省取三阶段 load 档交集，保证跨阶段同一批请求。

    返回（候选 query 列表, 选样方法, 三阶段文件存在性, query 的稳定 family 映射）。
    稳定 family 取三阶段该 query 出现过的 family 集合中字典序最小者，只依赖三份
    父档内容、与"本次从哪个阶段派生"无关，保证三阶段选出同一批 query。
    """
    parent_by_query: dict[str, dict] = {}
    for row in parent_rows:
        parent_by_query.setdefault(row["user_query"], row)
    stage_rows: dict[int, list[dict]] = {}
    stage_present: dict[int, bool] = {}
    stage_queries: dict[int, set[str]] = {}
    for other in STAGES:
        path = profile_dir / LOAD_FILE_TMPL.format(stage=other)
        stage_present[other] = path.is_file()
        if not path.is_file():
            stage_rows[other] = []
            stage_queries[other] = set()
            continue
        rows = load_json(path)
        stage_rows[other] = rows
        stage_queries[other] = {row["user_query"] for row in rows}
    if all(stage_present.values()):
        common = set.intersection(*(stage_queries[other] for other in STAGES))
        method = "three_stage_query_intersection"
    else:
        common = set(stage_queries[stage])
        method = "single_stage_parent_only"
    common &= set(parent_by_query)
    # query 的稳定 family：扫描全部可用阶段，取 family 集合字典序最小者
    family_sets: dict[str, set[str]] = {}
    for other in STAGES:
        if not stage_present[other]:
            continue
        for row in stage_rows[other]:
            query = row["user_query"]
            if query in common:
                family_sets.setdefault(query, set()).add(
                    str(row.get("family") or "(无 family)"))
    family_by_query = {query: sorted(families)[0]
                       for query, families in family_sets.items()}
    # 只按 user_query 排序：该键三阶段一致，保证同 seed 在三阶段选出同一批请求。
    ordered = sorted(common)
    return ordered, method, stage_present, family_by_query


def select_by_seed(ordered: list[str], family_by_query: dict[str, str],
                   samples: int, seed: int) -> list[str]:
    """按稳定 family 轮转选样：先保证每个 family 至少若干条，再逐轮补齐。

    三阶段共有 query 的数量分布不均（订单池小的 data_class 更容易跨阶段重合），
    纯随机抽会向个别 family 倾斜。按 family 轮转可让 50 条覆盖全部 8 个 family，
    且 family 归属只由三份父档共同决定，三阶段选出的 query 集合完全一致。
    """
    if samples > len(ordered):
        raise SystemExit(
            f"候选只有 {len(ordered)} 条共有 user_query，不足 --samples={samples}"
            "（可减小条数或改用 --ids 指定）")
    groups: dict[str, list[str]] = {}
    for query in ordered:
        groups.setdefault(family_by_query.get(query, "(无 family)"), []).append(query)
    rng = random.Random(seed)
    families = sorted(groups)
    rng.shuffle(families)
    for family in families:
        rng.shuffle(groups[family])
    chosen: list[str] = []
    round_no = 0
    longest = max(len(queries) for queries in groups.values())
    while len(chosen) < samples and round_no < longest:
        for family in families:
            if len(chosen) >= samples:
                break
            queries = groups[family]
            if round_no < len(queries):
                chosen.append(queries[round_no])
        round_no += 1
    # 跨阶段一致的规范顺序：先按稳定 family，再按 user_query
    return sorted(chosen, key=lambda query: (
        family_by_query.get(query, "(无 family)"), query))


def selection_stats(selected: list[dict],
                    family_by_query: dict[str, str]) -> dict:
    """选样覆盖统计：保证清单里留下可复核的分布证据。"""
    cases = [_case_id(row) or "(无 case_id)" for row in selected]
    families = Counter(family_by_query.get(row["user_query"], "(无 family)")
                       for row in selected)
    return {"case_count": len(set(cases)),
            "case_coverage": [case for case in sorted(set(cases))],
            "stable_family_counts": dict(sorted(families.items()))}


def select_by_ids(ids_path: Path, parent_rows: list[dict]) -> tuple[list[dict], dict]:
    """按 ids 清单顺序解析选中工单；支持 ticket_id / case_id / user_query。"""
    if not ids_path.is_file():
        raise SystemExit(f"--ids 文件不存在：{ids_path}")
    tickets = {row["ticket_id"]: index for index, row in enumerate(parent_rows)}
    queries = {row["user_query"]: index for index, row in enumerate(parent_rows)}
    cases: dict[str, list[int]] = {}
    for index, row in enumerate(parent_rows):
        case_id = _case_id(row)
        if case_id:
            cases.setdefault(case_id, []).append(index)
    raw_lines = ids_path.read_text(encoding="utf-8-sig").splitlines()
    selected: list[dict] = []
    used: set[str] = set()
    tokens: list[str] = []
    unresolved: list[str] = []
    for line_no, raw in enumerate(raw_lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        token = raw.strip()
        index = None
        if token in tickets:
            index = tickets[token]
        elif raw in queries:
            index = queries[raw]
        elif token in queries:
            index = queries[token]
        elif token in cases:
            index = cases[token][0]
        if index is None:
            unresolved.append(f"第 {line_no} 行：{token[:80]}")
            continue
        row = parent_rows[index]
        if row["ticket_id"] in used:
            raise SystemExit(f"--ids 第 {line_no} 行解析到重复工单 {row['ticket_id']}")
        used.add(row["ticket_id"])
        tokens.append(token)
        selected.append(row)
    if unresolved:
        raise SystemExit("--ids 有无法解析的键：\n" + "\n".join(unresolved))
    if not selected:
        raise SystemExit(f"--ids 文件没有有效选样：{ids_path}")
    return selected, {"ids_file": rel_repo(ids_path), "ids_file_sha256": digest(ids_path),
                      "ids_lines": len(selected)}


# ---------------------------------------------------------------------------
# 选样校验与清单构建
# ---------------------------------------------------------------------------

def check_selected(selected: list[dict], stage: int, rule_version: str,
                   common_queries: set[str]) -> None:
    """逐条核对选中工单仍是父档合法行，不改字段、不越阶段。"""
    seen: set[str] = set()
    for row in selected:
        ticket_id = row["ticket_id"]
        if ticket_id in seen:
            raise SystemExit(f"选中工单重复：{ticket_id}")
        seen.add(ticket_id)
        if stage not in row["applicable_stages"]:
            raise SystemExit(f"选中工单阶段不适用：{ticket_id}")
        if rule_version and row.get("rule_version") not in (None, rule_version):
            raise SystemExit(
                f"选中工单规则版本不符：{ticket_id} {row.get('rule_version')} != {rule_version}")
        if row["user_query"] not in common_queries:
            raise SystemExit(f"选中工单不在三阶段共有请求内：{ticket_id}")


def build_manifest(*, stage: int, profile_dir: Path, out_dir: Path,
                   parent_path: Path,
                   parent_rows: list[dict], parent_manifest: dict,
                   parent_manifest_path: Path, parent_manifest_sha: str,
                   parent_sha: str, selected: list[dict], method: str,
                   stage_present: dict[int, bool], common_count: int,
                   selection: dict, family_by_query: dict[str, str],
                   subset_file: str, subset_sha: str,
                   subset_bytes: int) -> dict:
    """按 A4 校验契约组装派生清单；主键与 run-isolated.sh 对齐。"""
    identity = {
        "dataset_version": parent_manifest.get("dataset_version"),
        "mock_rule_version": parent_manifest.get("mock_rule_version"),
        "schema_version": parent_manifest.get("schema_version"),
        "rule_version": parent_manifest.get("rule_version"),
    }
    queries = [row["user_query"] for row in selected]
    parent_positions = {row["ticket_id"]: index for index, row in enumerate(parent_rows)}
    entries = []
    for row in selected:
        perf_tags = row.get("perf_tags") or {}
        entries.append({
            "ticket_id": row["ticket_id"],
            "parent_index": parent_positions[row["ticket_id"]],
            "case_id": _case_id(row),
            "family": row.get("family"),
            "path_class": perf_tags.get("path_class"),
            "user_query_sha256": digest_text(row["user_query"])[:16],
        })
    family_counts = Counter(row.get("family") or "(无 family)" for row in selected)
    intent_counts = Counter(row["expected_intent"] for row in selected)
    path_counts = Counter((row.get("perf_tags") or {}).get("path_class") or "(无)"
                          for row in selected)
    same_dir = out_dir.resolve() == profile_dir.resolve()
    notes = [
        "子集文件只存选中工单数组，父档保持原样并独立通过 validate.py / precheck.py。",
        "清单自身不参与任何哈希；subset_sha256 对子集文件原始字节计算。",
        "缺省选样键为三阶段 load 档共有 user_query，三阶段同 seed 产出同一批请求。",
        "ticket_ids 顺序 = 子集数组顺序 = entries 的 parent_index 顺序。",
        "同一 query 在不同阶段可能由不同 source_case_id 生成（生成器按阶段独立抽样），"
        "故各阶段 case_count 可能不同；跨阶段同一批以 batch_sha256 为准。",
    ]
    if not same_dir:
        notes.append(
            "输出目录与父档目录不同：run-isolated.sh 要求 subset 与父档 manifest.json "
            "同目录，装载前请确认。")
    return {
        # ---- A4 run-isolated.sh 必填主键（键名不得随意改） ----
        "schema_version": SUBSET_SCHEMA,
        "stage": stage,
        "parent_profile_id": profile_dir.name,
        "parent_manifest_sha256": parent_manifest_sha,
        "parent_manifest_path": parent_manifest_path.name,
        "ticket_ids": [row["ticket_id"] for row in selected],
        "count": len(selected),
        "subset_file": subset_file,
        "subset_sha256": subset_sha,
        "generated_at": utc_now(),
        # ---- 追溯附加键（不参与 A4 必填校验） ----
        "dataset_version": identity["dataset_version"],
        "mock_rule_version": identity["mock_rule_version"],
        "parent_dataset_profile_id": parent_manifest.get("profile_id"),
        "parent_manifest_rel_path": rel_repo(parent_manifest_path),
        "parent_tickets_file": parent_path.name,
        "parent_tickets_rel_path": rel_repo(parent_path),
        "parent_tickets_sha256": parent_sha,
        "parent_tickets_registered_sha256": parent_manifest["files_sha256"][parent_path.name],
        "subset_rel_path": rel_repo(out_dir / subset_file),
        "same_dir_as_parent": same_dir,
        "subset_bytes": subset_bytes,
        "stage_files_present": {str(key): value for key, value in stage_present.items()},
        "selection": {**selection, "candidates": common_count,
                      "canonical_order": "stable_family,user_query",
                      **selection_stats(selected, family_by_query),
                      "batch_sha256": digest_text("\n".join(queries))},
        "entries": entries,
        "case_ids": [entry["case_id"] for entry in entries],
        "family_counts": dict(sorted(family_counts.items())),
        "intent_counts": dict(sorted(intent_counts.items())),
        "path_class_counts": dict(sorted(path_counts.items())),
        "generator_sha256": digest(HERE / "derive_subset.py"),
        "notes": notes,
    }


def verify_outputs(subset_path: Path, manifest_path: Path,
                   parent_path: Path, parent_sha: str,
                   parent_manifest_path: Path, parent_manifest_sha: str) -> None:
    """落盘后自检：哈希、条数、顺序、纯子集、父档未变。"""
    rows = load_json(subset_path)
    manifest = load_json(manifest_path)
    problems: list[str] = []
    if manifest.get("schema_version") != SUBSET_SCHEMA:
        problems.append("清单 schema_version 不符")
    if manifest.get("subset_file") != subset_path.name:
        problems.append("清单 subset_file 与文件名不符")
    if manifest.get("count") != len(rows):
        problems.append(f"条数不符：清单 {manifest.get('count')} != 文件 {len(rows)}")
    ticket_ids = [row.get("ticket_id") for row in rows]
    if manifest.get("ticket_ids") != ticket_ids:
        problems.append("清单 ticket_ids 与子集数组顺序不符")
    if digest(subset_path) != manifest.get("subset_sha256"):
        problems.append("子集 SHA-256 与清单不符")
    if digest(parent_path) != parent_sha:
        problems.append("父档在派生过程中被改动")
    if digest(parent_manifest_path) != parent_manifest_sha:
        problems.append("父档目录清单在派生过程中被改动")
    parent_rows = load_json(parent_path)
    parent_index = {row["ticket_id"]: row for row in parent_rows}
    for row in rows:
        original = parent_index.get(row.get("ticket_id"))
        if original is None:
            problems.append(f"子集工单不在父档：{row.get('ticket_id')}")
        elif original != row:
            problems.append(f"子集工单与父档不一致（疑似被改写）：{row.get('ticket_id')}")
    if problems:
        raise SystemExit("派生后自检失败：\n- " + "\n- ".join(problems))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", type=int, choices=STAGES, required=True,
                        help="阶段 1/2/3；从同阶段 load-stageN.json 派生")
    parser.add_argument("--samples", type=int, default=None,
                        help=f"子集条数；缺省 {DEFAULT_SAMPLES}（--ids 时缺省取 ids 条数）")
    parser.add_argument("--profile-dir", type=Path, default=None,
                        help="父档目录（缺省 out/reference-balanced-uniform）")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="输出目录（缺省=父档目录；须在仓库内）")
    parser.add_argument("--ids", type=Path, default=None,
                        help="选样清单：每行一个 ticket_id / case_id / user_query，"
                             "# 开头为注释")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"缺省选样种子（缺省 {DEFAULT_SEED}；同 seed 同档可复现同一批）")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已存在的 subset-stageN.json / .manifest.json")
    args = parser.parse_args()

    profile_dir = (args.profile_dir or DEFAULT_PROFILE_DIR).resolve()
    if not profile_dir.is_dir():
        raise SystemExit(f"父档目录不存在：{profile_dir}")
    stage = args.stage
    parent_rows, parent_path = load_parent(profile_dir, stage)
    parent_manifest, parent_manifest_path, parent_manifest_sha = load_parent_manifest(
        profile_dir, parent_path)
    parent_sha = digest(parent_path)
    rule_version = str(parent_manifest.get("rule_version") or "")

    ordered, method, stage_present, family_by_query = build_candidates(
        profile_dir, parent_rows, stage)
    common_queries = set(ordered)
    parent_by_query = {row["user_query"]: row for row in parent_rows}
    selection: dict = {"key": "user_query", "method": method, "seed": None,
                       "strategy": None,
                       "ids_file": None, "ids_file_sha256": None, "ids_lines": None}
    if args.ids:
        selected, ids_meta = select_by_ids(args.ids.resolve(), parent_rows)
        selection.update({"method": "ids_file", "strategy": "explicit_ids", **ids_meta})
        if args.samples is not None and args.samples != len(selected):
            raise SystemExit(
                f"--samples={args.samples} 与 --ids 解析出的 {len(selected)} 条不一致")
        samples = len(selected)
    else:
        samples = args.samples if args.samples is not None else DEFAULT_SAMPLES
        if samples < 1:
            raise SystemExit("--samples 必须为正整数")
        chosen_queries = select_by_seed(ordered, family_by_query, samples, args.seed)
        selected = [parent_by_query[query] for query in chosen_queries]
        selection.update({"method": method, "strategy": "family_round_robin",
                          "seed": args.seed})
    check_selected(selected, stage, rule_version, common_queries)

    out_dir = (args.out_dir or profile_dir)
    out_dir = ensure_in_repo(out_dir, "输出目录")
    subset_name = SUBSET_FILE_TMPL.format(stage=stage)
    manifest_name = SUBSET_MANIFEST_TMPL.format(stage=stage)
    subset_path = out_dir / subset_name
    manifest_path = out_dir / manifest_name
    if subset_path.resolve() == parent_path.resolve():
        raise SystemExit("派生输出不得与父档同名")
    existing = [str(path) for path in (subset_path, manifest_path) if path.exists()]
    if existing and not args.force:
        raise SystemExit("输出已存在，未覆盖（--force 才覆盖）：\n- " + "\n- ".join(existing))

    subset_bytes = write_json(subset_path, selected)
    subset_sha = hashlib.sha256(subset_bytes).hexdigest()
    manifest = build_manifest(
        stage=stage, profile_dir=profile_dir, out_dir=out_dir,
        parent_path=parent_path,
        parent_rows=parent_rows, parent_manifest=parent_manifest,
        parent_manifest_path=parent_manifest_path,
        parent_manifest_sha=parent_manifest_sha, parent_sha=parent_sha,
        selected=selected, method=method, stage_present=stage_present,
        common_count=len(ordered), selection=selection,
        family_by_query=family_by_query,
        subset_file=subset_name, subset_sha=subset_sha,
        subset_bytes=len(subset_bytes))
    write_json(manifest_path, manifest)
    verify_outputs(subset_path, manifest_path, parent_path, parent_sha,
                   parent_manifest_path, parent_manifest_sha)

    summary = {
        "stage": stage,
        "count": manifest["count"],
        "selection_method": selection["method"],
        "seed": selection.get("seed"),
        "candidates": len(ordered),
        "subset_file": subset_name,
        "subset_sha256": subset_sha,
        "manifest_file": manifest_name,
        "parent_tickets_file": parent_path.name,
        "parent_tickets_sha256": parent_sha,
        "parent_manifest_sha256": parent_manifest_sha,
        "batch_sha256": manifest["selection"]["batch_sha256"],
        "family_counts": manifest["family_counts"],
        "case_count": manifest["selection"]["case_count"],
        "same_dir_as_parent": manifest["same_dir_as_parent"],
        "out_dir": rel_repo(out_dir),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"派生完成：{out_dir / subset_name}（{manifest['count']} 条）"
          f"，清单：{manifest_path}")


if __name__ == "__main__":
    main()
