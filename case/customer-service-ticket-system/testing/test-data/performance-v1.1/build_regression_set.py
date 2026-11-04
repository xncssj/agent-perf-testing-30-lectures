"""构建分支冻结回归集（P3-16 / B15）：≥90 条、知识/澄清/转人工逐类 ≥30。

合并 ``branch_sample_filter.py`` 的筛选结果（原集 245 + v1.1 load 档 + 经核验的
构造样本），冻结为回归锚并输出：

- ``regression_set.json``：冻结样本（原字段 + branch_class/constructed/source/
  verification/sample_id 标注，构造样本单独标注）；
- ``regression_set.sha256``：sha256sum 兼容的侧车哈希；
- ``regression_set.manifest.json``：登记清单（文件 SHA-256、条数、逐类配额表、
  覆盖校验结果、输入指纹）；
- ``coverage-report.md``：人工可读的覆盖校验表。

默认不覆盖已有产物（``--force`` 才覆盖），输出路径限仓库内。

用法（任意目录执行）：
    python -X utf8 testing/test-data/performance-v1.1/build_regression_set.py
    python -X utf8 ...build_regression_set.py --min-total 90 --min-per-class 30
    python -X utf8 ...build_regression_set.py --candidates <branch_samples.json>
    python -X utf8 ...build_regression_set.py --verify <regression_set.json>
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
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import branch_sample_filter as bsf  # noqa: E402

SCHEMA = "branch-regression-set-v1"
MANIFEST_SCHEMA = "branch-regression-set-manifest-v1"
DEFAULT_OUT_DIR = HERE / "out" / "regression-set"
MIN_TOTAL = 90
MIN_PER_CLASS = 30
SET_FILE = "regression_set.json"
SHA_FILE = "regression_set.sha256"
MANIFEST_FILE = "regression_set.manifest.json"
COVERAGE_FILE = "coverage-report.md"


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


def write_text(path: Path, text: str) -> bytes:
    """按字节写 UTF-8：避免 Windows 文本模式把 \\n 转成 \\r\\n，保证哈希可复现。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    return data


def write_json(path: Path, value) -> bytes:
    return write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def display_width(text: str) -> int:
    """按终端显示宽度计（CJK 字符占 2 列），用于对齐中文计数表。"""
    return sum(2 if ord(char) >= 0x2E80 else 1 for char in text)


def pad(text: str, width: int, align: str = "left") -> str:
    space = max(0, width - display_width(text))
    return (" " * space + text) if align == "right" else (text + " " * space)


# ---------------------------------------------------------------------------
# 样本校验与冻结
# ---------------------------------------------------------------------------

def validate_samples(samples: list[dict]) -> None:
    """冻结前逐条复核：字段、标注、静态核验、唯一性、构造样本标注。"""
    if not samples:
        raise SystemExit("没有可冻结的样本")
    ticket_ids: set[str] = set()
    problems: list[str] = []
    for sample in samples:
        ticket_id = sample.get("ticket_id")
        if not ticket_id:
            problems.append("样本缺少 ticket_id")
            continue
        if ticket_id in ticket_ids:
            problems.append(f"ticket_id 重复：{ticket_id}")
        ticket_ids.add(ticket_id)
        cls = sample.get("branch_class")
        if cls not in bsf.CLASS_ORDER:
            problems.append(f"{ticket_id}: branch_class 无效：{cls}")
            continue
        if bsf.classify_row(sample) != cls:
            problems.append(f"{ticket_id}: branch_class 与字段口径不一致")
        if not isinstance(sample.get("constructed"), bool):
            problems.append(f"{ticket_id}: 缺少 constructed 标注")
        source = sample.get("source") or {}
        if not source.get("type"):
            problems.append(f"{ticket_id}: 缺少 source.type")
        verification = sample.get("verification") or {}
        if verification.get("status") != "pass":
            problems.append(f"{ticket_id}: 静态核验未通过")
        if sample.get("constructed") and not verification.get("constructed"):
            problems.append(f"{ticket_id}: 构造样本核验未标注 constructed")
        if sample.get("constructed") and not sample.get("construction"):
            problems.append(f"{ticket_id}: 构造样本缺少 construction 记录")
    if problems:
        raise SystemExit("冻结前样本校验失败：\n- " + "\n- ".join(problems[:30])
                         + ("\n（其余省略）" if len(problems) > 30 else ""))


