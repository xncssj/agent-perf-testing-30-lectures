// E2 多轮上下文场景工厂（k6 依赖）：同一会话内 TURNS 轮连续问答，逐轮累积上下文前缀。
// - 每轮请求经阶段适配器构造（请求契约随阶段走），user_query 前缀累积上下文（客户端可构造的
//   上下文增长），记录 ctx_turn 轮次与 ctx_payload_bytes 请求体规模（上下文规模代理读数），
//   业务判定复用四级判定链（qualified 随 ctx_turn 分档）
// - 服务端 context occupancy（阶段三 harness 直方图）由业务监控采样对账，客户端此处只承诺
//   「轮次 + 请求体规模 + 逐轮合格率」三个可判定读数
// 用法：-e TURNS=5 -e CTX_GROWTH=200（每轮追加字符数）
import exec from 'k6/execution';
import { Gauge, Trend } from 'k6/metrics';
import { requireTickets, pickTicket, resolveRuntime } from '../lib/config.js';
import { probeFingerprint } from '../lib/fingerprint.js';
import { createRunner } from '../lib/api.js';
import { gatewayPrecheck } from '../lib/gateway-auth.js';
import { summaryFor } from '../lib/summary.js';
import { k6ThresholdExprs, E2E_CLIENT_WALL_METRIC } from '../lib/contract.js';

export function defineE2(stage) {
  const runtime = resolveRuntime(stage);
  const runner = createRunner(stage, runtime);
  const TURNS = Number(__ENV.TURNS || 5);
  const CTX_GROWTH = Number(__ENV.CTX_GROWTH || 200);
  const exprs = k6ThresholdExprs(runtime.sloMs);

  requireTickets();

  const ctxBytes = new Gauge('ctx_payload_bytes');
  const ctxE2e = new Trend('ctx_turn_e2e_ms', true);
  const measuredStartG = new Gauge('window_measured_start_unix');
  const measuredEndG = new Gauge('window_measured_end_unix');

  const scenarios = {};
  const thresholds = { 'http_req_failed': [exprs.errorRate] };
  for (let t = 1; t <= TURNS; t++) {
    scenarios['turn_' + t] = {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 1,
      // 轮间隔 12s：多轮自然节奏，同时保证计量窗口 ≥ 监控采样上限（10s/15s），证据可覆盖
      startTime: ((t - 1) * 12) + 's',
      maxDuration: '30s',
    };
    thresholds['business_qualified_rate{ctx_turn:' + t + '}'] = [exprs.qualifiedRate];
    thresholds[E2E_CLIENT_WALL_METRIC + '{ctx_turn:' + t + '}'] = [exprs.e2eP90];
  }

  function setup() {
    const fingerprint = probeFingerprint(runtime.baseUrl); // 控制面直连不变
    const gateway = gatewayPrecheck(); // 网关模式：开跑前真实登录，失败即中止
    return Object.assign(fingerprint, { gateway: gateway });
  }

  function hit() {
    const now = Date.now();
    const turn = Number(exec.scenario.name.replace(/^turn_/, ''));
    // k6 每个 VU 有独立模块实例：起点按轮次号确定性发射（只在第 1 轮发），避免多实例末值覆盖
    if (turn === 1) measuredStartG.add(now / 1000);
    const base = pickTicket();
    const filler = new Array(1 + turn * CTX_GROWTH).join('。');
    const prefixed = Object.assign({}, base, {
      user_query: '【第' + turn + '轮，此前上下文' + (turn - 1) + '轮】' + filler + base.user_query,
    });
    const payload = runner.stage.buildBody(__ENV.RUN_ID || 'e2-multiturn', prefixed);
    const out = runner.runTicket(prefixed, { ctx_turn: String(turn) });
    ctxBytes.add(JSON.stringify(payload).length, { ctx_turn: String(turn) });
    ctxE2e.add(out.wallMs, { ctx_turn: String(turn) });
    measuredEndG.add(Date.now() / 1000);
    return out;
  }

  return {
    options: { scenarios: scenarios, thresholds: thresholds },
    setup: setup,
    run: hit,
    handleSummary: summaryFor('e2-multiturn', stage, runtime),
    runtime: runtime,
  };
}
