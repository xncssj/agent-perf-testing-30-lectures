#!/usr/bin/env python3
"""结果聚合器（批次 R04/R05：summary/run.json + hashes.sha256）。

输入：一个运行目录（含 manifest.json、raw/k6-summary.json、events/、fuse/）。
输出：
  summary/run.json —— 运行状态、实际窗口、三态门禁（契约 §5 判定顺序）、证据报告与排除说明
  hashes.sha256    —— 运行目录全部产物的 SHA-256 清单（R04 产物哈希）

门禁判定顺序（api-sketch §5，固定）：
  1) manifest / 样本 / 时间窗口 / 必需安全证据校验；
  2) 任一缺失、不属于当前 run_id 或未覆盖 measured 窗口 → NOT_VERIFIED（附原因码）；
  3) 证据齐全时计算三项阈值（等值边界）→ FAIL（附原因码）；
  4) 全部满足 → PASS。
NOT_VERIFIED 不是通过；相应窗口的合格请求不进入安全约束后的 Goodput（排除说明写入 run.json）。
"""
import hashlib
import json
import os
import sys
from datetime import datetime

CONTRACT_VERSION = "load-test-remediation-v1"
QUALIFIED_RATE_MIN = 0.90
ERROR_RATE_MAX = 0.005

R_MISSING_MANIFEST = "MISSING_MANIFEST"
R_MISSING_SAFETY_EVIDENCE = "MISSING_SAFETY_EVIDENCE"
R_WINDOW_MISMATCH = "WINDOW_MISMATCH"
R_QUALIFIED_BELOW = "QUALIFIED_RATE_BELOW_MIN"
R_ERROR_ABOVE = "ERROR_RATE_ABOVE_MAX"
R_E2E_EXCEEDED = "E2E_SLO_EXCEEDED"
R_FUSED = "FUSED"


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def ndjson_records(path):
    records = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return records


def evidence_report(path, run_id, measured):
    """证据文件三查：存在且非空、事件归属当前 run_id、时间覆盖 measured 窗口。"""
    report = {"path": path, "present": False, "belongs_to_run": False, "covers_measured": False}
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return report
    report["present"] = True
    records = ndjson_records(path)
    if not records:
        return report
    foreign = [r for r in records if r.get("run_id") != run_id]
    report["belongs_to_run"] = not foreign
    if foreign:
        report["foreign_run_events"] = len(foreign)
    start, end = parse_ts(measured.get("start")), parse_ts(measured.get("end"))
    if start is None or end is None:
        report["covers_measured"] = False
        return report
    times = [parse_ts(r.get("ts")) for r in records]
    times = [t for t in times if t is not None]
    if not times:
        return report
    # 覆盖判定：证据时间范围与 measured 窗口有交集且采样延伸到窗口内
    first, last = min(times), max(times)
    report["covers_measured"] = first <= end and last >= start
    report["first_ts"] = min(times).isoformat()
    report["last_ts"] = max(times).isoformat()
    return report


