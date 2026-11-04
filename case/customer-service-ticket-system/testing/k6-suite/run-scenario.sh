#!/usr/bin/env bash
# k6 场景执行封装（客服工单系统 · 三阶段共用）
#
# 用法：
#   testing/k6-suite/run-scenario.sh <1|2|3> <b0|c1|s1> [k6 额外参数...]
#   各阶段薄入口同签名：<阶段目录>/tests/performance/k6/run-scenario.sh <b0|c1|s1>
#
# 风险与前提（务必先读）：
#   1. 本脚本会对本机被测服务发起压测流量：B0 全量 245 笔约 10—20 分钟，
#      C1 默认 6 档约 6 分钟，S1 默认 20VU × 2 分钟（缩短版）。
#   2. 仅在 LLM_PROVIDER=mock 的本地/实验室环境运行，且已确认目标阶段服务已启动
#      （阶段一 8000 / 阶段二 8001 / 阶段三 8002；压测只读业务快照）。
#   3. 同机压测会与被测服务争用 CPU/内存，结论须标注「同机执行」（方案 §7-4）。
#
# 行为：由脚本自身位置推导案例根 → 按阶段号定位阶段目录 → 读 /version 指纹注入判定口径 →
#   建 out/ → 跑该阶段薄入口脚本 → 打印产物清单。产物（out/ 已 gitignore，不入库）：
#   <前缀>.ndjson（逐笔采样）、<前缀>-summary.html（判定表）、<前缀>-summary.json（机器可读看板）。
set -euo pipefail

STAGE_ARG="${1:-}"
SCENARIO="${2:-}"
case "$STAGE_ARG" in
  1) STAGE_ID="1"; STAGE_DIR_NAME="app/single-agent"; DEFAULT_PORT=8000 ;;
  2) STAGE_ID="2"; STAGE_DIR_NAME="app/multi-agent"; DEFAULT_PORT=8001 ;;
  3) STAGE_ID="3"; STAGE_DIR_NAME="app/workflow-flow-rag"; DEFAULT_PORT=8002 ;;
  *)
    echo "用法：$(basename "$0") <1|2|3> <b0|c1|s1> [k6 额外参数...]" >&2
    echo "  阶段一 8000 / 阶段二 8001 / 阶段三 8002（BASE_URL 可覆盖）" >&2
    exit 2
    ;;
esac
case "$SCENARIO" in
  b0) SCRIPT="b0-baseline.js" ;;
  c1) SCRIPT="c1-capacity.js" ;;
  s1) SCRIPT="s1-stability.js" ;;
  s1-ramp) SCRIPT="s1-ramp.js" ;;
  e1) SCRIPT="e1-traffic-change.js" ;;
  e2) SCRIPT="e2-multiturn.js" ;;
  calib) SCRIPT="calib.js" ;;
  *)
    echo "用法：$(basename "$0") <1|2|3> <b0|c1|s1|e1|e2|calib> [k6 额外参数...]" >&2
    echo "  b0 基准（工单池全量轮换，1VU；冷启动独立运行用 -e WARMUP=0）" >&2
    echo "  c1 容量阶梯（默认 1,5,10,20,30,50 × 3min/档 = 1min 稳定 + 2min 计量）" >&2
    echo "  s1 长稳（默认 20VU × 2min 缩短版）" >&2
  echo "  s1-ramp 连续递增长稳（-e RAMP_VUS=10 -e RAMP_MINUTES=10 -e RAMP_STEP_SEC=1：每秒 +1 VU 递增至 10 后保持共 10min；-e SCENARIO_NAME=<名> 自定场景名，进看板 scenario 变量，多案例对比时各设不同名）" >&2
    echo "  e1 流量变化（-e PATTERN=spike|drop，独立运行）" >&2
    echo "  e2 多轮上下文（-e TURNS=5，逐轮判定）" >&2
    echo "  calib 校准（1VU × -e COUNT=20 笔，输出 P50/P90/P95/P99 与 token 摘要，不参与门禁）" >&2
    exit 2
    ;;
esac
shift 2 || true

# 由脚本自身位置推导路径（不依赖调用时的 cwd）
SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CASE_DIR="$(cd "$SUITE_DIR/../.." && pwd)"
STAGE_DIR="$CASE_DIR/$STAGE_DIR_NAME"
OUT_DIR_ABS="$STAGE_DIR/out"
mkdir -p "$OUT_DIR_ABS"   # k6 的 handleSummary 不会自建目录

