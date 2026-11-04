"""同轮评测批次套件（006 契约 §3）：六类专项报告的批次身份与冻结清单。

阶段三七项联合门禁只消费同一 ``evaluation_suite_id`` 的冻结报告清单，
不再从目录按文件名排序取"最新"——那是 G01 混轮（bm25 与 hybrid 指纹
混入同一次门禁）的根因。三态语义与 002 压测门禁一致：

- 报告缺失、无批次身份、窗口不可核验、指纹或数据集不一致 → ``NOT_VERIFIED``；
- 证据完整但任一既有阈值不满足 → ``FAIL``；
- 证据完整且七项均满足 → ``PASS``。

阈值不在本模块定义，仍读取 ``workflow_config.yaml``（gate_rules）。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

MANIFEST_SCHEMA_VERSION = "observability-evaluation-v1"

# 七项联合门禁依赖的六类专项报告（顺序即运行顺序建议）
REQUIRED_REPORT_TYPES = (
    "benchmark",
    "branch_coverage",
    "rag_quality",
    "longtail",
    "critical_path",
    "stability",
)

# 六类报告必须一致的指纹核心键（006 契约 §3：相同的代码、模型、
# Prompt、工具、检索配置指纹；检索口径 search_mode 是 G01 混轮的直接观察点）
CORE_FINGERPRINT_KEYS = ("provider", "model", "architecture", "search_mode")

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_NOT_VERIFIED = "NOT_VERIFIED"

_SUITE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SuiteError(ValueError):
    """评测套件清单无法建立、结构非法或冻结输入不满足同轮要求。"""


def compute_dataset_sha256(path: Path | str) -> str:
    """计算数据文件 SHA-256，供清单冻结数据集指纹。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_report(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"报告文件损坏或不可读：{path}（{exc}）") from exc


def _require_window(payload: dict, label: str) -> tuple[str, str]:
    started = payload.get("started_at")
    ended = payload.get("ended_at")
    if not started or not ended:
        raise SuiteError(
            f"{label} 报告缺少有效计量窗口（started_at/ended_at）；"
            "文件名时间戳不能代替窗口，旧口径报告不得补造身份"
        )
    if str(ended) < str(started):
        raise SuiteError(f"{label} 报告窗口非法：ended_at 早于 started_at")
    return str(started), str(ended)


def create_suite_manifest(
    reports_dir: Path | str,
    evaluation_suite_id: str,
    report_paths: dict[str, Path | str],
    dataset_sha256: str,
    planned_window: dict | None = None,
) -> dict:
    """由同轮六类专项报告建立冻结清单（不落盘，落盘用 write_manifest）。

    任何一类报告缺 run_id、缺窗口或核心指纹不一致都会抛 SuiteError，
    避免把旧口径报告包装成新批次身份。
    """
    if not evaluation_suite_id or not _SUITE_ID_RE.match(evaluation_suite_id):
        raise SuiteError(
            f"evaluation_suite_id 非法：{evaluation_suite_id!r}"
            "（允许字母数字与 . _ -，且以字母数字开头）"
        )
    if set(report_paths) != set(REQUIRED_REPORT_TYPES):
        raise SuiteError(
            "报告类型必须恰好覆盖六类："
            f"缺少 {sorted(set(REQUIRED_REPORT_TYPES) - set(report_paths))}，"
            f"多余 {sorted(set(report_paths) - set(REQUIRED_REPORT_TYPES))}"
        )

    reports_dir = Path(reports_dir)
    entries: dict[str, dict] = {}
    reference_fp: dict | None = None
    for rtype in REQUIRED_REPORT_TYPES:
        path = reports_dir / str(report_paths[rtype])
        payload = _load_report(path)
        if payload.get("test_type") != rtype:
            raise SuiteError(
                f"{rtype} 清单项指向的文件 test_type={payload.get('test_type')!r}：{path}"
            )
        run_id = payload.get("run_id")
        if not run_id:
            raise SuiteError(
                f"{rtype} 报告缺少 run_id（{path}）；没有批次身份的报告"
                "只能按历史口径展示，不得纳入同轮评测套件"
            )
        started, ended = _require_window(payload, rtype)
        fp = payload.get("fingerprint")
        if not isinstance(fp, dict) or any(k not in fp for k in CORE_FINGERPRINT_KEYS):
            raise SuiteError(
                f"{rtype} 报告指纹缺失核心键 {list(CORE_FINGERPRINT_KEYS)}：{path}"
            )
        if reference_fp is None:
            reference_fp = fp
        else:
            diff = [k for k in CORE_FINGERPRINT_KEYS if fp.get(k) != reference_fp.get(k)]
            if diff:
                raise SuiteError(
                    f"{rtype} 与基准报告核心指纹不一致（{diff}）："
                    f"{fp} vs {reference_fp}（G01 混轮）"
                )
        own_dataset = payload.get("dataset_sha256")
        if own_dataset and own_dataset != dataset_sha256:
            raise SuiteError(
                f"{rtype} 报告数据集哈希与声明不一致：{own_dataset} != {dataset_sha256}"
            )
        entries[rtype] = {
            "file": path.name,
            "report_run_id": str(run_id),
            "started_at": started,
            "ended_at": ended,
            "dataset_sha256": own_dataset,
        }

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "evaluation_suite_id": evaluation_suite_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "planned_window": planned_window,
        "dataset_sha256": dataset_sha256,
        "fingerprint": reference_fp,
        "reports": entries,
    }