def main(run_dir):
    manifest_path = os.path.join(run_dir, "manifest.json")
    summary_path = os.path.join(run_dir, "raw", "k6-summary.json")
    reasons = []

    manifest = load_json(manifest_path)
    has_manifest = manifest is not None and manifest.get("contract_version") == CONTRACT_VERSION
    if not has_manifest:
        reasons.append(R_MISSING_MANIFEST)
        manifest = manifest or {}

    k6_summary = load_json(summary_path)
    context = (k6_summary or {}).get("context", {})
    board = (k6_summary or {}).get("board", {})
    windows = (context.get("windows") or {})
    measured = windows.get("measured") or {}

    # 样本与运行归属：k6 摘要存在且 run_id 与 manifest 一致
    has_samples = k6_summary is not None
    if has_samples and has_manifest and context.get("runId") != manifest.get("run_id"):
        if R_WINDOW_MISMATCH not in reasons:
            reasons.append(R_WINDOW_MISMATCH)

    # 时间窗口：实际 measured 窗口必须存在（聚合器不从日志推测）。
    # C1 以逐档窗口为准：运行级包络 = 各档 measured 窗口的并集跨度。
    if manifest.get("scenario") == "c1" and (not measured.get("start") or not measured.get("end")):
        levels_doc = load_json(os.path.join(run_dir, "raw", "k6-levels.json"))
        levels = (levels_doc or {}).get("levels") or []
        starts = [parse_ts((l.get("measured") or {}).get("start")) for l in levels]
        ends = [parse_ts((l.get("measured") or {}).get("end")) for l in levels]
        starts = [s for s in starts if s]
        ends = [e for e in ends if e]
        if starts and ends:
            measured = {"start": min(starts).isoformat(), "end": max(ends).isoformat()}
            # B14 修复：多档场景聚合层 measured 直接采用逐档包络（多档下聚合单窗与
            # 逐档口径同指一个窗），envelope 字段保留作溯源；run.json windows_actual
            # 不再为 null。单窗口径（b0/s1/e1）仍取 k6 原生 window_measured。
            windows["measured"] = measured
            windows["measured_envelope_from_levels"] = measured
    if not measured.get("start") or not measured.get("end"):
        if R_WINDOW_MISMATCH not in reasons:
            reasons.append(R_WINDOW_MISMATCH)

    # 必需安全证据（D1—D3）
    evidence = {}
    required = manifest.get("required_safety_evidence") or []
    if has_manifest:
        for rel in required:
            rep = evidence_report(os.path.join(run_dir, rel), manifest.get("run_id"), measured)
            evidence[rel] = rep
            if not rep["present"]:
                if R_MISSING_SAFETY_EVIDENCE not in reasons:
                    reasons.append(R_MISSING_SAFETY_EVIDENCE)
            elif not rep["belongs_to_run"] or not rep["covers_measured"]:
                if R_WINDOW_MISMATCH not in reasons:
                    reasons.append(R_WINDOW_MISMATCH)

    # 熔断：当前 run 目录存在熔断事件即 FUSED（不可判 PASS）
    fused = bool(ndjson_records(os.path.join(run_dir, "fuse", "events.ndjson")))
    if fused and R_FUSED not in reasons:
        reasons.append(R_FUSED)

    status = "FUSED" if fused else ("COMPLETED" if has_samples else "ABORTED")

    # 三项阈值（等值边界）：证据链完整时才计算
    verdict = None
    if not reasons:
        qualified_rate = board.get("qualifiedRate")
        error_rate = board.get("errorRate")
        e2e_p90 = board.get("e2eP90")
        slo_ms = context.get("sloMs")
        values = [qualified_rate, error_rate, e2e_p90, slo_ms]
        if any(v is None for v in values):
            reasons.append("INSUFFICIENT_SAMPLES")
        else:
            if float(qualified_rate) < QUALIFIED_RATE_MIN:
                reasons.append(R_QUALIFIED_BELOW)
            if float(error_rate) > ERROR_RATE_MAX:
                reasons.append(R_ERROR_ABOVE)
            if float(e2e_p90) > float(slo_ms):
                reasons.append(R_E2E_EXCEEDED)

    if reasons:
        verdict = "NOT_VERIFIED" if any(
            r in (R_MISSING_MANIFEST, R_MISSING_SAFETY_EVIDENCE, R_WINDOW_MISMATCH, R_FUSED,
                  "INSUFFICIENT_SAMPLES") for r in reasons) else "FAIL"
    else:
        verdict = "PASS"

    # 逐档摘要复核（C1）：k6 侧摘要拿不到 manifest 信息，聚合器按运行级证据链重判每档 gate，
    # 产出 summary/levels.json（权威逐档判定；NOT_VERIFIED 档不得计入容量曲线，契约 §5）
    levels_out = None
    levels_doc = load_json(os.path.join(run_dir, "raw", "k6-levels.json"))
    if levels_doc:
        levels_out = []
        for level in levels_doc.get("levels", []):
            level_gate_reasons = []
            if not has_manifest:
                level_gate_reasons.append(R_MISSING_MANIFEST)
            for rel, rep in evidence.items():
                if not rep.get("present"):
                    level_gate_reasons.append(R_MISSING_SAFETY_EVIDENCE)
                    break
                if not rep.get("belongs_to_run") or not rep.get("covers_measured"):
                    level_gate_reasons.append(R_WINDOW_MISMATCH)
                    break
            if fused:
                level_gate_reasons.append(R_FUSED)
            if not level_gate_reasons:
                q = level.get("qualified_rate")
                e = level.get("error_rate")
                p = level.get("e2e_client_wall_p90_ms")
                slo = context.get("sloMs")
                if any(v is None for v in (q, e, p, slo)):
                    level_gate_reasons.append("INSUFFICIENT_SAMPLES")
                else:
                    if float(q) < QUALIFIED_RATE_MIN:
                        level_gate_reasons.append(R_QUALIFIED_BELOW)
                    if float(e) > ERROR_RATE_MAX:
                        level_gate_reasons.append(R_ERROR_ABOVE)
                    if float(p) > float(slo):
                        level_gate_reasons.append(R_E2E_EXCEEDED)
            if level_gate_reasons:
                level_verdict = "NOT_VERIFIED" if any(
                    r in (R_MISSING_MANIFEST, R_MISSING_SAFETY_EVIDENCE, R_WINDOW_MISMATCH,
                          R_FUSED, "INSUFFICIENT_SAMPLES") for r in level_gate_reasons) else "FAIL"
            else:
                level_verdict = "PASS"
            level = dict(level)
            level["gate"] = {"verdict": level_verdict, "reasons": level_gate_reasons}
            levels_out.append(level)
        with open(os.path.join(run_dir, "summary", "levels.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "contract_version": CONTRACT_VERSION,
                "run_id": manifest.get("run_id") or context.get("runId"),
                "levels": levels_out,
            }, fh, ensure_ascii=False, indent=2)

    # D4 排除说明：未验证窗口的合格请求不进入安全约束后的 Goodput；
    # 报告必须能指出缺失的证据、受影响窗口和被排除的请求范围
    excluded = verdict != "PASS"
    missing_evidence = [rel for rel, rep in evidence.items() if not rep.get("present")]
    misaligned_evidence = [rel for rel, rep in evidence.items()
                           if rep.get("present") and (not rep.get("belongs_to_run") or not rep.get("covers_measured"))]

    run_json = {
        "contract_version": CONTRACT_VERSION,
        "run_id": manifest.get("run_id") or context.get("runId"),
        "scenario": manifest.get("scenario"),
        "status": status,
        "windows_actual": windows,
        "gate": {
            "verdict": verdict,
            "reason_codes": reasons,
            "evaluated_from": {
                "qualified_rate": board.get("qualifiedRate"),
                "error_rate": board.get("errorRate"),
                "e2e_client_wall_p90_ms": board.get("e2eP90"),
                "e2e_slo_ms": context.get("sloMs"),
            },
        },
        "required_safety_evidence": required,
        "evidence": evidence,
        "goodput_exclusion": {
            "excluded_from_qualified_goodput": excluded,
            "affected_window": measured or None,
            "missing_evidence": missing_evidence,
            "misaligned_evidence": misaligned_evidence,
            "excluded_scope": ("全部合格请求（窗口级排除，" + verdict + "）" if excluded else "无"),
            "note": ("NOT_VERIFIED/FAIL 窗口的合格请求不进入安全约束后的 Goodput（契约 §3.4/§5）"
                     if excluded else "无排除"),
        },
        "k6_exit": {
            "samples_ref": "raw/k6-samples.ndjson",
            "summary_ref": "raw/k6-summary.json",
        },
    }
    out_path = os.path.join(run_dir, "summary", "run.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(run_json, fh, ensure_ascii=False, indent=2)

    # 产物哈希（R04）：覆盖运行目录全部文件（含 run.json），清单自身除外
    hashes_path = os.path.join(run_dir, "hashes.sha256")
    entries = []
    for root, _dirs, files in os.walk(run_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, run_dir)
            if rel == "hashes.sha256" or full == hashes_path:
                continue
            digest = hashlib.sha256()
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
            entries.append((rel, digest.hexdigest()))
    with open(hashes_path, "w", encoding="utf-8") as fh:
        for rel, digest in sorted(entries):
            fh.write(f"{digest}  {rel}\n")

    print(f"verify-run: status={status} gate={verdict} reasons={reasons}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：verify-run.py <run_dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