def count_by_class(samples: list[dict]) -> dict[str, int]:
    counts = Counter(sample.get("branch_class") for sample in samples)
    return {cls: counts.get(cls, 0) for cls in bsf.CLASS_ORDER}


def build_coverage(samples: list[dict], min_total: int,
                   min_per_class: int, per_class_target: int) -> tuple[list[dict], dict]:
    """按冻结样本重算逐类覆盖与配额判定，返回（覆盖表行, 汇总检查）。"""
    by_class = count_by_class(samples)
    rows: list[dict] = []
    for cls in bsf.CLASS_ORDER:
        picked = [sample for sample in samples if sample.get("branch_class") == cls]
        sources = Counter(sample["source"]["type"] for sample in picked)
        constructed = sum(1 for sample in picked if sample.get("constructed"))
        rows.append({
            "class": cls,
            "label_zh": bsf.CLASS_LABEL[cls],
            "quota": min_per_class,
            "count": len(picked),
            "original": sources.get("original", 0),
            "load": sum(value for key, value in sources.items()
                        if key.startswith("load")),
            "constructed": constructed,
            "unique_queries": len({sample.get("user_query") for sample in picked}),
            "by_intent": dict(sorted(Counter(
                str(sample.get("expected_intent") or "(无意图)")
                for sample in picked).items())),
            "available": len(picked),
            "pass": len(picked) >= min_per_class,
        })
    total = len(samples)
    checks = {
        "schema_valid": True,
        "frozen": True,
        "total_quota": total >= min_total,
        "per_class_quota": all(row["pass"] for row in rows),
        "unique_ticket_ids": len({sample["ticket_id"] for sample in samples}) == total,
        "verification_pass": all(
            (sample.get("verification") or {}).get("status") == "pass"
            for sample in samples),
        "constructed_annotated": all(
            isinstance(sample.get("constructed"), bool) for sample in samples),
        "dataset_identity_present": True,
    }
    summary = {
        "min_total": min_total,
        "min_per_class": min_per_class,
        "per_class_target": per_class_target,
        "expected_total": per_class_target * len(bsf.CLASS_ORDER),
        "total": total,
        "by_class": by_class,
        "by_source": dict(sorted(Counter(
            sample["source"]["type"] for sample in samples).items())),
        "constructed": sum(1 for sample in samples if sample.get("constructed")),
        "quota_pass": checks["total_quota"] and checks["per_class_quota"],
    }
    return rows, {"checks": checks, "summary": summary}


def freeze(samples: list[dict], coverage: list[dict], verdict: dict,
           *, inputs: dict, quota: dict, identity: dict) -> dict:
    """给样本编冻结编号并组装回归集 payload。"""
    ordered: list[dict] = []
    for cls in bsf.CLASS_ORDER:
        seq = 0
        for sample in samples:
            if sample.get("branch_class") != cls:
                continue
            seq += 1
            frozen = dict(sample)
            frozen["sample_id"] = f"RS-{bsf.CLASS_CODE[cls]}-{seq:03d}"
            frozen["frozen"] = True
            ordered.append(frozen)
    return {
        "schema": SCHEMA,
        "frozen": True,
        "created_at": utc_now(),
        "generator": "build_regression_set.py",
        "generator_sha256": digest(HERE / "build_regression_set.py"),
        "dataset_version": identity.get("dataset_version"),
        "mock_rule_version": identity.get("mock_rule_version"),
        "inputs": inputs,
        "quota": quota,
        "counts": verdict["summary"],
        "checks": verdict["checks"],
        "coverage": coverage,
        "samples": ordered,
        "notes": [
            "冻结集为只读回归锚；变更须新建版本并重新登记 SHA-256。",
            "原集工单优先；load 档样本按 source.type=loadN 标注；构造样本单独标注。",
            "分支判定口径与 branch_sample_filter.py 一致：知识/澄清/转人工。",
            "构造样本的服务端回放核验仍需执行人按构造记录补做。",
        ],
    }