def write_manifest(manifest: dict, reports_dir: Path | str) -> Path:
    """把冻结清单写入 reports 目录，返回清单文件路径。"""
    out = Path(reports_dir) / f"evaluation_suite_{manifest['evaluation_suite_id']}.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_manifest(path: Path | str) -> dict:
    """读取冻结清单并校验结构；结构非法抛 SuiteError。"""
    manifest = _load_report(Path(path))
    if not isinstance(manifest, dict) or not manifest.get("evaluation_suite_id"):
        raise SuiteError(f"清单缺少 evaluation_suite_id：{path}")
    reports = manifest.get("reports")
    if not isinstance(reports, dict) or set(reports) != set(REQUIRED_REPORT_TYPES):
        missing = sorted(set(REQUIRED_REPORT_TYPES) - set(reports or {}))
        raise SuiteError(f"清单报告类型不完整，缺少：{missing}（{path}）")
    if not manifest.get("dataset_sha256"):
        raise SuiteError(f"清单缺少 dataset_sha256（数据集未冻结）：{path}")
    return manifest


def _issue(code: str, detail: str, evidence_path: str | Path) -> dict:
    return {"code": code, "detail": detail, "evidence_path": str(evidence_path)}


def validate_suite(
    manifest: dict,
    reports_dir: Path | str,
) -> tuple[dict[str, dict], list[dict]]:
    """按冻结清单逐项复核报告；返回（可读报告集, 问题列表）。

    问题列表非空即 ``NOT_VERIFIED``：门禁不得在证据不完整时继续判定。
    每条问题带原因码与冲突证据路径（006 契约 §3 目标处理表）。
    """
    reports_dir = Path(reports_dir)
    loaded: dict[str, dict] = {}
    issues: list[dict] = []
    manifest_fp = manifest.get("fingerprint") or {}

    for rtype in REQUIRED_REPORT_TYPES:
        entry = manifest["reports"][rtype]
        path = reports_dir / entry["file"]
        if not path.exists():
            issues.append(_issue(
                "MISSING_REPORT", f"{rtype} 报告文件缺失", path))
            continue
        try:
            payload = _load_report(path)
        except SuiteError as exc:
            issues.append(_issue("REPORT_CORRUPT", str(exc), path))
            continue

        if payload.get("test_type") != rtype:
            issues.append(_issue(
                "TYPE_MISMATCH",
                f"test_type={payload.get('test_type')!r} 与清单类型 {rtype} 不符",
                path,
            ))
            continue
        if payload.get("run_id") != entry.get("report_run_id"):
            issues.append(_issue(
                "IDENTITY_MISMATCH",
                f"run_id 与冻结清单不一致：{payload.get('run_id')!r}"
                f" != {entry.get('report_run_id')!r}（报告可能在冻结后被改写）",
                path,
            ))
        started = payload.get("started_at")
        ended = payload.get("ended_at")
        if not started or not ended or str(ended) < str(started):
            issues.append(_issue(
                "WINDOW_UNVERIFIABLE",
                f"有效计量窗口缺失或非法（started_at={started!r}, ended_at={ended!r}）",
                path,
            ))
        elif manifest.get("planned_window"):
            plan = manifest["planned_window"]
            if str(started) < str(plan.get("started_at", "")) or \
                    str(ended) > str(plan.get("ended_at", "")):
                issues.append(_issue(
                    "WINDOW_OUTSIDE_PLANNED",
                    f"窗口 {started}..{ended} 超出评测计划 {plan}",
                    path,
                ))

        fp = payload.get("fingerprint")
        if not isinstance(fp, dict) or any(k not in fp for k in CORE_FINGERPRINT_KEYS):
            issues.append(_issue(
                "FINGERPRINT_INCOMPLETE",
                f"指纹缺少核心键 {list(CORE_FINGERPRINT_KEYS)}",
                path,
            ))
        else:
            diff = [k for k in CORE_FINGERPRINT_KEYS if fp.get(k) != manifest_fp.get(k)]
            if diff:
                issues.append(_issue(
                    "FINGERPRINT_MISMATCH",
                    f"与清单指纹不一致（{diff}）：{ {k: fp.get(k) for k in diff} }"
                    f" vs { {k: manifest_fp.get(k) for k in diff} }",
                    path,
                ))

        own_dataset = payload.get("dataset_sha256")
        if own_dataset and own_dataset != manifest.get("dataset_sha256"):
            issues.append(_issue(
                "DATASET_MISMATCH",
                f"数据集哈希与清单不一致：{own_dataset} != {manifest.get('dataset_sha256')}",
                path,
            ))

        loaded[rtype] = payload

    return loaded, issues
