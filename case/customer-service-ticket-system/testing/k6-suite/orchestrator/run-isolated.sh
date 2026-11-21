#!/usr/bin/env bash
# 运行启动器（批次 R04/C1）：为每次执行创建独立 run_id 目录与 manifest，以真实 k6 PID 启动发压。
# 用法：orchestrator/run-isolated.sh <1|2|3> <b0|c1|s1|e1|e2|calib> [k6 额外参数（-e K=V 形式透传）]
# 行为：
#   1. 生成 run_id 并创建 <阶段>/out/runs/<run_id>/ 目录树（不覆盖已存在目录）
#   2. 写 manifest.json（契约 RunManifest：契约版本、场景参数、阈值、必需安全证据、进程文件路径）
#   3. 记录真实 k6 PID 到 process/k6.pid（校验进程身份含 k6）
#   4. 启动环境监控（≤15s）、业务监控（≤10s）与熔断看护（≤1s 轮询熔断事件）
#   5. k6 结束后调用 verify-run.py 聚合出 summary/run.json 与 hashes.sha256
# 本脚本不做重启决策：失败/熔断后是否以新 run_id 重启由主协调者决定（C5）。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

STAGE_ARG="${1:-}"
SCENARIO="${2:-}"
case "$STAGE_ARG" in
  1) STAGE_ID="1"; STAGE_DIR_NAME="$STAGE_DIR_NAME_1"; DEFAULT_PORT=8000; SCRIPT="b0-baseline.js" ;;
  2) STAGE_ID="2"; STAGE_DIR_NAME="$STAGE_DIR_NAME_2"; DEFAULT_PORT=8001; SCRIPT="b0-baseline.js" ;;
  3) STAGE_ID="3"; STAGE_DIR_NAME="$STAGE_DIR_NAME_3"; DEFAULT_PORT=8002; SCRIPT="b0-baseline.js" ;;
  *) echo "用法：run-isolated.sh <1|2|3> <b0|c1|s1|e1|e2|calib> [k6 额外参数]" >&2; exit 2 ;;
esac
case "$SCENARIO" in
  b0) SCRIPT="b0-baseline.js"; MODE="baseline" ;;
  c1) SCRIPT="c1-capacity.js"; MODE="capacity" ;;
  s1) SCRIPT="s1-stability.js"; MODE="stability" ;;
  e1) SCRIPT="e1-traffic-change.js"; MODE="traffic-change" ;;
  e2) SCRIPT="e2-multiturn.js"; MODE="multiturn" ;;
  calib) SCRIPT="calib.js"; MODE="calibration" ;;   # A5 校准场景：1 VU × COUNT 笔，不参与门禁
  *) echo "用法：run-isolated.sh <1|2|3> <b0|c1|s1|e1|e2|calib> [k6 额外参数]" >&2; exit 2 ;;
esac
shift 2 || true

STAGE_DIR="$CASE_DIR/$STAGE_DIR_NAME"
BASE_URL_VALUE="${BASE_URL:-http://127.0.0.1:$DEFAULT_PORT}"
RUN_ID="${RUN_ID:-$(new_run_id "$SCENARIO")}"
RUN_DIR="$(runs_root "$STAGE_DIR")/$RUN_ID"

# C1 运行目录规则：不得指向已存在的可写运行目录（契约 §2）
if [ -e "$RUN_DIR" ]; then
  echo "错误：运行目录已存在 ${RUN_DIR}（run_id 必须全新）" >&2
  exit 3
fi
mkdir -p "$RUN_DIR"/{process,raw,events,fuse,summary}

# 预检（PRECHECKED）：目标不可达不开跑
if ! precheck_target "$BASE_URL_VALUE"; then
  append_event "$RUN_DIR/events/coordinator.ndjson" coordinator "$RUN_ID" precheck_failed \
    "base_url=$BASE_URL_VALUE"
  echo "错误：$BASE_URL_VALUE/health 不可达，未开跑（事件已记录）" >&2
  exit 4
fi

# 网关链路（云端默认，lab.sh run 注入 GATEWAY_URL/GATEWAY_ORIGIN；GATEWAY=0 回退直连）：
# 业务流量经网关内网回环（真实用户链路），控制面（本文件 precheck/resolve_slo 与各 monitor）
# 仍直连 BASE_URL。凭据从控制机状态盘文件读取（lab.sh 同步，0600），环境变量已设置时不覆盖；
# 口令不进命令行参数、manifest 与事件。
GATEWAY_SECRETS_FILE="${GATEWAY_SECRETS_FILE:-<run-root>"
if [ -n "${GATEWAY_URL:-}" ] && [ -z "${GATEWAY_USERNAME:-}" ]; then
  if [ ! -f "$GATEWAY_SECRETS_FILE" ]; then
    echo "错误：GATEWAY_URL 已设置但凭据文件缺失：$GATEWAY_SECRETS_FILE" >&2
    exit 6
  fi
  GATEWAY_USERNAME="$(grep -E '^GATEWAY_USERNAME=' "$GATEWAY_SECRETS_FILE" | head -1 | cut -d= -f2-)"
  GATEWAY_PASSWORD="$(grep -E '^GATEWAY_PASSWORD=' "$GATEWAY_SECRETS_FILE" | head -1 | cut -d= -f2-)"
  export GATEWAY_USERNAME GATEWAY_PASSWORD
