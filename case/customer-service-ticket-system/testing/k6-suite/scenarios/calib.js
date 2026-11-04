// calib 校准场景工厂（k6 依赖）：1 VU × COUNT 笔（默认 20）低并发校准，不参与门禁。
// 用途（方案 v1.8 B11 / 战役 v13 实施 A5）：真实模型轮开窗前按阶段请求契约做小样本校准，
// 输出客户端墙钟 P50/P90/P95/P99、服务端 e2e、首字与 token 摘要，供 SLO/首字/计价拍板引用。
// - 请求契约：直接复用各阶段适配器 buildBody（阶段三为 query 字段，阶段一/二沿用原契约），
//   本场景不硬编码字段，换阶段自动正确（改造前 real-smoke 的阶段三契约缺陷由此规避）。
// - 不参与门禁：不声明 thresholds，k6 退出码只反映脚本/传输错误；判定仍由 b0/c1/s1 等场景负责，
//   校准读数不得当作容量结论。
// - 产物：OUT_DIR/<前缀>-calib.json（机器可读）+ <前缀>-calib.md（人读摘要）；
//   编排链路 OUT_DIR=raw、OUT_PREFIX=k6 → raw/k6-calib.json|md。
// 用法：-e COUNT=20（笔数，可调）；-e TICKETS=（可选数据档，缺省共享工单集）
import { OUT_DIR, OUT_PREFIX, RUN_ID, CALIBER_SOURCE, requireTickets, pickTicket, resolveRuntime } from '../lib/config.js';
import { probeFingerprint } from '../lib/fingerprint.js';
import { createRunner } from '../lib/api.js';
import { gatewayPrecheck } from '../lib/gateway-auth.js';
import { CONTRACT_VERSION } from '../lib/contract.js';

