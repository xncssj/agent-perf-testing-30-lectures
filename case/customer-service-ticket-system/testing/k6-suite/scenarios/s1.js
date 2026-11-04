// S1 长稳场景工厂（k6 依赖）：固定并发长时运行，观察合格率漂移、时延末段劣化与重试放大
// - 判定：末段（late）对首段（early）劣化 ≤20%（方案 §5）；段位按 exec.scenario.progress 三等分，
//   由 lib/board.js 在摘要页给出段位对比与判定（该判定不进 k6 退出码）
// - 时长按方案 §8.2 公式回填（业务积累量 ÷ 选定 Goodput）；默认 2min 为缩短版冒烟口径
// - 段位时延取客户端墙钟（门禁同源，契约 §3.1）；阈值等值边界统一继承 lib/contract.js
// - WARMUP_SEC（P2/R2a，默认 0 保持「整场即计量窗」契约原状）：>0 时前 N 秒为预热段，
//   请求打 phase=warmup 标签、不计入计量窗/Token 累计/段位指标——供预热隔离对照轮
//   （把 collab 队列预热突刺移出计量窗）使用；段位三等分改为按计量段时长计算
// 用法：-e VUS=20 -e SOAK_MINUTES=120 -e WARMUP_SEC=60
import { Trend, Rate, Gauge, Counter } from 'k6/metrics';
import { requireTickets, pickTicket, resolveRuntime } from '../lib/config.js';
import { probeFingerprint } from '../lib/fingerprint.js';
import { createRunner } from '../lib/api.js';
import { gatewayPrecheck } from '../lib/gateway-auth.js';
import { summaryFor } from '../lib/summary.js';
import { k6ThresholdExprs, E2E_CLIENT_WALL_METRIC } from '../lib/contract.js';

export function defineS1(stage) {
  const runtime = resolveRuntime(stage);
  const runner = createRunner(stage, runtime);
  const VUS = Number(__ENV.VUS || 20);
  const SOAK_MINUTES = Number(__ENV.SOAK_MINUTES || 2);
  const WARMUP_SEC = Number(__ENV.WARMUP_SEC || 0);
  const TOTAL_SEC = SOAK_MINUTES * 60;
  const exprs = k6ThresholdExprs(runtime.sloMs);

  // S1 预热段（WARMUP_SEC>0 时）从 SOAK 总时长内切出，不延长运行；
  // 计量窗 = [WARMUP_SEC, SOAK 结束]。窗口边界经 Gauge 传给 handleSummary
  //（k6 的 handleSummary 在独立 JS 上下文执行，Gauge 摘要保留末值）；Token 走计数器累计。
  const warmupStartG = new Gauge('window_warmup_start_unix');
  const warmupEndG = new Gauge('window_warmup_end_unix');
  const measuredStartG = new Gauge('window_measured_start_unix');
  const measuredEndG = new Gauge('window_measured_end_unix');
  const measuredTokensC = new Counter('window_measured_tokens_total');
  const measuredUsageMissingC = new Counter('window_measured_usage_missing_total');
  const seen = { warmup: false, measured: false };
  let runStartedAt = null;

  requireTickets();

  // 段位指标：early/mid/late 各自独立，避免依赖分档子指标的 key 形态（board.js 直接按名取）
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

  // 阈值键按预热开关拼接（goja 不支持对象字面量拼接/计算键，统一运行时赋值，
  // 与 c1.js 风格一致）：WARMUP_SEC>0 时只考核 phase=measured 子指标
  const thresholdTag = WARMUP_SEC > 0 ? '{phase:measured}' : '';
  const thresholds = {};
  thresholds['http_req_failed' + thresholdTag] = [exprs.errorRate];
  thresholds['business_qualified_rate' + thresholdTag] = [exprs.qualifiedRate];
  thresholds[E2E_CLIENT_WALL_METRIC + thresholdTag] = [exprs.e2eP90];

  const options = {
    scenarios: {
      soak: { executor: 'constant-vus', vus: VUS, duration: SOAK_MINUTES + 'm' },
    },
    thresholds: thresholds,
  };

  function setup() {
    const fingerprint = probeFingerprint(runtime.baseUrl); // 控制面直连不变
    const gateway = gatewayPrecheck(); // 网关模式：开跑前真实登录，失败即中止
    return Object.assign(fingerprint, { gateway: gateway });
  }

  function run() {
    const now = Date.now();
    if (runStartedAt === null) runStartedAt = now;
    if (WARMUP_SEC > 0) {
      const elapsedSec = (now - runStartedAt) / 1000;
      if (elapsedSec < WARMUP_SEC) {
        if (!seen.warmup) { warmupStartG.add(now / 1000); seen.warmup = true; }
        const warmupOut = runner.runTicket(pickTicket(), { phase: 'warmup' });
        warmupEndG.add(Date.now() / 1000);
        return warmupOut;
      }
      if (!seen.measured) { measuredStartG.add(now / 1000); seen.measured = true; }
    } else if (!seen.measured) {
      measuredStartG.add(now / 1000); seen.measured = true;
    }
    const out = runner.runTicket(pickTicket(), WARMUP_SEC > 0 ? { phase: 'measured' } : {});
    if (out.tokens === null || out.tokens === undefined) measuredUsageMissingC.add(1);
    else measuredTokensC.add(Number(out.tokens));
    measuredEndG.add(Date.now() / 1000);
    // 段位三等分：预热段按计量段时长计算（elapsed-WARMUP_SEC 占 (TOTAL-WARMUP_SEC) 比例），
    // 无预热时与 exec.scenario.progress 等价；预热请求不计段位（防止 early 段被预热污染）
    const measuredFrac = ((Date.now() - runStartedAt) / 1000 - WARMUP_SEC) / Math.max(1, TOTAL_SEC - WARMUP_SEC);
    const seg = measuredFrac < 1 / 3 ? 'early' : (measuredFrac < 2 / 3 ? 'mid' : 'late');
    segE2e[seg].add(out.wallMs);
    segQualified[seg].add(out.levels.qualified);
  }

  return {
    options: options,
    setup: setup,
    run: run,
    handleSummary: summaryFor('s1', stage, runtime),
    runtime: runtime,
  };
}