fi
GW_ARGS=()
K6_TLS_ARGS=()
GATEWAY_URL_VALUE=""
GATEWAY_ORIGIN_VALUE=""
GATEWAY_USERNAME_VALUE=""
if [ -n "${GATEWAY_URL:-}" ]; then
  : "${GATEWAY_ORIGIN:?GATEWAY_URL 已设置，须同时提供 GATEWAY_ORIGIN}"
  : "${GATEWAY_USERNAME:?GATEWAY_URL 已设置但凭据缺失（GATEWAY_SECRETS_FILE=$GATEWAY_SECRETS_FILE）}"
  : "${GATEWAY_PASSWORD:?GATEWAY_URL 已设置但凭据缺失}"
  # 开跑前网关登录预检：凭据/ACL/证书任一不可用都不进入压测（口令经 stdin，不进 ps）
  GW_PRECHECK_CODE="$(printf '{"username":"%s","password":"%s"}' "$GATEWAY_USERNAME" "$GATEWAY_PASSWORD" \
    | curl -k -s -o /dev/null -m 10 -w '%{http_code}' -X POST \
      -H "Origin: ${GATEWAY_ORIGIN}" -H 'Content-Type: application/json' \
      --data-binary @- "${GATEWAY_URL}/auth/login" 2>/dev/null || echo 000)"
  if [ "$GW_PRECHECK_CODE" != "204" ]; then
    append_event "$RUN_DIR/events/coordinator.ndjson" coordinator "$RUN_ID" gateway_precheck_failed \
      "http_code=$GW_PRECHECK_CODE" "url=$GATEWAY_URL"
    echo "错误：网关登录预检失败（HTTP ${GW_PRECHECK_CODE}，URL ${GATEWAY_URL}）；401=凭据错 403=Origin/ACL 拒绝 429=限速 000=不可达" >&2
    exit 6
  fi
  GATEWAY_URL_VALUE="$GATEWAY_URL"
  GATEWAY_ORIGIN_VALUE="$GATEWAY_ORIGIN"
  GATEWAY_USERNAME_VALUE="$GATEWAY_USERNAME"
  GW_ARGS=(
    -e "GATEWAY_URL=$GATEWAY_URL"
    -e "GATEWAY_ORIGIN=$GATEWAY_ORIGIN"
    -e "GATEWAY_USERNAME=$GATEWAY_USERNAME"
    -e "GATEWAY_PASSWORD=$GATEWAY_PASSWORD"
  )
  # TLS 主机名跳过仅因内网地址与证书 SAN（公网 IP）不一致，属实验室内网闭环口径
  K6_TLS_ARGS=(--insecure-skip-tls-verify)
  append_event "$RUN_DIR/events/coordinator.ndjson" coordinator "$RUN_ID" gateway_precheck_ok \
    "url=$GATEWAY_URL" "username=$GATEWAY_USERNAME"
fi

SLO_VALUE="$(resolve_slo "$BASE_URL_VALUE" "$STAGE_ID" "$(stage_default_slo "$STAGE_ID")")"
# 首字与计价口径（B2）：环境显式值 > /version 指纹（真实模型轮优先 slo_real_*）> 阶段默认；
# 首字为空串表示本阶段不设门槛（阶段一/二），manifest 记 null、不传给 k6。
FIRST_ANSWER_SLO_VALUE="$(resolve_first_answer_slo "$BASE_URL_VALUE" "$STAGE_ID" "$(stage_default_first_answer_slo "$STAGE_ID")")"
COST_PER_1K_VALUE="$(resolve_cost_per_1k "$BASE_URL_VALUE" "$STAGE_ID" "$(stage_default_cost_per_1k "$STAGE_ID")")"
# 首字/计价显式 -e 透传（B2）：不依赖 k6 隐式继承进程环境；首字为空（阶段一/二不设门槛）时不传。
CALIBER_ARGS=()
if [ -n "$FIRST_ANSWER_SLO_VALUE" ]; then
  CALIBER_ARGS+=(-e "FIRST_ANSWER_SLO_MS=$FIRST_ANSWER_SLO_VALUE")
