// E1 流量变化场景工厂（k6 依赖）：突发（spike）与骤降（drop）两个独立模式，PATTERN 选择。
// - 突发：低压 → 阶跃高压 → 回落，三段独立 exec（phase=pre|spike|post 分段判定）
// - 骤降：高压 → 骤降低压，两段独立 exec（phase=high|low）
// - 每种模式一次独立运行（独立 run_id 与摘要），不与基准共享窗口；阈值等值边界继承 lib/contract.js
// - 整场即计量窗口（无预热）：边界 Gauge 供聚合器还原窗口（同 s1）
// 用法：-e PATTERN=spike（默认）|drop -e PHASE_SEC=20 -e SPIKE_VUS=20 -e BASE_VUS=1
import exec from 'k6/execution';
import { Gauge } from 'k6/metrics';
import { requireTickets, pickTicket, resolveRuntime } from '../lib/config.js';
import { probeFingerprint } from '../lib/fingerprint.js';
import { createRunner } from '../lib/api.js';
import { gatewayPrecheck } from '../lib/gateway-auth.js';
import { summaryFor } from '../lib/summary.js';
import { k6ThresholdExprs, E2E_CLIENT_WALL_METRIC } from '../lib/contract.js';

export function defineE1(stage) {
  const runtime = resolveRuntime(stage);
  const runner = createRunner(stage, runtime);
  const PATTERN = (__ENV.PATTERN || 'spike');
  const PHASE_SEC = Number(__ENV.PHASE_SEC || 20);
  const SPIKE_VUS = Number(__ENV.SPIKE_VUS || 20);
  const BASE_VUS = Number(__ENV.BASE_VUS || 1);
  const exprs = k6ThresholdExprs(runtime.sloMs);

  requireTickets();

  const measuredStartG = new Gauge('window_measured_start_unix');
  const measuredEndG = new Gauge('window_measured_end_unix');

  const phases = (PATTERN === 'drop')
    ? [{ name: 'high', vus: SPIKE_VUS }, { name: 'low', vus: BASE_VUS }]
    : [{ name: 'pre', vus: BASE_VUS }, { name: 'spike', vus: SPIKE_VUS }, { name: 'post', vus: BASE_VUS }];
  const FIRST_PHASE = phases[0].name;

  const scenarios = {};
  const thresholds = { 'http_req_failed': [exprs.errorRate] };
  let offset = 0;
  for (let i = 0; i < phases.length; i++) {
    const p = phases[i];
    scenarios['phase_' + p.name] = {
      executor: 'constant-vus',
      vus: p.vus,
      duration: PHASE_SEC + 's',
      startTime: offset + 's',
    };
    thresholds['business_qualified_rate{phase:' + p.name + '}'] = [exprs.qualifiedRate];
    thresholds[E2E_CLIENT_WALL_METRIC + '{phase:' + p.name + '}'] = [exprs.e2eP90];
    offset += PHASE_SEC;
  }

  function setup() {
    const fingerprint = probeFingerprint(runtime.baseUrl); // 控制面直连不变
    const gateway = gatewayPrecheck(); // 网关模式：开跑前真实登录，失败即中止
    return Object.assign(fingerprint, { gateway: gateway });
  }

  function hit() {
    const now = Date.now();
    const phase = exec.scenario.name.replace(/^phase_/, '');
    // k6 每个 VU 有独立模块实例：起点按首相位确定性发射，避免多实例末值覆盖
    if (phase === FIRST_PHASE) measuredStartG.add(now / 1000);
    runner.runTicket(pickTicket(), { pattern: PATTERN, phase: phase });
    measuredEndG.add(Date.now() / 1000);
  }

  return {
    options: { scenarios: scenarios, thresholds: thresholds },
    setup: setup,
    run: hit,
    handleSummary: summaryFor(PATTERN === 'drop' ? 'e1-drop' : 'e1-spike', stage, runtime),
    runtime: runtime,
  };
}
