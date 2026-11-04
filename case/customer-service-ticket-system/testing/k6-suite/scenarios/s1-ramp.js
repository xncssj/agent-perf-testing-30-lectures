// S1-Ramp 连续递增长稳场景工厂（k6 依赖）：线性递增到目标并发后保持，观察递增期与稳态期的合格率/时延漂移
// - 与 s1 共用全部判定链路（runner、早/中/晚段位、契约阈值、摘要看板），仅执行器不同：
//   ramping-vus，每 RAMP_STEP_SEC 秒 +1 VU，到 RAMP_VUS 后保持到 RAMP_MINUTES 总时长
// - 用法：-e RAMP_VUS=10 -e RAMP_MINUTES=10 -e RAMP_STEP_SEC=1
//   递增时长 = RAMP_VUS × RAMP_STEP_SEC，稳态时长 = 总时长 − 递增时长；递增时长不短于总时长直接报错
// - SCENARIO_NAME 可配场景名（进 k6 自动 scenario 标签 → Prometheus/Grafana 看板的
//   scenario 变量）：同机同看板对比多案例时各设不同名（如 case1-single / case2-multi /
//   case3-workflow），缺省沿用 soak 保持既有产物与看板口径
// - gracefulRampDown 保留 k6 默认（30s）：收尾让在途业务跑完，不腰斩在途工单
import exec from 'k6/execution';
import { Trend, Rate, Gauge, Counter } from 'k6/metrics';
import { requireTickets, pickTicket, resolveRuntime } from '../lib/config.js';
import { probeFingerprint } from '../lib/fingerprint.js';
import { createRunner } from '../lib/api.js';
import { gatewayPrecheck } from '../lib/gateway-auth.js';
import { summaryFor } from '../lib/summary.js';
import { segmentOf } from '../lib/segments.js';
import { k6ThresholdExprs, E2E_CLIENT_WALL_METRIC } from '../lib/contract.js';

export function defineS1Ramp(stage) {
  const runtime = resolveRuntime(stage);
  const runner = createRunner(stage, runtime);
  const RAMP_VUS = Number(__ENV.RAMP_VUS || 10);
  const RAMP_MINUTES = Number(__ENV.RAMP_MINUTES || 10);
  const RAMP_STEP_SEC = Number(__ENV.RAMP_STEP_SEC || 1);
  const SCENARIO_NAME = __ENV.SCENARIO_NAME || 'soak';
  const rampSec = RAMP_VUS * RAMP_STEP_SEC;
  const totalSec = Math.round(RAMP_MINUTES * 60);
  if (rampSec <= 0 || totalSec <= 0 || rampSec >= totalSec) {
    throw new Error(`s1-ramp 参数非法：连续递增时长 ${rampSec}s 必须大于 0 且小于总时长 ${totalSec}s（RAMP_VUS=${RAMP_VUS} RAMP_STEP_SEC=${RAMP_STEP_SEC} RAMP_MINUTES=${RAMP_MINUTES}）`);
  }
  const exprs = k6ThresholdExprs(runtime.sloMs);

  // 与 s1 同口径：整场即计量窗口（契约 §3.4），窗口边界经 Gauge 传给 handleSummary
  const measuredStartG = new Gauge('window_measured_start_unix');
  const measuredEndG = new Gauge('window_measured_end_unix');
  const measuredTokensC = new Counter('window_measured_tokens_total');
  const measuredUsageMissingC = new Counter('window_measured_usage_missing_total');
  let measuredSeen = false;

  requireTickets();

  // 段位指标与 s1 同名同义：early 含递增期，late 为高稳态末段，末段劣化判定沿用 ≤20%
  const segE2e = {
    early: new Trend('soak_e2e_early', true),
    mid: new Trend('soak_e2e_mid', true),
    late: new Trend('soak_e2e_late', true),
  };
  const segQualified = {
    early: new Rate('soak_qualified_early'),
    mid: new Rate('soak_qualified_mid'),
    late: new Rate('soak_qualified_late'),
  };

  const options = {
    scenarios: {
      [SCENARIO_NAME]: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          { duration: rampSec + 's', target: RAMP_VUS },
          { duration: (totalSec - rampSec) + 's', target: RAMP_VUS },
        ],
        gracefulRampDown: '30s',
      },
    },
    thresholds: {
      'http_req_failed': [exprs.errorRate],
      'business_qualified_rate': [exprs.qualifiedRate],
      [E2E_CLIENT_WALL_METRIC]: [exprs.e2eP90],
    },
  };

  function setup() {
    const fingerprint = probeFingerprint(runtime.baseUrl); // 控制面直连不变
    const gateway = gatewayPrecheck(); // 网关模式：开跑前真实登录，失败即中止
    return Object.assign(fingerprint, { gateway: gateway });
  }

  function run() {
    const now = Date.now();
    if (!measuredSeen) { measuredStartG.add(now / 1000); measuredSeen = true; }
    const out = runner.runTicket(pickTicket(), {});
    if (out.tokens === null || out.tokens === undefined) measuredUsageMissingC.add(1);
    else measuredTokensC.add(Number(out.tokens));
    measuredEndG.add(Date.now() / 1000);
    const seg = segmentOf(exec.scenario.progress);
    segE2e[seg].add(out.wallMs);
    segQualified[seg].add(out.levels.qualified);
  }

  return {
    options: options,
    setup: setup,
    run: run,
    handleSummary: summaryFor('s1-ramp', stage, runtime),
    runtime: runtime,
  };
}