fi
CALIBER_ARGS+=(-e "COST_PER_1K=$COST_PER_1K_VALUE")
# 观测段取证（S2-U1）：manifest 记录当轮 Langfuse 上报开关（/version observability），
# 失败留空由 manifest 记 null（下游按从严判读，不冒充已知）
TARGET_VERSION_RAW="$(curl -fsS -m 5 "$BASE_URL_VALUE/version" 2>/dev/null || true)"
export TARGET_VERSION_RAW
GIT_HEAD_VALUE="$(git -C "$CASE_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
K6_VERSION_VALUE="$(k6 version 2>/dev/null | head -1 || echo unknown)"
TICKETS_PATH="${TICKETS:-$CASE_DIR/testing/test-data/tickets.json}"
DATA_PROFILE_MANIFEST=""
TARGET_VERSION_JSON=""
if [ -n "${DATA_PROFILE_DIR:-}" ]; then
  # 校验器与版本核对（007 整改）：schema、数据版本、Mock 版本三方一致才继续，
  # 未知 schema 立即拒绝；校验器从脚本自身推导的案例根定位，不依赖调用方 cwd。
  python3 - "$DATA_PROFILE_DIR" "$CASE_DIR" <<'PICK'
import json, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
case_root = pathlib.Path(sys.argv[2])
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
schema = manifest.get("schema_version")
# schema -> (校验器目录, 数据集版本, Mock 规则版本)：唯一合法映射，隐式字段一并核对
KNOWN_SCHEMAS = {
    "agent-performance-data-v1": ("performance-v1", "v1", "legacy"),
    "agent-performance-data-v1.1": ("performance-v1.1", "v1.1", "performance-v1.1"),
}
if schema not in KNOWN_SCHEMAS:
    raise SystemExit(f"未知数据档 schema_version：{schema!r}，拒绝开跑")
version_dir, dataset_version, mock_rule = KNOWN_SCHEMAS[schema]
declared_dataset = manifest.get("dataset_version") or dataset_version
if declared_dataset != dataset_version:
    raise SystemExit(
        "数据档 schema 与 dataset_version 不一致："
        + repr(schema) + " 声明 " + repr(declared_dataset))
declared_mock = manifest.get("mock_rule_version") or mock_rule
if declared_mock != mock_rule:
    raise SystemExit(
        "数据档 schema 与 mock_rule_version 不一致："
        + repr(schema) + " 声明 " + repr(declared_mock))
validator = case_root / "testing/test-data" / version_dir / "validate.py"
if not validator.exists():
    raise SystemExit(f"数据档校验器不存在：{validator}（拒绝开跑）")
raise SystemExit(subprocess.call([sys.executable, str(validator), "--data-dir", str(root)]))
PICK
  DATA_PROFILE_MANIFEST="$DATA_PROFILE_DIR/manifest.json"
  TARGET_VERSION_JSON="$(curl -fsS -m 5 "$BASE_URL_VALUE/version")" || {
    echo "错误：选择性能数据档时须获取目标 /version 指纹" >&2
    exit 6
  }
  export TARGET_VERSION_JSON
  python3 - "$DATA_PROFILE_MANIFEST" "$TICKETS_PATH" "$STAGE_ID" <<'PY'
import hashlib, json, os, pathlib, sys
manifest_path, tickets_path = map(pathlib.Path, sys.argv[1:3])
stage_id = sys.argv[3]
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
version = json.loads(os.environ["TARGET_VERSION_JSON"])


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_present(mapping, keys):
    """按候选键取第一个非空值（派生清单键名容错；主键见方案 §7.4 派生数据档契约）。"""
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", []):
            return value
    return None


try:
    rel = str(tickets_path.resolve().relative_to(manifest_path.parent.resolve()))
except ValueError as exc:
    raise SystemExit("TICKETS 必须指向所选 DATA_PROFILE_DIR 下的文件") from exc