# ---------------------------------------------------------------------------
# 产物与校验表
# ---------------------------------------------------------------------------

def format_coverage_markdown(payload: dict, set_path: Path, set_sha: str) -> str:
    """人工可读的覆盖校验表（Markdown）。"""
    counts = payload["counts"]
    lines = [
        "# 分支冻结回归集覆盖校验表",
        "",
        f"- 回归集文件：`{rel_repo(set_path)}`",
        f"- SHA-256：`{set_sha}`",
        f"- 生成器：`build_regression_set.py`（SHA-256 `{payload['generator_sha256']}`）",
        f"- 生成时间：{payload['created_at']}",
        f"- 数据档身份：dataset_version=`{payload['dataset_version']}`，"
        f"mock_rule_version=`{payload['mock_rule_version']}`",
        f"- 配额：总数 ≥{counts['min_total']}，逐类 ≥{counts['min_per_class']}",
        "",
        "| 类别 | 目标 | 实际 | 去重请求 | 原集 | load 档 | 构造 | 意图分布 | 通过 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["coverage"]:
        intents = " / ".join(f"{key} {value}"
                             for key, value in row["by_intent"].items())
        lines.append(
            f"| {row['label_zh']}（{row['class']}） | {row['quota']} | {row['count']} "
            f"| {row['unique_queries']} | {row['original']} | {row['load']} "
            f"| {row['constructed']} | {intents} | {'是' if row['pass'] else '否'} |")
    lines.append(
        f"| **合计** | {counts['min_total']} | {counts['total']} "
        f"| {sum(row['unique_queries'] for row in payload['coverage'])} "
        f"| {sum(row['original'] for row in payload['coverage'])} "
        f"| {sum(row['load'] for row in payload['coverage'])} "
        f"| {counts['constructed']} | — "
        f"| {'是' if counts['quota_pass'] else '否'} |")
    lines += ["", "| 校验项 | 结果 |", "|---|---|"]
    labels = {
        "schema_valid": "结构合法",
        "frozen": "冻结标记",
        "total_quota": "总数配额",
        "per_class_quota": "逐类配额",
        "unique_ticket_ids": "ticket_id 唯一",
        "verification_pass": "逐条静态核验",
        "constructed_annotated": "构造样本标注",
        "dataset_identity_present": "数据档身份",
    }
    for key, label in labels.items():
        lines.append(f"| {label}（{key}） | {'通过' if payload['checks'].get(key) else '不通过'} |")
    lines += ["", "## 说明", ""]
    lines += [f"- {note}" for note in payload["notes"]]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, payload: dict) -> dict:
    """落盘回归集、哈希侧车、登记清单与覆盖校验表；返回路径与哈希。"""
    set_path = out_dir / SET_FILE
    set_bytes = write_json(set_path, payload)
    set_sha = hashlib.sha256(set_bytes).hexdigest()
    coverage_text = format_coverage_markdown(payload, set_path, set_sha)
    coverage_path = out_dir / COVERAGE_FILE
    coverage_sha = hashlib.sha256(write_text(coverage_path, coverage_text)).hexdigest()
    sha_path = out_dir / SHA_FILE
    write_text(sha_path, f"{set_sha}  {SET_FILE}\n")
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "file": SET_FILE,
        "sha256": set_sha,
        "bytes": len(set_bytes),
        "count": payload["counts"]["total"],
        "counts": payload["counts"],
        "quota": payload["quota"],
        "coverage": payload["coverage"],
        "checks": payload["checks"],
        "created_at": payload["created_at"],
        "generator_sha256": payload["generator_sha256"],
        "inputs": payload["inputs"],
        "sha256_file": SHA_FILE,
        "coverage_report": COVERAGE_FILE,
        "coverage_report_sha256": coverage_sha,
    }
    manifest_path = out_dir / MANIFEST_FILE
    write_json(manifest_path, manifest)
    return {"set": set_path, "sha256_file": sha_path, "manifest": manifest_path,
            "coverage": coverage_path, "sha256": set_sha,
            "coverage_sha256": coverage_sha}


