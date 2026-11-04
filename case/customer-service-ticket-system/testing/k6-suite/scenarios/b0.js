// B0 基准场景工厂（k6 依赖）：工单池轮换 × 1VU ×（WARMUP 预热 + ITERS 全量）
// - 预热：正式计数前 WARMUP 次迭代打 phase=warmup，阈值只考核 phase=measured；首个正式迭代加 cold 标注
// - 默认 ITERS=245：共享工单集全量一轮（schema v2.1.0，245 条）
// - setup 抓取 /version 与 /health 指纹（方案 §4.1），随摘要产物落盘
// - 窗口经边界 Gauge 跨上下文传给 handleSummary（k6 的 handleSummary 在独立 JS 上下文执行，
//   VU 侧模块状态不可见；Gauge 摘要保留末值）；WARMUP=0 时无 warmup 窗口（冷启动独立运行）
// 阈值等值边界与 E2E 门禁来源统一继承 lib/contract.js（契约 §3.1/§3.3），不在场景内另设数值。
import { Gauge, Counter } from 'k6/metrics';
import { requireTickets, pickTicket, resolveRuntime } from '../lib/config.js';
import { probeFingerprint } from '../lib/fingerprint.js';
import { createRunner } from '../lib/api.js';
import { gatewayPrecheck } from '../lib/gateway-auth.js';
import { summaryFor } from '../lib/summary.js';
import { k6ThresholdExprs, E2E_CLIENT_WALL_METRIC } from '../lib/contract.js';

export function defineB0(stage) {
  const runtime = resolveRuntime(stage);
  const runner = createRunner(stage, runtime);
  const ITERS = Number(__ENV.ITERS || 245);
  const WARMUP = Number(__ENV.WARMUP === undefined ? 3 : __ENV.WARMUP);
  const exprs = k6ThresholdExprs(runtime.sloMs);

  // 窗口边界与计量范围内累计（契约 §2/§3.4）：VU 侧只发指标，handleSummary 从聚合摘要读取
  const warmupStartG = new Gauge('window_warmup_start_unix');
  const warmupEndG = new Gauge('window_warmup_end_unix');
  const measuredStartG = new Gauge('window_measured_start_unix');
  const measuredEndG = new Gauge('window_measured_end_unix');
  const measuredTokensC = new Counter('window_measured_tokens_total');
  const measuredUsageMissingC = new Counter('window_measured_usage_missing_total');
  const seen = { warmup: false, measured: false };

  requireTickets();

  const options = {
    scenarios: {
      baseline: {
        executor: 'per-vu-iterations',
        vus: 1,
        iterations: WARMUP + ITERS,
        maxDuration: '2h',
      },
    },
    thresholds: {
      'http_req_failed{phase:measured}': [exprs.errorRate],
      'business_qualified_rate{phase:measured}': [exprs.qualifiedRate],
      [E2E_CLIENT_WALL_METRIC + '{phase:measured}']: [exprs.e2eP90],
    },
  };

  function setup() {
    const fingerprint = probeFingerprint(runtime.baseUrl); // 控制面直连不变
    const gateway = gatewayPrecheck(); // 网关模式：开跑前真实登录，失败即中止
    return Object.assign(fingerprint, { gateway: gateway });
  }

  function run() {
    const phase = __ITER < WARMUP ? 'warmup' : 'measured';
    const tags = { phase: phase };
    if (__ITER === WARMUP) tags.cold = '1'; // 首个正式迭代 = 冷启动标注轮
    const now = Date.now();
    if (phase === 'warmup') {
      if (!seen.warmup) { warmupStartG.add(now / 1000); seen.warmup = true; }
      warmupEndG.add(now / 1000); // 末值 = 最后一次预热请求完成时刻
      return runner.runTicket(pickTicket(), tags);
    }
    if (!seen.measured) { measuredStartG.add(now / 1000); seen.measured = true; }
    const out = runner.runTicket(pickTicket(), tags);
    // 计量范围统一（契约 §3.4）：Token 与 Goodput 使用同一 measured 范围（摘要按计数器读取）
    if (out.tokens === null || out.tokens === undefined) measuredUsageMissingC.add(1);
    else measuredTokensC.add(Number(out.tokens));
    measuredEndG.add(Date.now() / 1000);
    return out;
  }

  return {
    options: options,
    setup: setup,
    run: run,
    handleSummary: summaryFor('b0', stage, runtime),
    runtime: runtime,
  };
}