subset_name = f"subset-stage{stage_id}.json"
is_subset = (rel == subset_name)
if is_subset:
    # 派生公共子集档（方案 §7.4）：子集文件只存工单数组，派生清单记录父档指纹、
    # 子集 SHA-256、条数与选中顺序；任一项不符或缺失一律拒绝开跑（禁止绕过校验）。
    subset_path = tickets_path.resolve()
    derived_path = subset_path.parent / f"subset-stage{stage_id}.manifest.json"
    if not derived_path.exists():
        raise SystemExit("公共子集档缺少派生清单：" + derived_path.name + "（须与子集文件同目录）")
    try:
        derived = json.loads(derived_path.read_text(encoding="utf-8"))
        subset_tickets = json.loads(subset_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit("公共子集档或派生清单不是合法 JSON：" + repr(exc)) from exc
    if not isinstance(derived, dict) or not isinstance(subset_tickets, list):
        raise SystemExit("公共子集派生清单须为对象、子集文件须为工单数组")
    declared_stage = derived.get("stage")
    if declared_stage is not None and str(declared_stage) != stage_id:
        raise SystemExit("公共子集派生清单 stage 与运行阶段不一致：" + repr(declared_stage))
    declared_count = first_present(derived, ("count", "ticket_count", "num_tickets", "sample_count"))
    if declared_count is None:
        raise SystemExit("公共子集派生清单缺少条数（count）")
    try:
        declared_count = int(declared_count)
    except (TypeError, ValueError):
        raise SystemExit("公共子集派生清单条数字段不可解析：" + repr(declared_count))
    if declared_count != len(subset_tickets):
        raise SystemExit(
            "公共子集条数与清单不符：清单 " + repr(declared_count) + "，文件 " + str(len(subset_tickets)))
    declared_sha = first_present(derived, ("subset_sha256", "subset_file_sha256", "sha256", "file_sha256"))
    if not declared_sha:
        raise SystemExit("公共子集派生清单缺少子集 SHA-256（subset_sha256）")
    actual_sha = sha256_file(subset_path)
    if str(declared_sha) != actual_sha:
        raise SystemExit("公共子集 SHA-256 与派生清单不符：" + actual_sha + " != " + str(declared_sha))
    parent_sha = first_present(derived, ("parent_manifest_sha256", "parent_profile_sha256", "parent_sha256"))
    if not parent_sha:
        raise SystemExit("公共子集派生清单缺少父档指纹（parent_manifest_sha256）")
    actual_parent_sha = sha256_file(manifest_path)
    if str(parent_sha) != actual_parent_sha:
        raise SystemExit(
            "公共子集父档指纹与当前数据档清单不符（父档已变更，须重新派生）："
            + str(parent_sha) + " != " + actual_parent_sha)
    declared_subset_file = first_present(derived, ("subset_file", "subset_filename", "file"))
    if declared_subset_file and str(declared_subset_file) != subset_path.name:
        raise SystemExit("公共子集派生清单登记的文件名与 TICKETS 不一致：" + str(declared_subset_file))
    ticket_ids = first_present(derived, ("ticket_ids", "tickets", "ids", "selected_ticket_ids"))
    if ticket_ids is not None:
        subset_ids = [str(t.get("ticket_id")) for t in subset_tickets if isinstance(t, dict)]
        if [str(v) for v in ticket_ids] != subset_ids:
            raise SystemExit("公共子集派生清单的 ticket_ids 顺序与子集文件不一致")
    parent_tickets_name = derived.get("parent_tickets_file")
    parent_tickets_sha = derived.get("parent_tickets_sha256")
    if parent_tickets_name and parent_tickets_sha:
        parent_tickets = subset_path.parent / str(parent_tickets_name)
        if not parent_tickets.exists() or sha256_file(parent_tickets) != str(parent_tickets_sha):
            raise SystemExit("公共子集的父档工单文件缺失或 SHA-256 不符：" + str(parent_tickets_name))
elif rel not in (f"golden-stage{stage_id}.json", f"load-stage{stage_id}.json",
                 f"fixed-stage{stage_id}.json") and not (
            rel.startswith("fault") and rel.endswith(f"-stage{stage_id}.json")):
    raise SystemExit("TICKETS 的阶段与运行阶段不一致")
if not is_subset and rel not in manifest["files_sha256"]:
    raise SystemExit("TICKETS 未被数据档清单收录")
declared_mock = manifest.get("mock_rule_version")
target_mock = version.get("mock_rule_version")
if declared_mock and declared_mock != target_mock:
    raise SystemExit(
        "数据档声明的 Mock 规则版本与目标 /version 不符："
        + repr(declared_mock) + " != " + repr(target_mock))
expected_mock = os.environ.get("EXPECTED_MOCK_RULE_VERSION") or ""
if expected_mock and target_mock != expected_mock:
    raise SystemExit(
        "目标 /version 的 Mock 规则版本与本轮设定不符："
        + repr(target_mock) + " != " + repr(expected_mock))
# 数据版本三方核对（007 整改）：v1 档无显式 dataset_version 时按 schema 隐式 v1
declared_dataset = manifest.get("dataset_version") or "v1"
expected_dataset = os.environ.get("EXPECTED_DATASET_VERSION") or ""
if expected_dataset and declared_dataset != expected_dataset:
    raise SystemExit(
        "数据档 dataset_version 与本轮 --dataset-version 不符（混用版本拒绝开跑）："
        + repr(declared_dataset) + " != " + repr(expected_dataset))
for rel in ("orders.json", "logistics.json", "knowledge-base/faq.json",
            "knowledge-base/policies.json", "knowledge-base/products.json"):
    actual = (version.get("file_hashes") or {}).get("test-data/" + rel)
    if actual != manifest["files_sha256"][rel]:
        raise SystemExit("目标 /version 数据指纹与所选档不符：" + rel
                         + "；先在本机执行 lab.sh switch-data --dataset-version <v> --profile <档名> 切档后再 run")
if stage_id == "3":
    actual = (version.get("protocol_mode") or {}).get("rag_index_version")
    if actual != manifest["expected_rag_index_version"]:
        raise SystemExit("Workflow 运行时知识索引版本与所选档不符")
PY
fi
export DATA_PROFILE_MANIFEST TICKETS_PATH TARGET_VERSION_JSON
if [ -n "$DATA_PROFILE_MANIFEST" ]; then
  cp "$DATA_PROFILE_MANIFEST" "$RUN_DIR/data-profile-manifest.json"
  if ! cmp -s "$DATA_PROFILE_MANIFEST" "$RUN_DIR/data-profile-manifest.json"; then
    echo "错误：数据档清单在预检后发生变化" >&2
    exit 6
  fi
fi
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

RW_ARGS=()
if [ -n "${K6_PROMETHEUS_RW_SERVER_URL:-}" ]; then
  export K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(90),p(95),avg,med}"
  export K6_PROMETHEUS_RW_STALE_MARKERS="${K6_PROMETHEUS_RW_STALE_MARKERS:-true}"
  RW_ARGS=(--out experimental-prometheus-rw --tag "stage=$STAGE_ID" --tag "run_id=$RUN_ID")