BASE_URL_VALUE="${BASE_URL:-http://127.0.0.1:$DEFAULT_PORT}"
TS="$(date +%Y%m%d-%H%M%S)"
PREFIX="${OUT_PREFIX:-stage${STAGE_ID}-$SCENARIO-$TS}"
RUN_ID_VALUE="${RUN_ID:-k6-stage$STAGE_ID-$TS}"
TICKETS_PATH="${TICKETS:-$CASE_DIR/testing/test-data/tickets.json}"
GIT_HEAD_VALUE="$(git -C "$CASE_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
K6_VERSION_VALUE="$(k6 version 2>/dev/null | head -1 || echo unknown)"

# 可选观测：K6_PROMETHEUS_RW_SERVER_URL 非空时，k6 指标直推 Prometheus
# remote write（监控栈 9190 已开启接收；配合 Grafana「k6 压测」看板实时呈现，
# 接入说明见 k6-suite/README.md）。缺省关闭：不设该变量时行为与产物不变。
RW_ARGS=()
if [ -n "${K6_PROMETHEUS_RW_SERVER_URL:-}" ]; then
  export K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(90),p(95),avg,med}"
  export K6_PROMETHEUS_RW_STALE_MARKERS="${K6_PROMETHEUS_RW_STALE_MARKERS:-true}"
  RW_ARGS=(
    --out experimental-prometheus-rw
    --tag "stage=$STAGE_ID"
    --tag "run_id=$RUN_ID_VALUE"
  )
fi

# 可选链路：GATEWAY_URL 非空时业务流量经公网网关「内网回环」（真实用户链路：
# 网关→前端→Agent），控制面（上方 /version 指纹探测）仍直连 BASE_URL。
# TLS 主机名跳过仅因内网地址与证书 SAN（公网 IP）不一致，属实验室内网闭环口径；
# Origin 仍按认证服务强校验填 https://<公网IP>。凭据经环境变量注入，不入产物与日志。
GW_ARGS=()
K6_TLS_ARGS=()
if [ -n "${GATEWAY_URL:-}" ]; then
  : "${GATEWAY_ORIGIN:?GATEWAY_URL 已设置，须同时提供 GATEWAY_ORIGIN（https://<公网IP>）}"
  : "${GATEWAY_USERNAME:?GATEWAY_URL 已设置，须同时提供 GATEWAY_USERNAME}"
  : "${GATEWAY_PASSWORD:?GATEWAY_URL 已设置，须同时提供 GATEWAY_PASSWORD}"
  GW_ARGS=(
    -e "GATEWAY_URL=$GATEWAY_URL"
    -e "GATEWAY_ORIGIN=$GATEWAY_ORIGIN"
    -e "GATEWAY_USERNAME=$GATEWAY_USERNAME"
    -e "GATEWAY_PASSWORD=$GATEWAY_PASSWORD"
  )
  K6_TLS_ARGS=(--insecure-skip-tls-verify)
fi

# 判定口径：环境显式提供 > 服务端 /version 指纹（阶段三含 slo 与计价）> 阶段默认
SLO_ARGS=()
CALIBER_SOURCE="stage-default"
if [ -n "${SLO_MS:-}" ]; then
  SLO_ARGS+=(-e "SLO_MS=$SLO_MS"); CALIBER_SOURCE="env"
fi
if [ -n "${FIRST_ANSWER_SLO_MS:-}" ]; then
  SLO_ARGS+=(-e "FIRST_ANSWER_SLO_MS=$FIRST_ANSWER_SLO_MS"); CALIBER_SOURCE="env"
fi
if [ -n "${COST_PER_1K:-}" ]; then
  SLO_ARGS+=(-e "COST_PER_1K=$COST_PER_1K"); CALIBER_SOURCE="env"
fi

CALIBER_JSON="$(curl -s -m 5 "$BASE_URL_VALUE/version" 2>/dev/null || true)"
FP_SLO=""; FP_FIRST=""; FP_PRICE=""
if [ -n "$CALIBER_JSON" ]; then
  CALIBER_LINES="$(printf '%s' "$CALIBER_JSON" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
