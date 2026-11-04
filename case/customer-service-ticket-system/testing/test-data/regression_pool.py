"""失败样本候选池与版本化回归集（006 R05 / 契约 §5）。

职责边界：
- 线上或实验失败先进入候选池，记录来源 run_id、脱敏问题摘要、失败类别
  与证据位置；未复核或重复失败只留在候选池；
- 人工复核并去重后，批准样本进入**新版本**回归集（regression_set_v<N>.json）；
- 固定基准集 ``tickets.json`` 与历史 205 条报告不被本模块覆盖；
- 新旧数据集必须在核对哈希与构成后才能比较，本模块不做自动合并。

候选字段遵循内容最小化：问题只存脱敏摘要与长度区间，不存原文。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

POOL_DIR = Path(__file__).resolve().parent / "regression"
CANDIDATES_FILE = POOL_DIR / "candidates.jsonl"
REVIEW_LOG = POOL_DIR / "review_log.jsonl"
SET_PREFIX = "regression_set_v"

# 候选状态机：pending → approved / rejected（人工复核决定）
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"

_FAILURE_CATEGORIES = (
    "business_failed",     # 业务判定不合格
    "tool_failed",         # 工具执行失败/超时
    "slo_breach",          # SLO 超限
    "safety_violation",    # 护栏违规
)


def failure_category(stop_reason: str, status: str, slo_breached: bool) -> str:
    """由终态归出失败类别；无法归类时显式返回 other，不猜。"""
    if slo_breached:
        return "slo_breach"
    if stop_reason in ("tool_failed", "provider_error"):
        return "tool_failed"
    if status == "failed":
        return "business_failed"
    return "other"


def candidate_key(candidate: dict) -> str:
    """去重键：数据集版本 + 工单 + 失败类别 + 脱敏摘要哈希。"""
    raw = "|".join([
        candidate.get("dataset_sha256", ""),
        candidate.get("task_id", ""),
        candidate.get("failure_category", ""),
        candidate.get("question_digest", ""),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _digest_question(question: str) -> str:
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()[:16]


def add_candidate(
    pool_dir: Path,
    *,
    task_id: str,
    run_id: str,
    dataset_sha256: str,
    question_digest: str,
    failure_category: str,
    evidence_path: str,
    length_bucket: str = "",
    note: str = "",
) -> dict:
    """新增候选；重复候选（同键）不重复入库，返回现有候选。"""
    pool_dir = Path(pool_dir)
    pool_dir.mkdir(parents=True, exist_ok=True)
    candidate = {
        "candidate_key": "",
        "task_id": task_id,
        "run_id": run_id,
        "dataset_sha256": dataset_sha256,
        "question_digest": question_digest,
        "failure_category": failure_category,
        "evidence_path": evidence_path,
        "length_bucket": length_bucket,
        "note": note,
        "status": PENDING,
        "added_at": datetime.now().isoformat(timespec="seconds"),
    }
    candidate["candidate_key"] = candidate_key(candidate)
    existing = _load_candidates(pool_dir)
    for cand in existing:
        if cand.get("candidate_key") == candidate["candidate_key"]:
            return {"status": "duplicate", "candidate": cand}
    with (pool_dir / "candidates.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(candidate, ensure_ascii=False) + "\n")
    return {"status": "added", "candidate": candidate}


def review_candidate(
    pool_dir: Path, candidate_key: str, decision: str, reviewer: str,
    rationale: str = "",
) -> dict:
    """人工复核：只确认样本分类与证据，不引入模型 Judge。"""
    if decision not in (APPROVED, REJECTED):
        raise ValueError(f"复核决定只能是 approved/rejected：{decision}")
    pool_dir = Path(pool_dir)
    candidates = _load_candidates(pool_dir)
    for cand in candidates:
        if cand.get("candidate_key") == candidate_key:
            if cand.get("status") != PENDING:
                return {"status": "already_reviewed", "candidate": cand}
            cand["status"] = decision
            _rewrite_candidates(pool_dir, candidates)
            record = {
                "candidate_key": candidate_key,
                "decision": decision,
                "reviewer": reviewer,
                "rationale": rationale,
                "reviewed_at": datetime.now().isoformat(timespec="seconds"),
            }
            with (pool_dir / "review_log.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            return {"status": "reviewed", "candidate": cand, "record": record}
    return {"status": "not_found", "candidate_key": candidate_key}


def promote(
    pool_dir: Path, dataset_sha256: str, operator: str = "",
) -> dict:
    """把 approved 且不重复的候选写入新版本回归集；固定基准集不变。"""
    pool_dir = Path(pool_dir)
    candidates = _load_candidates(pool_dir)
    approved = [c for c in candidates if c.get("status") == APPROVED
                and c.get("dataset_sha256") == dataset_sha256]
    if not approved:
        return {"status": "empty", "version": None}
    existing = sorted(pool_dir.glob(f"{SET_PREFIX}*.json"))
    version = len(existing) + 1
    out = pool_dir / f"{SET_PREFIX}{version}.json"
    payload = {
        "schema": "regression-set-v1",
        "version": version,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_sha256": dataset_sha256,
        "operator": operator,
        # 候选进入回归集时冻结其证据定位与脱敏摘要
        "samples": approved,
        "counts": {
            "candidates_total": len(candidates),
            "approved_in_set": len(approved),
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return {"status": "promoted", "version": version, "file": str(out)}


def load_set(pool_dir: Path, version: int | None = None) -> dict | None:
    """读取指定版本回归集（缺省最新版）；版本不可变，只读。"""
    pool_dir = Path(pool_dir)
    if version is None:
        files = sorted(
            pool_dir.glob(f"{SET_PREFIX}*.json"),
            key=lambda p: int(re.search(r"v(\d+)", p.name).group(1)),
        )
        if not files:
            return None
        path = files[-1]
    else:
        path = pool_dir / f"{SET_PREFIX}{version}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_candidates(pool_dir: Path) -> list[dict]:
    path = Path(pool_dir) / "candidates.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _rewrite_candidates(pool_dir: Path, candidates: list[dict]) -> None:
    path = Path(pool_dir) / "candidates.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for cand in candidates:
            fh.write(json.dumps(cand, ensure_ascii=False) + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="失败样本候选池与回归集管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="新增失败候选（重复自动去重）")
    p_add.add_argument("--task-id", required=True)
    p_add.add_argument("--run-id", required=True)
    p_add.add_argument("--dataset-sha256", required=True)
    p_add.add_argument("--question", default="",
                       help="问题原文仅用于本地计算脱敏摘要，不落盘")
    p_add.add_argument("--failure-category", required=True,
                       choices=sorted(set(_FAILURE_CATEGORIES) | {"other"}))
    p_add.add_argument("--evidence-path", required=True)
    p_add.add_argument("--length-bucket", default="")

    p_review = sub.add_parser("review", help="人工复核候选")
    p_review.add_argument("--candidate-key", required=True)
    p_review.add_argument("--decision", required=True,
                          choices=[APPROVED, REJECTED])
    p_review.add_argument("--reviewer", required=True)
    p_review.add_argument("--rationale", default="")

    p_promote = sub.add_parser("promote", help="批准样本写入新版本回归集")
    p_promote.add_argument("--dataset-sha256", required=True)
    p_promote.add_argument("--operator", default="")

    args = parser.parse_args(argv)
    if args.cmd == "add":
        result = add_candidate(
            POOL_DIR, task_id=args.task_id, run_id=args.run_id,
            dataset_sha256=args.dataset_sha256,
            question_digest=_digest_question(args.question),
            failure_category=args.failure_category,
            evidence_path=args.evidence_path,
            length_bucket=args.length_bucket,
        )
    elif args.cmd == "review":
        result = review_candidate(
            POOL_DIR, args.candidate_key, args.decision, args.reviewer,
            args.rationale)
    else:
        result = promote(POOL_DIR, args.dataset_sha256, args.operator)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") not in ("not_found",) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