fi

# 透传参数里的 WARMUP（b0 预热计划轮数；冷启动 = 0）；
# WARMUP_SEC（s1 时间型预热秒数，P2/R2a：预热段打 phase=warmup 且不入计量窗，
# 供预热隔离对照轮使用；缺省 0 保持「整场即计量窗」契约原状）
WARMUP_PLANNED="${WARMUP:-3}"
SOAK_PLANNED="${SOAK_MINUTES:-2}"
WARMUP_SEC_PLANNED="${WARMUP_SEC:-0}"
_prev=""
for _a in "$@"; do
  case "$_prev" in
    -e) case "$_a" in
      WARMUP=*) WARMUP_PLANNED="${_a#WARMUP=}" ;;
      SOAK_MINUTES=*) SOAK_PLANNED="${_a#SOAK_MINUTES=}" ;;
      WARMUP_SEC=*) WARMUP_SEC_PLANNED="${_a#WARMUP_SEC=}" ;;
    esac ;;
  esac
  _prev="$_a"
done
if [[ ! "$SOAK_PLANNED" =~ ^[0-9]+$ ]] || [ "$SOAK_PLANNED" -gt 720 ]; then
  echo "错误：SOAK_MINUTES 须为 0 至 720 的整数" >&2
  exit 2
fi
if [[ ! "$WARMUP_SEC_PLANNED" =~ ^[0-9]+$ ]] || [ "$WARMUP_SEC_PLANNED" -gt 3600 ]; then
  echo "错误：WARMUP_SEC 须为 0 至 3600 的整数（s1 时间型预热秒数）" >&2
  exit 2
fi
export WARMUP_SEC="$WARMUP_SEC_PLANNED"
export RUN_MONITOR_SECONDS=$(( SOAK_PLANNED * 60 + 600 ))
if [ "$RUN_MONITOR_SECONDS" -lt 14400 ]; then export RUN_MONITOR_SECONDS=14400; fi

# 链路口径（A4）：gateway=业务经网关内网回环，direct=业务直连被测服务；
# 与 target.gateway_url 同源判定，写入 manifest 供报告层区分「真实用户链路」与「直连口径」。
LINK_MODE_VALUE="direct"
if [ -n "${GATEWAY_URL:-}" ]; then LINK_MODE_VALUE="gateway"; fi
export LINK_MODE_VALUE

# 观测开关（A4）：OBS_LANGFUSE=0/1 显式登记当轮 Langfuse 上报开关；
# 未设置时沿用 /version observability.langfuse_enabled 探测结果（保持既有行为不变）。
OBS_LANGFUSE_VALUE="${OBS_LANGFUSE:-}"
case "$OBS_LANGFUSE_VALUE" in
  "") ;;
  0|1) ;;
  *) echo "错误：OBS_LANGFUSE 须为 0 或 1（当前：$OBS_LANGFUSE_VALUE）" >&2; exit 2 ;;
esac
export OBS_LANGFUSE_VALUE