def verify_set(path: Path) -> dict:
    """独立复核一个已冻结回归集：哈希侧车、结构、逐类配额、样本可核验性。"""
    payload = load_json(path)
    problems: list[str] = []
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema 不符：{payload.get('schema')}")
    if payload.get("frozen") is not True:
        problems.append("缺少 frozen=true 冻结标记")
    samples = payload.get("samples") or []
    counts = payload.get("counts") or {}
    quota = payload.get("quota") or {}
    if counts.get("total") != len(samples):
        problems.append(f"counts.total 与样本数不符：{counts.get('total')} != {len(samples)}")
    by_class = count_by_class(samples)
    if counts.get("by_class") != by_class:
        problems.append(f"逐类计数与样本不符：{counts.get('by_class')} != {by_class}")
    min_total = int(quota.get("min_total", MIN_TOTAL))
    min_per_class = int(quota.get("min_per_class", MIN_PER_CLASS))
    if len(samples) < min_total:
        problems.append(f"总数未达配额：{len(samples)} < {min_total}")
    for cls in bsf.CLASS_ORDER:
        if by_class.get(cls, 0) < min_per_class:
            problems.append(f"{cls} 未达配额：{by_class.get(cls, 0)} < {min_per_class}")
    checks = payload.get("checks") or {}
    if not checks.get("per_class_quota") or not checks.get("total_quota"):
        problems.append("内嵌覆盖校验未通过")
    ticket_ids = [sample.get("ticket_id") for sample in samples]
    if len(set(ticket_ids)) != len(ticket_ids):
        problems.append("ticket_id 存在重复")
    sample_ids = [sample.get("sample_id") for sample in samples]
    if any(not sample_id for sample_id in sample_ids):
        problems.append("存在缺少 sample_id 的样本")
    if len(set(sample_ids)) != len(sample_ids):
        problems.append("sample_id 存在重复")
    for sample in samples:
        if (sample.get("verification") or {}).get("status") != "pass":
            problems.append(f"{sample.get('ticket_id')}: 静态核验未通过")
            break
        if bsf.classify_row(sample) != sample.get("branch_class"):
            problems.append(f"{sample.get('ticket_id')}: branch_class 与字段口径不一致")
            break
        if sample.get("constructed"):
            if not (sample.get("verification") or {}).get("constructed"):
                problems.append(f"{sample.get('ticket_id')}: 构造样本核验未标注 constructed")
                break
            if not sample.get("construction"):
                problems.append(f"{sample.get('ticket_id')}: 构造样本缺少 construction 记录")
                break
            if (sample.get("source") or {}).get("type") != "constructed":
                problems.append(f"{sample.get('ticket_id')}: 构造样本来源标注不符")
                break
    constructed_total = sum(1 for sample in samples if sample.get("constructed"))
    if counts.get("constructed") != constructed_total:
        problems.append(
            f"counts.constructed 与样本不符：{counts.get('constructed')} != {constructed_total}")
    set_sha = digest(path)
    sha_side = path.parent / SHA_FILE
    if sha_side.is_file():
        recorded = sha_side.read_text(encoding="utf-8").split()[0]
        if recorded != set_sha:
            problems.append(f"侧车 SHA-256 不符：{recorded} != {set_sha}")
    result = {
        "file": rel_repo(path),
        "sha256": set_sha,
        "count": len(samples),
        "by_class": by_class,
        "quota": {"min_total": min_total, "min_per_class": min_per_class},
        "checks": checks,
        "problems": problems,
        "pass": not problems,
    }
    if problems:
        raise SystemExit("回归集复核失败：\n- " + "\n- ".join(problems))
    return result


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile-dir", type=Path, default=None,
                        help="v1.1 数据档目录（缺省 out/reference-balanced-uniform）")
    parser.add_argument("--tickets", type=Path, default=None,
                        help="原集工单文件（缺省 testing/test-data/tickets.json）")
    parser.add_argument("--candidates", type=Path, default=None,
                        help="复用已有的 branch_samples.json（缺省在进程内调用筛选器）")
    parser.add_argument("--min-total", type=int, default=MIN_TOTAL,
                        help=f"回归集总数下限（缺省 {MIN_TOTAL}，不得低于 {MIN_TOTAL}）")
    parser.add_argument("--min-per-class", type=int, default=MIN_PER_CLASS,
                        help=f"逐类样本下限（缺省 {MIN_PER_CLASS}，不得低于 {MIN_PER_CLASS}）")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="产物目录（缺省 out/regression-set；须在仓库内）")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已存在的回归集产物")
    parser.add_argument("--verify", type=Path, default=None,
                        help="只复核已有回归集，不写任何产物")
    args = parser.parse_args()

    if args.verify is not None:
        path = args.verify.resolve()
        if not path.is_file():
            raise SystemExit(f"--verify 文件不存在：{path}")
        result = verify_set(path)
        print(format_count_table_from_payload(load_json(path)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.min_total < MIN_TOTAL:
        raise SystemExit(f"--min-total 不得低于 {MIN_TOTAL}")
    if args.min_per_class < MIN_PER_CLASS:
        raise SystemExit(f"--min-per-class 不得低于 {MIN_PER_CLASS}")
    per_class_target = max(args.min_per_class,
                           -(-args.min_total // len(bsf.CLASS_ORDER)))
    profile_dir = (args.profile_dir or bsf.DEFAULT_PROFILE_DIR).resolve()
    if not profile_dir.is_dir():
        raise SystemExit(f"数据档目录不存在：{profile_dir}")
    if args.tickets is not None:
        bsf.TICKETS_PATH = args.tickets.resolve()

    if args.candidates is not None:
        candidates_path = args.candidates.resolve()
        if not candidates_path.is_file():
            raise SystemExit(f"--candidates 文件不存在：{candidates_path}")
        filter_payload = load_json(candidates_path)
        if filter_payload.get("schema") != bsf.SCHEMA:
            raise SystemExit(
                f"--candidates 不是 {bsf.SCHEMA} 结构的筛选产物：{candidates_path}")
        samples = filter_payload.get("samples") or []
        filter_inputs = filter_payload.get("inputs") or {}
        identity = {
            "dataset_version": filter_payload.get("dataset_version"),
            "mock_rule_version": filter_payload.get("mock_rule_version"),
        }
        inputs = {
            "candidates": {"path": rel_repo(candidates_path),
                           "sha256": digest(candidates_path),
                           "source": "reused branch samples"},
            "filter_generator_sha256": digest(HERE / "branch_sample_filter.py"),
            "filter_inputs": filter_inputs,
        }
    else:
        filter_payload = bsf.collect(profile_dir=profile_dir,
                                     per_class=per_class_target,
                                     construct_missing=True)
        samples = filter_payload.get("samples") or []
        identity = {
            "dataset_version": filter_payload.get("dataset_version"),
            "mock_rule_version": filter_payload.get("mock_rule_version"),
        }
        inputs = {
            "candidates": {"path": None, "sha256": None,
                           "source": "in-process branch_sample_filter.collect"},
            "filter_generator_sha256": digest(HERE / "branch_sample_filter.py"),
            "filter_inputs": filter_payload.get("inputs") or {},
        }

    # 冻结前把真实样本的落盘核验记录补齐（--candidates 复用时防止手改或旧数据）
    order_ids, _orders = bsf.load_orders()
    kb_docs = bsf.load_knowledge()
    for sample in samples:
        cls = sample.get("branch_class")
        if cls not in bsf.CLASS_ORDER:
            raise SystemExit(f"样本 branch_class 无效：{sample.get('ticket_id')}")
        constructed = bool(sample.get("constructed"))
        sample["verification"] = bsf.verify_sample(sample, cls, order_ids, kb_docs)
        if constructed:
            # 构造样本保留「待服务端回放」的如实标注，不被静态核验覆盖
            sample["verification"].update({
                "constructed": True,
                "service_replay": "pending",
                "manual_review": "required",
            })
    validate_samples(samples)
    coverage, verdict = build_coverage(samples, args.min_total,
                                       args.min_per_class, per_class_target)
    verdict["checks"]["dataset_identity_present"] = bool(
        identity.get("dataset_version") and identity.get("mock_rule_version"))
    if not verdict["checks"]["total_quota"] or not verdict["checks"]["per_class_quota"]:
        summary = verdict["summary"]
        raise SystemExit(
            "冻结集配额未达标，未写产物："
            f"total={summary['total']}（需 ≥{args.min_total}），"
            f"by_class={summary['by_class']}（逐类需 ≥{args.min_per_class}）；"
            "请增加候选样本或允许构造样本后重跑")

    out_dir = ensure_in_repo(args.out_dir or DEFAULT_OUT_DIR, "产物目录")
    targets = [out_dir / name for name in (SET_FILE, SHA_FILE, MANIFEST_FILE,
                                            COVERAGE_FILE)]
    existing = [str(path) for path in targets if path.exists()]
    if existing and not args.force:
        raise SystemExit("产物已存在，未覆盖（--force 才覆盖）：\n- "
                         + "\n- ".join(existing))

    quota = {"min_total": args.min_total, "min_per_class": args.min_per_class,
             "per_class_target": per_class_target,
             "expected_total": per_class_target * len(bsf.CLASS_ORDER)}
    payload = freeze(samples, coverage, verdict, inputs=inputs, quota=quota,
                     identity=identity)
    output = write_outputs(out_dir, payload)
    # 落盘后独立复核（含侧车哈希），避免「写完即宣布完成」
    result = verify_set(output["set"])
    print(format_count_table_from_payload(payload))
    print(json.dumps({
        "regression_set": rel_repo(output["set"]),
        "sha256": result["sha256"],
        "sha256_file": rel_repo(output["sha256_file"]),
        "manifest": rel_repo(output["manifest"]),
        "coverage_report": rel_repo(output["coverage"]),
        "count": result["count"],
        "by_class": result["by_class"],
        "constructed": payload["counts"]["constructed"],
        "checks": payload["checks"],
    }, ensure_ascii=False, indent=2))


def format_count_table_from_payload(payload: dict) -> str:
    """逐类计数表（与 branch_sample_filter.py 表头一致，全角对齐）。"""
    counts = payload.get("counts") or {}
    coverage = payload.get("coverage") or []
    header = (pad("类别", 18) + pad("目标", 6, "right") + pad("实际", 6, "right")
              + pad("原集", 6, "right") + pad("load 档", 8, "right")
              + pad("构造", 6, "right") + pad("通过", 6, "right"))
    lines = [f"分支冻结回归集覆盖校验（总数 ≥{counts.get('min_total')}，"
             f"逐类 ≥{counts.get('min_per_class')}）", header, "-" * 62]
    for row in coverage:
        label = f"{row['label_zh']}({row['class']})"
        lines.append(pad(label, 18) + pad(str(row["quota"]), 6, "right")
                     + pad(str(row["count"]), 6, "right")
                     + pad(str(row["original"]), 6, "right")
                     + pad(str(row["load"]), 8, "right")
                     + pad(str(row["constructed"]), 6, "right")
                     + pad("是" if row["pass"] else "否", 6, "right"))
    lines.append("-" * 62)
    lines.append(pad("合计", 18) + pad(str(counts.get("min_total", "")), 6, "right")
                 + pad(str(counts.get("total", "")), 6, "right")
                 + pad(str(sum(row["original"] for row in coverage)), 6, "right")
                 + pad(str(sum(row["load"] for row in coverage)), 8, "right")
                 + pad(str(counts.get("constructed", "")), 6, "right")
                 + pad("是" if counts.get("quota_pass") else "否", 6, "right"))
    return "\n".join(lines)


if __name__ == "__main__":
    main()