function numOf(values, field) {
  if (!values) return null;
  const v = values[field];
  if (v === undefined || v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// 分位摘要：k6 摘要的 Trend values 只含已配置的分位统计（未配置项为 undefined→null）；
// med 即 P50。编排链路显式传 --summary-trend-stats，直接 k6 run 时由 options.summaryTrendStats 兜底。
function latencySummary(metrics, name) {
  const m = metrics[name];
  const values = (m && m.values) ? m.values : null;
  if (!values) return null;
  return {
    p50: numOf(values, 'med'),
    p90: numOf(values, 'p(90)'),
    p95: numOf(values, 'p(95)'),
    p99: numOf(values, 'p(99)'),
    avg: numOf(values, 'avg'),
    min: numOf(values, 'min'),
    max: numOf(values, 'max'),
  };
}

function counterOf(metrics, name) {
  const m = metrics[name];
  const values = (m && m.values) ? m.values : null;
  return numOf(values, 'count');
}

function rateOf(metrics, name) {
  const m = metrics[name];
  const values = (m && m.values) ? m.values : null;
  return numOf(values, 'rate');
}

function fmt(n, digits) {
  return (n === null || n === undefined) ? '—' : Number(n).toFixed(digits === undefined ? 0 : digits);
}

export function defineCalib(stage) {
  const runtime = resolveRuntime(stage);
  const runner = createRunner(stage, runtime);
  const COUNT = Number(__ENV.COUNT || 20);

  // 不声明 thresholds：校准轮不参与门禁（k6 退出码不承载判定）
  const options = {
    scenarios: {
      calib: {
        executor: 'per-vu-iterations',
        vus: 1,
        iterations: COUNT,
        maxDuration: '1h',
      },
    },
    // 直接 k6 run 本脚本时也保证分位齐全（编排链路另有 --summary-trend-stats 同口径）
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  };

  requireTickets();

  function setup() {
    const fingerprint = probeFingerprint(runtime.baseUrl); // 控制面直连不变
    const gateway = gatewayPrecheck(); // 网关模式：开跑前真实登录，失败即中止
    return Object.assign(fingerprint, { gateway: gateway });
  }

  function run() {
    // 无预热/相位切分：COUNT 笔即全部校准样本（逐笔判定仍走 api.js 统一链路）
    return runner.runTicket(pickTicket(), {});
  }

  function handleSummary(data) {
    const metrics = (data && data.metrics) || {};
    const prefix = OUT_PREFIX || (stage.id + '-calib');
    const jsonPath = OUT_DIR + '/' + prefix + '-calib.json';
    const mdPath = OUT_DIR + '/' + prefix + '-calib.md';
    try {
      const bizTotal = counterOf(metrics, 'business_total');
      const qualifiedN = counterOf(metrics, 'business_qualified_total');
      const qualifiedRate = rateOf(metrics, 'business_qualified_rate');
      const tokensTotal = counterOf(metrics, 'tokens_total');
      const usageMissing = counterOf(metrics, 'usage_missing');
      const wall = latencySummary(metrics, 'e2e_client_wall_ms');
      const serverE2e = latencySummary(metrics, 'e2e_server_ms');
      const firstAnswer = latencySummary(metrics, 'server_first_answer_ms');
      const usageBase = (bizTotal === null || usageMissing === null) ? null : bizTotal - usageMissing;
      const usageCoverage = (usageBase === null || bizTotal === null || bizTotal === 0) ? null : usageBase / bizTotal;
      const tokenAvg = (tokensTotal === null || bizTotal === null || bizTotal === 0) ? null : tokensTotal / bizTotal;

      const doc = {
        contract_version: CONTRACT_VERSION,
        scenario: 'calib',
        stage: stage.id,
        stage_name: stage.name,
        run_id: RUN_ID,
        generated_at: new Date().toISOString(),
        gate_participation: false,
        planned_count: COUNT,
        sample_count: bizTotal,
        qualified: { count: qualifiedN, rate: qualifiedRate },
        e2e_client_wall_ms: wall,
        e2e_server_ms: serverE2e,
        first_answer_ms: firstAnswer,
        tokens: {
          total: tokensTotal,
          usage_missing: usageMissing,
          usage_coverage: usageCoverage,
          avg_per_business: tokenAvg,
        },
        http: {
          requests: counterOf(metrics, 'http_reqs'),
          failed_rate: rateOf(metrics, 'http_req_failed'),
        },
        caliber: {
          slo_ms: runtime.sloMs,
          first_answer_slo_ms: runtime.firstAnswerSloMs,
          cost_per_1k: runtime.costPer1k,
          source: CALIBER_SOURCE,
          from_env: runtime.fromEnv,
        },
        probe: (data && data.setup_data) ? data.setup_data : null,
      };

      const lines = [
        '# calib 校准摘要（阶段 ' + stage.id + ' · ' + stage.name + '）',
        '',
        '- run_id：' + RUN_ID,
        '- 生成时间：' + doc.generated_at,
        '- 样本：' + fmt(bizTotal) + ' / 计划 ' + fmt(COUNT) + ' 笔'
          + '（合格 ' + fmt(qualifiedN) + '，合格率 ' + (qualifiedRate === null ? '—' : fmt(qualifiedRate * 100, 2) + '%') + '）',
        '- 客户端墙钟 E2E（门禁同源）：P50 ' + fmt(wall && wall.p50) + ' / P90 ' + fmt(wall && wall.p90)
          + ' / P95 ' + fmt(wall && wall.p95) + ' / P99 ' + fmt(wall && wall.p99)
          + ' ms（avg ' + fmt(wall && wall.avg) + '，max ' + fmt(wall && wall.max) + '）',
        '- 服务端 E2E（诊断，不参与门禁）：P50 ' + fmt(serverE2e && serverE2e.p50) + ' / P90 ' + fmt(serverE2e && serverE2e.p90)
          + ' / P95 ' + fmt(serverE2e && serverE2e.p95) + ' / P99 ' + fmt(serverE2e && serverE2e.p99) + ' ms',
        '- 首字（服务端 first_answer_ms）：P50 ' + fmt(firstAnswer && firstAnswer.p50)
          + ' / P90 ' + fmt(firstAnswer && firstAnswer.p90) + ' / P95 ' + fmt(firstAnswer && firstAnswer.p95)
          + ' / P99 ' + fmt(firstAnswer && firstAnswer.p99) + ' ms',
        '- Token：总 ' + fmt(tokensTotal) + '，均值 ' + fmt(tokenAvg, 0) + '/笔，usage 缺失 ' + fmt(usageMissing)
          + '，覆盖率 ' + (usageCoverage === null ? '—' : fmt(usageCoverage * 100, 1) + '%'),
        '- 判定口径：SLO ' + fmt(runtime.sloMs) + 'ms / 首字 ' + fmt(runtime.firstAnswerSloMs)
          + 'ms / 计价 ' + fmt(runtime.costPer1k, 4) + ' 元每千 token（来源 ' + CALIBER_SOURCE + '）',
        '',
        '> 校准轮不参与门禁，读数用于 SLO/首字/计价拍板，不得作为容量结论。',
        '',
      ];

      const out = {};
      out[jsonPath] = JSON.stringify(doc, null, 2);
      out[mdPath] = lines.join('\n');
      return out;
    } catch (e) {
      // 摘要生成自身异常也必须落产物：带错误信息的最小摘要，现场可排障
      const stack = String((e && e.stack) || e);
      const out = {};
      out[jsonPath] = JSON.stringify({
        contract_version: CONTRACT_VERSION,
        scenario: 'calib',
        stage: stage.id,
        run_id: RUN_ID,
        gate_participation: false,
        error: stack,
      }, null, 2);
      out[mdPath] = '# calib 校准摘要生成失败\n\n```\n' + stack + '\n```\n';
      return out;
    }
  }

  return {
    options: options,
    setup: setup,
    run: run,
    handleSummary: handleSummary,
    runtime: runtime,
    stage: stage,
  };
}