# 故障注入预期（B13/A4）：窗口包登记的目标/时段/预期信号/判定边界 JSON；
# 支持环境变量 INJECTION_EXPECTATION 直接传 JSON 或 INJECTION_EXPECTATION_FILE 读文件，
# 缺省 null（非注入窗不登记）。开跑前做 JSON 合法性校验，非法即拒绝开跑。
INJECTION_EXPECTATION_JSON=""
if [ -n "${INJECTION_EXPECTATION:-}" ] && [ -n "${INJECTION_EXPECTATION_FILE:-}" ]; then
  echo "错误：INJECTION_EXPECTATION 与 INJECTION_EXPECTATION_FILE 只能二选一" >&2
  exit 2
fi
if [ -n "${INJECTION_EXPECTATION_FILE:-}" ]; then
  if [ ! -f "$INJECTION_EXPECTATION_FILE" ]; then
    echo "错误：INJECTION_EXPECTATION_FILE 指向的文件不存在：$INJECTION_EXPECTATION_FILE" >&2
    exit 2
  fi
  INJECTION_EXPECTATION_JSON="$(cat "$INJECTION_EXPECTATION_FILE")"
elif [ -n "${INJECTION_EXPECTATION:-}" ]; then
  INJECTION_EXPECTATION_JSON="$INJECTION_EXPECTATION"
fi
if [ -n "$INJECTION_EXPECTATION_JSON" ]; then
  if ! printf '%s' "$INJECTION_EXPECTATION_JSON" | python3 -c 'import json, sys; json.load(sys.stdin)' 2>/dev/null; then
    echo "错误：injection_expectation 不是合法 JSON（环境变量或文件内容）" >&2
    exit 2
  fi
fi
export INJECTION_EXPECTATION_JSON

# manifest.json：启动前原子生成（先写临时文件再 mv）
MANIFEST_TMP="$RUN_DIR/manifest.json.tmp"
python3 - "$MANIFEST_TMP" "$WARMUP_PLANNED" "$*" <<PY
import hashlib, json, os, pathlib, sys
manifest_path, warmup_planned, extra_args = sys.argv[1], sys.argv[2], sys.argv[3]
warmup_planned = int(warmup_planned) if warmup_planned not in ("", None) else 3
_raw = os.environ.get("TARGET_VERSION_RAW") or ""
_langfuse_enabled = None
if _raw:
    try:
        _langfuse_enabled = bool((json.loads(_raw).get("observability") or {}).get("langfuse_enabled"))
    except Exception:
        _langfuse_enabled = None
# A4：OBS_LANGFUSE 显式值优先（0/1），缺省沿用 /version 探测结果（null 表示未知，下游从严判读）
_obs_env = os.environ.get("OBS_LANGFUSE_VALUE") or ""
_obs_enabled = _langfuse_enabled
if _obs_env in ("0", "1"):
    _obs_enabled = (_obs_env == "1")
# A4：故障注入预期 JSON（环境变量或文件），缺省 null；shell 侧已做 JSON 合法性校验
_injection_raw = os.environ.get("INJECTION_EXPECTATION_JSON") or ""
_injection_expectation = json.loads(_injection_raw) if _injection_raw else None
# 首字门槛为空表示本阶段不设门槛（阶段一/二），manifest 记 null
_first_answer_slo = "$FIRST_ANSWER_SLO_VALUE"
manifest = {
    "observation": {"langfuse_enabled": _langfuse_enabled, "obs_enabled": _obs_enabled},
    "link_mode": os.environ.get("LINK_MODE_VALUE") or "direct",
    "injection_expectation": _injection_expectation,
    "contract_version": "$CONTRACT_VERSION",
    "run_id": "$RUN_ID",
    "scenario": "$SCENARIO",
    "mode": "$MODE",
    "baseline_commit": "$GIT_HEAD_VALUE",
    "started_at": "$STARTED_AT",
    "target": {"base_url": "$BASE_URL_VALUE", "stage": "$STAGE_ID",
               "gateway_url": "$GATEWAY_URL_VALUE", "gateway_origin": "$GATEWAY_ORIGIN_VALUE",
               "gateway_username": "$GATEWAY_USERNAME_VALUE"},
    "process": {
        "k6_pid_file": "process/k6.pid",
        "stop_confirmation_file": "process/k6-stop.json",
    },
    "windows": {
        "warmup": None if warmup_planned == 0 else {"planned_iterations": warmup_planned},
        "warmup_seconds": int("$WARMUP_SEC_PLANNED"),
        "note": "planned structure only; actual ISO windows are recorded by k6 boundary gauges into raw/k6-summary.json and aggregated in summary/run.json",
    },
    "thresholds": {
        "qualified_rate_min": 0.90,
        "error_rate_max": 0.005,
        "e2e_slo_ms": float("$SLO_VALUE"),
        "first_answer_slo_ms": (None if _first_answer_slo == "" else float(_first_answer_slo)),
        "cost_per_1k": float("$COST_PER_1K_VALUE"),
    },
    "required_safety_evidence": [
        "events/environment.ndjson",
        "events/business.ndjson",
    ],
    "parameters": {"k6_extra_args": extra_args},
    "artifact_root": "$RUN_DIR",
}
driver_root = pathlib.Path("$CASE_DIR") / "testing/k6-suite"
driver_files = [driver_root / "orchestrator/run-isolated.sh",
                pathlib.Path("$STAGE_DIR") / "tests/performance/k6/$SCRIPT"]