for key, field in (("SLO", "slo_e2e_ms"), ("FIRST", "slo_first_answer_ms"), ("PRICE", "token_price_per_1k")):
    value = data.get(field)
    if isinstance(value, (int, float)):
        print("%s=%s" % (key, value))
' 2>/dev/null || true)"
  while IFS='=' read -r key value; do
    case "$key" in
      SLO) FP_SLO="$value" ;;
      FIRST) FP_FIRST="$value" ;;
      PRICE) FP_PRICE="$value" ;;
    esac
  done <<< "$CALIBER_LINES"
  if [ -z "${SLO_MS:-}" ] && [ -n "$FP_SLO" ]; then
    SLO_ARGS+=(-e "SLO_MS=$FP_SLO"); CALIBER_SOURCE="fingerprint"
  fi
  if [ -z "${FIRST_ANSWER_SLO_MS:-}" ] && [ -n "$FP_FIRST" ]; then
    SLO_ARGS+=(-e "FIRST_ANSWER_SLO_MS=$FP_FIRST"); CALIBER_SOURCE="fingerprint"
  fi
  if [ -z "${COST_PER_1K:-}" ] && [ -n "$FP_PRICE" ]; then
    SLO_ARGS+=(-e "COST_PER_1K=$FP_PRICE"); CALIBER_SOURCE="fingerprint"
  fi
else
  echo "警告：$BASE_URL_VALUE/version 不可达，判定口径退回阶段默认值（CALIBER_SOURCE=stage-default）" >&2
fi

echo "阶段：${STAGE_ID}（${STAGE_DIR_NAME}）；场景：${SCENARIO}（${SCRIPT}）"
echo "服务：${BASE_URL_VALUE}；口径来源：${CALIBER_SOURCE}"
if [ -n "${GATEWAY_URL:-}" ]; then
  echo "链路：gateway（业务经 ${GATEWAY_URL}，Origin ${GATEWAY_ORIGIN}，用户 ${GATEWAY_USERNAME}；控制面直连）"
else
  echo "链路：direct（业务直连被测服务）"
fi
echo "指纹：git ${GIT_HEAD_VALUE}"
echo "      ${K6_VERSION_VALUE}；数据 ${TICKETS_PATH}"
echo "产物前缀：${PREFIX}"
echo "---"

# calib 校准场景需要 P99 分位（其余场景沿用 k6 默认统计口径，历史摘要不变）
TREND_ARGS=()
if [ "$SCENARIO" = calib ]; then
  TREND_ARGS=(--summary-trend-stats "avg,min,med,max,p(90),p(95),p(99)")
fi

cd "$STAGE_DIR"
set +e
k6 run \
  --out "json=$OUT_DIR_ABS/$PREFIX.ndjson" \
  "${TREND_ARGS[@]+"${TREND_ARGS[@]}"}" \
  -e "STAGE=$STAGE_ID" \
  -e "BASE_URL=$BASE_URL_VALUE" \
  -e "RUN_ID=$RUN_ID_VALUE" \
  -e "OUT_PREFIX=$PREFIX" \
  -e "OUT_DIR=out" \
  -e "TICKETS=$TICKETS_PATH" \
  -e "GIT_HEAD=$GIT_HEAD_VALUE" \
  -e "K6_VERSION=$K6_VERSION_VALUE" \
  -e "CALIBER_SOURCE=$CALIBER_SOURCE" \
  "${SLO_ARGS[@]+"${SLO_ARGS[@]}"}" \
  "${RW_ARGS[@]+"${RW_ARGS[@]}"}" \
  "${GW_ARGS[@]+"${GW_ARGS[@]}"}" \
  "${K6_TLS_ARGS[@]+"${K6_TLS_ARGS[@]}"}" \
  "tests/performance/k6/$SCRIPT" "$@"
RC=$?
set -e

echo "---"
echo "产物："
echo "  逐笔采样   out/${PREFIX}.ndjson"
echo "  判定表     out/${PREFIX}-summary.html"
echo "  看板数据   out/${PREFIX}-summary.json"
if [ "$RC" -ne 0 ]; then
  echo "k6 退出码 ${RC}（非 0 通常为阈值未达标；产物已保留，请按方案 §4.3 判定是否暂停）"
fi
exit "$RC"