driver_files += sorted((driver_root / "lib").glob("*.js"))
manifest["load_driver_sha256"] = {
    str(path.relative_to(pathlib.Path("$CASE_DIR"))): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in driver_files
}
profile_path = os.environ.get("DATA_PROFILE_MANIFEST")
if profile_path:
    profile_path = pathlib.Path(profile_path)
    profile_copy = pathlib.Path(manifest_path).parent / "data-profile-manifest.json"
    profile = json.loads(profile_copy.read_text(encoding="utf-8"))
    tickets = pathlib.Path(os.environ["TICKETS_PATH"]).resolve()
    ticket_rel = str(tickets.relative_to(profile_path.parent.resolve()))
    # 派生公共子集（A4）：子集文件与派生清单一并登记；父档清单未收录子集文件，
    # tickets_sha256 按子集文件原始字节现算，父档关系与子集清单一并复制进运行目录。
    if ticket_rel in profile["files_sha256"]:
        tickets_sha = profile["files_sha256"][ticket_rel]
    else:
        tickets_sha = hashlib.sha256(tickets.read_bytes()).hexdigest()
    manifest["data"] = {
        "profile_id": profile["profile_id"],
        "profile_manifest_artifact": "data-profile-manifest.json",
        "profile_sha256": hashlib.sha256(profile_copy.read_bytes()).hexdigest(),
        "tickets_file": ticket_rel,
        "tickets_sha256": tickets_sha,
        "rule_version": profile["rule_version"],
        "mock_rule_version": profile.get("mock_rule_version"),
        "dataset_version": profile.get("dataset_version", "v1"),
        "mix": profile["mix"], "access": profile["access"],
        "physical_counts": profile["counts"],
        "expected_rag_index_version": profile["expected_rag_index_version"],
        "declared_file_fingerprints_verified": True,
        "runtime_load_verified": False,
    }
    subset_manifest = tickets.parent / (tickets.stem + ".manifest.json")
    if ticket_rel.startswith("subset-stage") and subset_manifest.exists():
        subset_copy = pathlib.Path(manifest_path).parent / "data-subset-manifest.json"
        subset_copy.write_bytes(subset_manifest.read_bytes())
        subset_meta = json.loads(subset_manifest.read_text(encoding="utf-8"))
        manifest["data"]["subset"] = {
            "derived_manifest_artifact": "data-subset-manifest.json",
            "derived_manifest_sha256": hashlib.sha256(subset_copy.read_bytes()).hexdigest(),
            "parent_manifest_sha256": subset_meta.get("parent_manifest_sha256"),
            "parent_tickets_file": subset_meta.get("parent_tickets_file"),
            "count": subset_meta.get("count"),
            "ticket_ids_sha256": hashlib.sha256(
                json.dumps(subset_meta.get("ticket_ids") or [], ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        }
    version = json.loads(os.environ["TARGET_VERSION_JSON"])
    manifest["target_version"] = {
        "fingerprint": version.get("fingerprint"),
        "provider": version.get("provider"),
        "model": version.get("model"),
        "mock_rule_version": version.get("mock_rule_version"),
        "protocol_mode": version.get("protocol_mode"),
        "effective_limits": version.get("effective_limits"),
    }
json.dump(manifest, open(manifest_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
mv "$MANIFEST_TMP" "$RUN_DIR/manifest.json"

append_event "$RUN_DIR/events/coordinator.ndjson" coordinator "$RUN_ID" run_created \
  "base_url=$BASE_URL_VALUE" "scenario=$SCENARIO" "contract_version=$CONTRACT_VERSION"

# 发压（k6 直接子进程：$! 即真实 k6 PID，不经包装进程）
# --summary-trend-stats：摘要补 p(99)/min/max（D2：run.json 与逐档 levels 的
#   e2e p99 不再缺失；同时作用于全部 Trend 指标，摘要体积略增，可接受）；
# -e EMIT_JUDGEMENT=1 + --console-output：逐笔四级判定与要素明细落
#   raw/judgements.ndjson（B15/B3：逐笔身份按 ticket_id+ts 对账，不经 k6 tags
#   进时序库，规避 task_id 高基数 series；解析端按 event=ticket_judged 过滤）
cd "$STAGE_DIR"
set +e
k6 run \
  --out "json=$RUN_DIR/raw/k6-samples.ndjson" \
  --summary-trend-stats "avg,min,med,max,p(90),p(95),p(99)" \
  --console-output "$RUN_DIR/raw/judgements.ndjson" \
  -e "STAGE=$STAGE_ID" \
  -e "BASE_URL=$BASE_URL_VALUE" \
  -e "RUN_ID=$RUN_ID" \
  -e "OUT_PREFIX=k6" \
  -e "OUT_DIR=$RUN_DIR/raw" \
  -e "TICKETS=$TICKETS_PATH" \
  -e "GIT_HEAD=$GIT_HEAD_VALUE" \
  -e "K6_VERSION=$K6_VERSION_VALUE" \
  -e "CALIBER_SOURCE=orchestrator" \
  -e "SLO_MS=$SLO_VALUE" \
  "${CALIBER_ARGS[@]+"${CALIBER_ARGS[@]}"}" \
  -e "MANIFEST=1" \
  -e "EMIT_JUDGEMENT=1" \
  "${RW_ARGS[@]+"${RW_ARGS[@]}"}" \
  "${GW_ARGS[@]+"${GW_ARGS[@]}"}" \
  "${K6_TLS_ARGS[@]+"${K6_TLS_ARGS[@]}"}" \
  "$@" \
  "tests/performance/k6/$SCRIPT" &
K6_PID=$!
set -e

# 真实 PID 记录与身份校验（C2）
sleep 1
if ! pid_is_k6 "$K6_PID"; then
  append_event "$RUN_DIR/events/coordinator.ndjson" coordinator "$RUN_ID" pid_identity_check_failed \
    "pid=$K6_PID"
  echo "错误：PID $K6_PID 身份校验失败（进程不是 k6）" >&2
  kill "$K6_PID" 2>/dev/null || true
  exit 5
fi
echo "$K6_PID" > "$RUN_DIR/process/k6.pid"
append_event "$RUN_DIR/events/load.ndjson" load "$RUN_ID" k6_started \
  "pid=$K6_PID"

# 角色：环境监控（≤15s）、业务监控（≤10s）、主协调状态心跳（≤30s，契约 §7）、
#       熔断控制器（规则评估）与熔断看护（消费事件停进程）
"$ORCH_DIR/monitor-env.sh"     "$RUN_DIR" "$BASE_URL_VALUE" "$K6_PID" &
ENV_PID=$!
"$ORCH_DIR/monitor-biz.sh"     "$RUN_DIR" "$BASE_URL_VALUE" "$K6_PID" &
BIZ_PID=$!
"$ORCH_DIR/fuse-controller.sh" "$RUN_DIR" "$K6_PID" &
CTRL_PID=$!
"$ORCH_DIR/watch-fuse.sh"      "$RUN_DIR" "$K6_PID" &
WATCH_PID=$!
(
  # 主协调状态汇总：运行期间每 30 秒一条心跳（随 k6 退出自动停止）
  while kill -0 "$K6_PID" 2>/dev/null; do
    append_event "$RUN_DIR/events/coordinator.ndjson" coordinator "$RUN_ID" status_sample "k6_alive=true"
    sleep 30
  done
) &
HEART_PID=$!

append_event "$RUN_DIR/events/coordinator.ndjson" coordinator "$RUN_ID" run_running \
  "pid=$K6_PID" "monitors=env:$ENV_PID,biz:$BIZ_PID,ctrl:$CTRL_PID,watch:$WATCH_PID,heart:$HEART_PID"

# 等待发压结束（正常或被熔断停止）；角色进程自行随 k6 退出
set +e
wait "$K6_PID"
K6_RC=$?
set -e

# 收尾：等角色落地最后一轮，聚合并生成 run.json 与 hashes.sha256
sleep 2
kill "$ENV_PID" "$BIZ_PID" "$CTRL_PID" "$WATCH_PID" "$HEART_PID" 2>/dev/null || true
wait "$ENV_PID" "$BIZ_PID" "$CTRL_PID" "$WATCH_PID" "$HEART_PID" 2>/dev/null || true

append_event "$RUN_DIR/events/coordinator.ndjson" coordinator "$RUN_ID" run_finished \
  "k6_exit_code=$K6_RC"

python3 "$ORCH_DIR/verify-run.py" "$RUN_DIR" || true

echo "---"
echo "运行目录：$RUN_DIR"
echo "k6 退出码：${K6_RC}（熔断停止为 143/137 等；判定看 summary/run.json 的 gate）"
echo "判定：$(cat "$RUN_DIR/summary/run.json" 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["gate"]["verdict"], d["gate"]["reason_codes"])' 2>/dev/null || echo 'verify-run 未产出判定')"
exit "$K6_RC"
