// 业务看板（纯 ESM，无 k6 依赖）：从 k6 handleSummary 的聚合数据算出可入库读数并渲染 HTML/JSON。
// 口径（与三阶段 tests/performance/common.py aggregate() 对齐）：
// - 一笔请求 = 一笔业务；业务数与合格数走 business_total / business_measured_*（探活请求不进分母）
// - Goodput = 合格业务数 ÷ k6 实测运行时长
// - 成本仅在用量覆盖率 100% 时给出；存在缺失则显示「不可计算」，不显示零成本
// - 重试放大系数 = 工具尝试总数 ÷ 逻辑调用数
// - 安全层：违规业务数/违规条数/数据缺失笔数三项并列，缺失不并入「无违规」
// - 阶段观测（阶段二 agent 路径/降级/熔断/扇出，阶段三 path/branch/intent/tool/首字）按指标是否存在渲染
import { degradationVerdict } from './segments.js';
import {
  CONTRACT_VERSION, evaluateGate, measuredSeconds,
  E2E_CLIENT_WALL_METRIC, E2E_SERVER_METRIC,
  GATE_PASS, GATE_FAIL, GATE_NOT_VERIFIED,
} from './contract.js';

export function fmt(n, d) {
  const digits = (d === undefined) ? 2 : d;
  return (n === null || n === undefined || !Number.isFinite(Number(n))) ? '—' : Number(n).toFixed(digits);
}

export function val(metrics, key) {
  return (metrics && metrics[key] && metrics[key].values) ? metrics[key].values : null;
}

function valOf(metrics, key, field) {
  const v = val(metrics, key);
  if (v === null || v[field] === undefined) return null;
  return v[field];
}

// 单点取值：分档标签存在时取分档读数，否则回退全量（与合格率保持同一口径范围）
function pick(metrics, name, tag, field) {
  if (tag) {
    const tagged = valOf(metrics, name + '{' + tag + '}', field);
    if (tagged !== null) return tagged;
  }
  return valOf(metrics, name, field);
}

// 全量合格数（B18②）：business_qualified_total 计数器优先；缺失按全轮合格率×全轮业务数推算；
// 再缺失回退入参 fallback（调用方传 qualifiedN，避免同域修复把成本分母算成 null）
function fullQualifiedN(metrics, bizTotal, fallback) {
  const total = valOf(metrics, 'business_qualified_total', 'count');
  if (total !== null) return total;
  const rate = valOf(metrics, 'business_qualified_rate', 'rate');
  if (rate !== null && bizTotal !== null) return Math.round(rate * bizTotal);
  return fallback;
}

function caliberMismatches(runtime, fingerprint) {
  const list = [];
  const fp = (fingerprint && fingerprint.caliber) ? fingerprint.caliber : null;
  if (fp === null) return list;
  if (fp.slo_e2e_ms !== null && fp.slo_e2e_ms !== undefined && Number(fp.slo_e2e_ms) !== Number(runtime.sloMs)) {
    list.push('E2E SLO：本次判定 ' + runtime.sloMs + 'ms，服务端指纹 ' + fp.slo_e2e_ms + 'ms');
  }
  if (fp.slo_first_answer_ms !== null && fp.slo_first_answer_ms !== undefined
    && Number(fp.slo_first_answer_ms) !== Number(runtime.firstAnswerSloMs)) {
    list.push('首字 SLO：本次判定 ' + runtime.firstAnswerSloMs + 'ms，服务端指纹 ' + fp.slo_first_answer_ms + 'ms');
  }
  if (fp.token_price_per_1k !== null && fp.token_price_per_1k !== undefined
    && Number(fp.token_price_per_1k) !== Number(runtime.costPer1k)) {
    list.push('计价：本次 ' + runtime.costPer1k + ' 元/千 token，服务端指纹 ' + fp.token_price_per_1k);
  }
  return list;
}

export function computeBoard(data, options) {
  const opts = options || {};
  const metrics = (data && data.metrics) || {};
  const runSec = ((data && data.state && data.state.testRunDurationMs) || 0) / 1000;
  const costPer1k = Number(opts.costPer1k || 2.0);
  const fingerprint = (data && data.setup_data) || null;
  const runtime = {
    sloMs: opts.sloMs,
    firstAnswerSloMs: opts.firstAnswerSloMs,
    costPer1k: costPer1k,
  };

  // 业务数与合格数：优先 measured 分档（排除预热），缺失则回退全量并在页面注明
  const measuredCount = valOf(metrics, 'business_measured_total', 'count');
  const measuredRate = valOf(metrics, 'business_measured_qualified_rate', 'rate');
  const bizTotal = valOf(metrics, 'business_total', 'count');
  const bizCount = (measuredCount !== null) ? measuredCount : bizTotal;
  const qualifiedRate = (measuredRate !== null) ? measuredRate : valOf(metrics, 'business_qualified_rate', 'rate');
  const bizTag = (measuredCount !== null) ? 'phase:measured' : '';
  const qualifiedN = (qualifiedRate === null || bizCount === null)
    ? null : Math.round(qualifiedRate * bizCount);

  // Goodput 分母（契约 §3.4）：分子分母同域原则（B18①）——计量窗分母仅在业务计数同为
  // 计量窗口径（business_measured_total 在场）时采用；相位型场景（e1/e2，无窗口计数器）
  // 分子为全量，须退回整场时长，否则口径混合高估读数（pre3 e1drop 实测 5.338 vs 全轮 2.881）。
  const windowSec = measuredSeconds(opts.windows);
  const goodputDenom = (windowSec !== null && measuredCount !== null) ? windowSec : runSec;
  const goodputScope = (goodputDenom === windowSec && windowSec !== null) ? 'measured-window' : 'total-run';
  const goodput = (qualifiedN !== null && goodputDenom > 0) ? qualifiedN / goodputDenom : null;

  // 用量与成本（契约 §3.4）：存在计量窗口累计计数器时（b0/s1 注入）优先采用，分母取计量窗
  // 合格数；窗口 token 缺失（c1 族未注入，B18②）时退回全量计数，分母须同取全量合格数
  // （business_qualified_total 优先，缺失按全轮合格率×全轮业务数推算），不得与计量窗笔数混除
  // （pre3 c1 实测混除 5.290 vs 同域 3.486）。
  const windowTokens = valOf(metrics, 'window_measured_tokens_total', 'count');
  const windowUsageMissing = valOf(metrics, 'window_measured_usage_missing_total', 'count');
  const tokens = (windowTokens !== null) ? windowTokens : (valOf(metrics, 'tokens_total', 'count') || 0);
  const usageMissing = (windowUsageMissing !== null)
    ? windowUsageMissing : (valOf(metrics, 'usage_missing', 'count') || 0);
  const usageBase = bizTotal || 0;
  const coverage = usageBase ? (usageBase - usageMissing) / usageBase : 0;
  const costScope = (windowTokens !== null) ? 'measured-window' : 'total-run';
  const costDenom = (windowTokens !== null) ? qualifiedN : fullQualifiedN(metrics, bizTotal, qualifiedN);
  const costKnown = coverage === 1 && costDenom !== null && costDenom > 0;
  const cost = costKnown ? (tokens / 1000) * costPer1k / costDenom : null;

  // 工具调用与重试放大
  const calls = valOf(metrics, 'tool_calls_total', 'count') || 0;
  const attempts = valOf(metrics, 'tool_attempts_total', 'count') || 0;
  const amplification = calls ? attempts / calls : null;

  // 工具覆盖率（阶段一/二观测项）
  const toolRate = valOf(metrics, 'tool_coverage_rate', 'rate');
  const toolChecked = valOf(metrics, 'tool_checked_total', 'count');

  // 安全层（三阶段）
  const security = {
    violations: valOf(metrics, 'security_violations_total', 'count'),
    violating: valOf(metrics, 'security_violating_total', 'count'),
    missing: valOf(metrics, 'security_missing_total', 'count'),
  };

  // 阶段二观测
  const stage2 = { available: val(metrics, 'ma_agent_path_ok') !== null };
  if (stage2.available) {
    stage2.agentPathOkRate = valOf(metrics, 'ma_agent_path_ok', 'rate');
    stage2.degradedRate = valOf(metrics, 'ma_degraded_rate', 'rate');
    stage2.degradedAgents = valOf(metrics, 'ma_degraded_agents_total', 'count');
    stage2.breakerSkipped = valOf(metrics, 'ma_breaker_skipped_total', 'count');
    stage2.fanout = {
      available: val(metrics, 'ma_fanout_efficiency') !== null,
      efficiencyAvg: valOf(metrics, 'ma_fanout_efficiency', 'avg'),
      efficiencyMin: valOf(metrics, 'ma_fanout_efficiency', 'min'),
      efficiencyMax: valOf(metrics, 'ma_fanout_efficiency', 'max'),
      wallAvg: valOf(metrics, 'ma_fanout_wall_ms', 'avg'),
      serialAvg: valOf(metrics, 'ma_fanout_serial_ms', 'avg'),
    };
  }

  // 阶段三观测
  const stage3 = { available: val(metrics, 'wf_path_ok') !== null || val(metrics, 'wf_tool_ok') !== null };
  if (stage3.available) {
    stage3.pathOkRate = valOf(metrics, 'wf_path_ok', 'rate');
    stage3.branchOkRate = valOf(metrics, 'wf_branch_ok', 'rate');
    stage3.intentOkRate = valOf(metrics, 'wf_intent_ok', 'rate');
    stage3.toolOkRate = valOf(metrics, 'wf_tool_ok', 'rate');
    stage3.nodeErrors = valOf(metrics, 'wf_node_errors_total', 'count');
    stage3.confidenceAvg = valOf(metrics, 'wf_confidence', 'avg');
    stage3.firstAnswerP90 = pick(metrics, 'server_first_answer_ms', bizTag, 'p(90)');
    stage3.firstAnswerAvg = pick(metrics, 'server_first_answer_ms', bizTag, 'avg');
    stage3.firstAnswerOkRate = valOf(metrics, 'business_first_answer_ok', 'rate');
    // RAG 链路观测（不进门禁）：仅知识意图请求出样本
    stage3.rag = {
      available: val(metrics, 'wf_rag_hit_rate') !== null,
      latencyAvg: valOf(metrics, 'wf_rag_latency_ms', 'avg'),
      latencyP95: valOf(metrics, 'wf_rag_latency_ms', 'p(95)'),
      hitRate: valOf(metrics, 'wf_rag_hit_rate', 'rate'),
      citationsAvg: valOf(metrics, 'wf_rag_citations', 'avg'),
    };
  } else {
    // 阶段一/二：首字仅作观测（若服务端返回 first_answer_ms）
    stage3.firstAnswerP90 = pick(metrics, 'server_first_answer_ms', bizTag, 'p(90)');
    stage3.firstAnswerAvg = pick(metrics, 'server_first_answer_ms', bizTag, 'avg');
    stage3.firstAnswerOkRate = null;
  }

  // 长稳段位：仅在 s1 存在段位指标时计算
  const soak = { available: false };
  const earlyAvg = valOf(metrics, 'soak_e2e_early', 'avg');
  const lateAvg = valOf(metrics, 'soak_e2e_late', 'avg');
  if (earlyAvg !== null || lateAvg !== null) {
    soak.available = true;
    const segs = ['early', 'mid', 'late'];
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      // Trend 摘要不提供 count，Rate 提供 passes/fails：用合格率样本数作为段位样本数
      const qv = val(metrics, 'soak_qualified_' + s) || {};
      const passes = (qv.passes === undefined) ? null : Number(qv.passes);
      const fails = (qv.fails === undefined) ? null : Number(qv.fails);
      soak[s] = {
        avg: valOf(metrics, 'soak_e2e_' + s, 'avg'),
        p90: valOf(metrics, 'soak_e2e_' + s, 'p(90)'),
        count: (passes !== null && fails !== null) ? passes + fails : valOf(metrics, 'soak_e2e_' + s, 'count'),
        qualifiedRate: valOf(metrics, 'soak_qualified_' + s, 'rate'),
      };
    }
    soak.degradation = degradationVerdict(earlyAvg, lateAvg, opts.degradationLimitPct || 20);
  }

  // 请求级错误率：与门禁阈值同口径（http_req_failed 分档优先），供三态门禁计算
  const errorRate = pick(metrics, 'http_req_failed', bizTag, 'rate');

  // 三态门禁（契约 §5）：判定顺序固定——证据/窗口校验 → NOT_VERIFIED；三项阈值 → FAIL/PASS。
  // manifest、窗口、熔断与安全证据由运行清单上下文提供（启动器接入见批次计划 R04/R05）；
  // 上下文缺失时按契约如实返回 NOT_VERIFIED，不冒充通过。
  const gateInput = {
    hasManifest: !!(opts.manifest && opts.manifest.run_id),
    windowAligned: (opts.windows && opts.windows.measured) ? true : undefined,
    fused: opts.fused === true,
    aborted: opts.aborted === true,
    evidence: opts.safetyEvidence || [],
    qualifiedRate: qualifiedRate,
    errorRate: errorRate,
    e2eP90Ms: pick(metrics, E2E_CLIENT_WALL_METRIC, bizTag, 'p(90)'),
    sloMs: runtime.sloMs,
  };
  const gate = evaluateGate(gateInput);
  gate.input = gateInput;

  // 阈值判定：兼容数组 [{threshold,ok}] 与对象 {threshold:{ok}} 两种形态
  const thresholds = [];
  const names = Object.keys(metrics);
  for (let i = 0; i < names.length; i++) {
    const m = metrics[names[i]];
    if (!m || !m.thresholds) continue;
    const th = m.thresholds;
    if (Array.isArray(th)) {
      for (let j = 0; j < th.length; j++) {
        thresholds.push({ metric: names[i], threshold: th[j].threshold, ok: !!th[j].ok });
      }
    } else {
      const keys = Object.keys(th);
      for (let j = 0; j < keys.length; j++) {
        thresholds.push({ metric: names[i], threshold: keys[j], ok: !!(th[keys[j]] && th[keys[j]].ok) });
      }
    }
  }
  const allOk = thresholds.length === 0 || thresholds.every(function (v) { return v.ok; });

  return {
    contractVersion: CONTRACT_VERSION,
    scenario: opts.scenario || 'k6',
    stageId: opts.stageId || '',
    stamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
    runSec: runSec,
    fingerprint: fingerprint,
    caliber: {
      sloMs: runtime.sloMs,
      firstAnswerSloMs: runtime.firstAnswerSloMs,
      costPer1k: costPer1k,
      source: opts.caliberSource || '',
      mismatches: caliberMismatches(runtime, fingerprint),
    },
    bizTotal: bizTotal,
    bizCount: bizCount,
    bizScope: bizTag,
    qualifiedN: qualifiedN,
    qualifiedRate: qualifiedRate,
    errorRate: errorRate,
    goodput: goodput,
    goodputScope: goodputScope,
    costScope: costScope,
    goodputDenomSec: goodputDenom,
    latencyScope: bizTag,
    // E2E 双口径分开取数（契约 §3.1）：门禁与展示主口径为客户端墙钟；服务端值单列诊断
    e2eP90: pick(metrics, E2E_CLIENT_WALL_METRIC, bizTag, 'p(90)'),
    e2eP95: pick(metrics, E2E_CLIENT_WALL_METRIC, bizTag, 'p(95)'),
    e2eAvg: pick(metrics, E2E_CLIENT_WALL_METRIC, bizTag, 'avg'),
    e2eServerP90: pick(metrics, E2E_SERVER_METRIC, bizTag, 'p(90)'),
    e2eServerAvg: pick(metrics, E2E_SERVER_METRIC, bizTag, 'avg'),
    tokens: tokens,
    usageMissing: usageMissing,
    coverage: coverage,
    costKnown: costKnown,
    cost: cost,
    calls: calls,
    attempts: attempts,
    amplification: amplification,
    toolRate: toolRate,
    toolChecked: toolChecked,
    e2eMissing: valOf(metrics, 'e2e_missing_total', 'count'),
    security: security,
    stage2: stage2,
    stage3: stage3,
    soak: soak,
    thresholds: thresholds,
    // k6 阈值表达式结果（与退出码一致）保留展示；三态门禁 verdict 以契约判定顺序为准
    allOk: allOk,
    gate: gate,
    httpReqs: valOf(metrics, 'http_reqs', 'count'),
  };
}

// 从 k6 聚合摘要还原运行窗口（Gauge 摘要保留末值；场景侧只发边界指标，此处为唯一解析点）。
// 返回 { warmup, measured, levels }；窗口不完整时对应项为 null，调用方不得用整场时长顶替。
// opts.stabilizationSeconds：C1 稳定期秒数（与场景侧 STAB_SEC 同值）；opts.levels：C1 档位数组
export function windowsFromMetrics(metrics, opts) {
  const o = opts || {};
  const m = metrics || {};
  function iso(name) {
    const v = valOf(m, name, 'value');
    if (v === null || v === undefined) return null;
    const n = Number(v);
    if (!Number.isFinite(n) || n <= 0) return null;
    return new Date(n * 1000).toISOString();
  }
  const warmupStart = iso('window_warmup_start_unix');
  const warmupEnd = iso('window_warmup_end_unix');
  const measuredStart = iso('window_measured_start_unix');
  const measuredEnd = iso('window_measured_end_unix');
  const levels = {};
  const levelList = Array.isArray(o.levels) ? o.levels : [];
  const stabMs = ((o.stabilizationSeconds === undefined ? 60 : Number(o.stabilizationSeconds))) * 1000;
  for (let i = 0; i < levelList.length; i++) {
    const level = levelList[i];
    const s = iso('c1_l' + level + '_start_unix');
    const e = iso('c1_l' + level + '_end_unix');
    if (s === null || e === null) continue;
    const startMs = Date.parse(s);
    const stabEndIso = new Date(startMs + stabMs).toISOString();
    levels[level] = {
      start: s,
      stabilizationEnd: stabEndIso,
      measuredStart: stabEndIso,
      end: e,
      plannedLoad: level,
    };
  }
  return {
    warmup: (warmupStart && warmupEnd) ? { start: warmupStart, end: warmupEnd } : null,
    measured: (measuredStart && measuredEnd) ? { start: measuredStart, end: measuredEnd } : null,
    levels: levels,
  };
}

// 逐档容量摘要（契约 §4）：每个 C1 档位独立生成；跨档汇总可用于展示趋势，
// 但不得代替逐档摘要回答容量。阈值统计只取 {level:X,phase:measured} 子指标（稳定期不入分母）。
// levelWindows: {level: {start, stabilizationEnd, measuredStart, end, plannedLoad}}（场景运行期记录，ISO 字符串）
export function computeLevelSummaries(data, options) {
  const opts = options || {};
  const metrics = (data && data.metrics) || {};
  const levelWindows = opts.levelWindows || {};
  const levels = Object.keys(levelWindows).map(Number).sort(function (a, b) { return a - b; });
  const out = [];
  for (let i = 0; i < levels.length; i++) {
    const level = levels[i];
    const w = levelWindows[level] || {};
    const tag = 'level:' + level + ',phase:measured';
    // Rate 子指标 values 形如 {rate, passes, fails}：passes/fails 即计量窗口内的合格/不合格样本数
    const qValues = val(metrics, 'business_qualified_rate{' + tag + '}') || {};
    const passes = (qValues.passes === undefined) ? null : Number(qValues.passes);
    const fails = (qValues.fails === undefined) ? null : Number(qValues.fails);
    const businessTotal = (passes !== null && fails !== null) ? passes + fails : null;
    const qualifiedTotal = passes;
    const qualifiedRate = (qValues.rate === undefined) ? null : Number(qValues.rate);
    const errorRate = valOf(metrics, 'http_req_failed{' + tag + '}', 'rate');
    const measuredSec = measuredSeconds({ measured: { start: w.measuredStart, end: w.end } });
    const goodput = (qualifiedTotal !== null && measuredSec !== null && measuredSec > 0)
      ? qualifiedTotal / measuredSec : null;
    const gate = evaluateGate({
      hasManifest: false, // 运行清单由隔离层接入（批次计划 R04）；接入前按契约如实 NOT_VERIFIED
      qualifiedRate: qualifiedRate,
      errorRate: errorRate,
      e2eP90Ms: valOf(metrics, E2E_CLIENT_WALL_METRIC + '{' + tag + '}', 'p(90)'),
      sloMs: opts.sloMs,
    });
    out.push({
      contract_version: opts.contractVersion,
      run_id: opts.runId,
      level_id: 'c1-level-' + level,
      planned_load: (w.plannedLoad === undefined) ? level : w.plannedLoad,
      stabilization: { start: w.start || null, end: w.stabilizationEnd || null },
      measured: { start: w.measuredStart || null, end: w.end || null },
      stabilization_seconds: (w.start && w.stabilizationEnd)
        ? (Date.parse(w.stabilizationEnd) - Date.parse(w.start)) / 1000 : null,
      measured_seconds: measuredSec,
      business_total: businessTotal,
      qualified_total: qualifiedTotal,
      qualified_rate: qualifiedRate,
      goodput_per_second: goodput,
      e2e_client_wall_p90_ms: valOf(metrics, E2E_CLIENT_WALL_METRIC + '{' + tag + '}', 'p(90)'),
      e2e_client_wall_p99_ms: valOf(metrics, E2E_CLIENT_WALL_METRIC + '{' + tag + '}', 'p(99)'),
      e2e_server_p90_ms: valOf(metrics, E2E_SERVER_METRIC + '{' + tag + '}', 'p(90)'),
      error_rate: errorRate,
      gate: { verdict: gate.verdict, reasons: gate.reason_codes },
      evidence: {
        manifest: null, // R04 接入运行清单后回填
        samples: opts.samplesRef || null,
        environment: null,
        business: null,
      },
    });
  }
  return out;
}

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function row(label, value) {
  return '<tr><td>' + esc(label) + '</td><td class="num">' + esc(value) + '</td></tr>';
}

function pct(rate) {
  return (rate === null || rate === undefined) ? '—' : fmt(rate * 100, 1) + '%';
}

function sectionTable(title, rows) {
  return '<h2>' + esc(title) + '</h2>\n<table>\n' + rows.join('') + '</table>\n';
}

function fingerprintRows(fp, extra) {
  if (!fp) {
    return row('服务端指纹', '不可用（setup 探活未返回 /version）')
      + row('探活错误', extra.probeError || '—');
  }
  const v = fp.version || {};
  const mode = v.protocol_mode || {};
  const obs = v.observability || {};
  const limits = v.effective_limits || {};
  const hashes = v.file_hashes || {};
  return row('服务端运行指纹', v.fingerprint || '—')
    + row('应用与版本', (v.app || '—') + ' ' + (v.version || '—'))
    + row('指纹文件数（含共享数据哈希）', Object.keys(hashes).length)
    + row('协议模式', 'provider=' + (mode.llm_provider || v.provider || '—')
      + '，model=' + (v.model || '—')
      + '，tool_calling=' + (mode.tool_calling_mode || '—')
      + '，stream=' + (mode.stream_mode || '—')
      + '，mock_seed=' + (v.mock_seed === undefined ? '—' : v.mock_seed))
    + row('阶段特征字段', stageExtraFields(v))
    + row('有效预算', 'max_iterations=' + (limits.max_iterations === undefined ? '—' : limits.max_iterations)
      + '，max_tool_calls=' + (limits.max_tool_calls === undefined ? '—' : limits.max_tool_calls)
      + '，request_timeout_s=' + (limits.request_timeout_s === undefined ? '—' : limits.request_timeout_s))
    + row('观测上报', 'langfuse_enabled=' + (obs.langfuse_enabled === undefined ? '—' : String(obs.langfuse_enabled)))
    + row('服务存活', fp.health && fp.health.status ? fp.health.status : '—')
    + row('探活错误', fp.error ? fp.error : '无');
}

// 阶段特征字段：阶段二编排参数 / 阶段三 RAG 与 SLO 配置（各阶段指纹字段不同，缺失显示 —）
function stageExtraFields(v) {
  const parts = [];
  if (v.orchestration !== undefined) parts.push('orchestration=' + v.orchestration);
  if (v.parallel_limit !== undefined) parts.push('parallel_limit=' + v.parallel_limit);
  if (v.max_retries !== undefined) parts.push('max_retries=' + v.max_retries);
  if (v.degradation_enabled !== undefined) parts.push('degradation_enabled=' + v.degradation_enabled);
  if (v.fault_enabled !== undefined) parts.push('fault_enabled=' + v.fault_enabled);
  if (v.architecture !== undefined) parts.push('architecture=' + v.architecture);
  if (v.search_mode !== undefined) parts.push('search_mode=' + v.search_mode + '/top_k=' + v.top_k);
  if (v.slo_e2e_ms !== undefined) parts.push('slo_e2e=' + v.slo_e2e_ms + 'ms');
  if (v.slo_first_answer_ms !== undefined) parts.push('slo_first_answer=' + v.slo_first_answer_ms + 'ms');
  if (v.token_price_per_1k !== undefined) parts.push('token_price=' + v.token_price_per_1k);
  if (v.checkpoint_store !== undefined) parts.push('checkpoint_store=' + v.checkpoint_store);
  return parts.length ? parts.join('，') : '—（该阶段指纹无额外字段）';
}

export function renderHtml(board, context) {
  const ctx = context || {};
  const fp = board.fingerprint;
  // 三态判定横幅（契约 §5）：PASS=达标、FAIL=未达标、NOT_VERIFIED=证据/窗口缺失不可判
  const gateVerdict = (board.gate && board.gate.verdict) || GATE_NOT_VERIFIED;
  const gateClass = gateVerdict === GATE_PASS ? 'meets' : (gateVerdict === GATE_FAIL ? 'fails' : 'na');
  const gateText = gateVerdict === GATE_PASS ? 'MEETS SLA (PASS)'
    : (gateVerdict === GATE_FAIL ? 'FAILS SLA' : 'NOT VERIFIED');

  const verdictRows = board.thresholds.map(function (v) {
    return '<tr><td>' + esc(v.metric) + '</td><td>' + esc(v.threshold) + '</td><td class="'
      + (v.ok ? 'ok' : 'bad') + '">' + (v.ok ? 'MEETS' : 'FAILS') + '</td></tr>';
  }).join('\n');

  const gateReasonCodes = (board.gate && board.gate.reason_codes) || [];
  const gateRows = '<tr><td>三态门禁（契约 ' + esc(board.contractVersion || '') + '）</td><td>'
    + esc(gateReasonCodes.length ? gateReasonCodes.join('，') : '—') + '</td><td class="'
    + gateClass + '">' + esc(gateVerdict) + '</td></tr>';

  const scopeNote = board.bizScope
    ? '合格率/Goodput 与时延均取分档 <code>' + esc(board.bizScope) + '</code>（预热不计入判定）'
    : '合格率/Goodput 与时延取全量（无分档标签）';
  const goodputScopeNote = board.goodputScope === 'measured-window'
    ? 'Goodput 分母为 manifest measured 窗口实际秒数'
    : 'Goodput 分母为 k6 实测总时长（b0 含预热段）；measured 窗口接入后改用窗口秒数';

  const sections = [];

  // 安全层（三阶段）
  sections.push(sectionTable('安全层（护栏，缺失不冒充无违规）', [
    row('违规业务数', board.security.violating === null ? '—' : board.security.violating),
    row('违规条数', board.security.violations === null ? '—' : board.security.violations),
    row('安全数据缺失笔数', board.security.missing === null ? '—' : board.security.missing),
  ]));

  // 阶段二
  if (board.stage2.available) {
    const f = board.stage2.fanout || {};
    sections.push(sectionTable('阶段二观测（Multi-Agent，不进门禁）', [
      row('Agent 路径正确率', pct(board.stage2.agentPathOkRate)),
      row('降级业务占比', pct(board.stage2.degradedRate)),
      row('降级 Agent 次数', board.stage2.degradedAgents === null ? '—' : board.stage2.degradedAgents),
      row('断路器跳过次数（skipped_breaker）', board.stage2.breakerSkipped === null ? '—' : board.stage2.breakerSkipped),
      row('扇出并行效率（avg/min/max）', f.available ? fmt(f.efficiencyAvg, 3) + ' / ' + fmt(f.efficiencyMin, 3) + ' / ' + fmt(f.efficiencyMax, 3) : '—'),
      row('扇出墙钟 avg(ms)', fmt(f.wallAvg, 1)),
      row('串行和 avg(ms)', fmt(f.serialAvg, 1)),
    ]));
  }

  // 阶段三
  if (board.stage3.available) {
    sections.push(sectionTable('阶段三观测（Workflow+RAG；path/branch/intent/tool 进入合格判定）', [
      row('路径通过率 path_ok', pct(board.stage3.pathOkRate)),
      row('分支通过率 branch_ok', pct(board.stage3.branchOkRate)),
      row('意图通过率 intent_ok', pct(board.stage3.intentOkRate)),
      row('工具覆盖通过率 tool_ok', pct(board.stage3.toolOkRate)),
      row('首字 avg / P90 (ms)', fmt(board.stage3.firstAnswerAvg, 0) + ' / ' + fmt(board.stage3.firstAnswerP90, 0)),
      row('首字达标率（≤ 首字 SLO）', pct(board.stage3.firstAnswerOkRate)),
      row('节点执行错误数', board.stage3.nodeErrors === null ? '—' : board.stage3.nodeErrors),
      row('置信度 avg', fmt(board.stage3.confidenceAvg, 3)),
    ].concat(board.stage3.rag.available ? [
      row('RAG 检索 avg / P95 (ms)', fmt(board.stage3.rag.latencyAvg, 1) + ' / ' + fmt(board.stage3.rag.latencyP95, 1)),
      row('RAG 命中率（检索非空）', pct(board.stage3.rag.hitRate)),
      row('RAG 引用条数 avg', fmt(board.stage3.rag.citationsAvg, 2)),
    ] : [])));
  } else if (board.stage3.firstAnswerP90 !== null) {
    sections.push(sectionTable('首字观测（TTFT 口径，不进门禁）', [
      row('首字 avg / P90 (ms)', fmt(board.stage3.firstAnswerAvg, 0) + ' / ' + fmt(board.stage3.firstAnswerP90, 0)),
    ]));
  }

  // 通用观测项
  sections.push(sectionTable('通用观测项（不进门禁）', [
    row('工具覆盖率（expected_tools 全覆盖业务占比）', pct(board.toolRate)),
    row('工具覆盖检查业务数', board.toolChecked === null ? '—' : board.toolChecked),
    row('服务端 e2e_server_ms P90 / avg(ms，仅诊断)', fmt(board.e2eServerP90, 0) + ' / ' + fmt(board.e2eServerAvg, 0)),
    row('服务端 e2e 缺失笔数（不回退、不影响门禁）', board.e2eMissing === null ? '—' : board.e2eMissing),
    row('业务 Goodput 输入业务数（含预热）', board.bizTotal === null ? '—' : board.bizTotal),
    row('HTTP 请求数（含 setup 探活与预热）', board.httpReqs === null ? '—' : board.httpReqs),
  ]));

  let soakSection = '';
  if (board.soak.available) {
    const d = board.soak.degradation || { pct: null, ok: null, reason: '—' };
    const dText = d.pct === null ? '无法判定（' + esc(d.reason) + '）' : fmt(d.pct, 2) + '%';
    const dClass = d.ok === null ? 'num' : (d.ok ? 'ok' : 'bad');
    soakSection = '<h2>长稳首末段对比（方案 §5 末段劣化 ≤20%）</h2>\n'
      + '<table><tr><th>段位</th><th>样本数</th><th>E2E avg(ms)</th><th>E2E P90(ms)</th><th>合格率</th></tr>\n'
      + ['early', 'mid', 'late'].map(function (s) {
        const seg = board.soak[s] || {};
        return '<tr><td>' + esc(s) + '</td><td class="num">' + esc(seg.count === null || seg.count === undefined ? '—' : seg.count)
          + '</td><td class="num">' + fmt(seg.avg, 0) + '</td><td class="num">' + fmt(seg.p90, 0)
          + '</td><td class="num">' + pct(seg.qualifiedRate) + '</td></tr>';
      }).join('\n')
      + '</table>\n'
      + '<table><tr><th>末段劣化（late 对 early）</th><th>判定</th><th>口径</th></tr>\n'
      + '<tr><td class="' + dClass + '">' + dText + '</td><td class="' + (d.ok === null ? '' : (d.ok ? 'ok' : 'bad')) + '">'
      + (d.ok === null ? 'N/A' : (d.ok ? 'MEETS' : 'FAILS'))
      + '</td><td>' + esc(d.reason) + '；该判定不进 k6 退出码，仅作报告层结论</td></tr></table>\n';
  }

  const mismatchRows = board.caliber.mismatches.length
    ? board.caliber.mismatches.map(function (m) { return row('口径不一致', m); }).join('')
    : row('口径一致性', '本次判定参数与服务端指纹一致');

  const style = 'body{font-family:Consolas,monospace;margin:24px;background:#fafafa;color:#222}'
    + 'h1{font-size:18px}h2{font-size:14px;margin:20px 0 6px;border-left:4px solid #369;padding-left:8px}'
    + 'table{border-collapse:collapse;font-size:13px;background:#fff;margin-bottom:8px}'
    + 'td,th{border:1px solid #ccc;padding:4px 10px;text-align:left}'
    + 'td.num{text-align:right;font-variant-numeric:tabular-nums}'
    + 'td.ok{color:#0a7;font-weight:bold}td.bad{color:#d33;font-weight:bold}'
    + 'td.na{color:#b8860b;font-weight:bold}'
    + '.verdict{font-size:16px;font-weight:bold;padding:6px 10px;border:2px solid;display:inline-block;margin:8px 0}'
    + '.meets{color:#0a7;border-color:#0a7}.fails{color:#d33;border-color:#d33}.na{color:#b8860b;border-color:#b8860b}'
    + 'footer{margin-top:24px;color:#777;font-size:12px}';

  return '<!DOCTYPE html>\n<html lang="zh"><head><meta charset="utf-8"><title>'
    + esc(board.scenario) + ' 性能运行摘要（阶段' + esc(board.stageId) + '）</title>\n<style>' + style + '</style></head><body>\n'
    + '<h1>' + esc(board.scenario) + ' 性能运行摘要 · 阶段' + esc(board.stageId) + ' ' + esc(ctx.stageName || '') + '</h1>\n'
    + '<div class="verdict ' + gateClass + '">' + gateText + '</div>\n'
    + '<table>\n<tr><th>业务数</th><th>合格业务</th><th>合格率</th><th>Goodput(业务/秒)</th>'
    + '<th>E2E P90(ms，客户端墙钟)</th><th>E2E P95(ms)</th><th>每合格业务成本(元)</th><th>重试放大系数</th></tr>\n'
    + '<tr><td class="num">' + esc(board.bizCount === null ? '—' : board.bizCount) + '</td>'
    + '<td class="num">' + esc(board.qualifiedN === null ? '—' : board.qualifiedN) + '</td>'
    + '<td class="num">' + pct(board.qualifiedRate) + '</td>'
    + '<td class="num">' + fmt(board.goodput, 4) + '</td>'
    + '<td class="num">' + fmt(board.e2eP90, 0) + '</td><td class="num">' + fmt(board.e2eP95, 0) + '</td>'
    + '<td class="num">' + (board.costKnown ? fmt(board.cost, 4) : '不可计算（用量覆盖率 ' + fmt(board.coverage * 100, 1) + '%）') + '</td>'
    + '<td class="num">' + fmt(board.amplification, 3) + '</td></tr>\n</table>\n'
    + '<p>' + scopeNote + '；' + goodputScopeNote + '。错误率读数：' + pct(board.errorRate) + '（门禁 ≤0.5%）。</p>\n'
    + sections.join('')
    + (board.thresholds.length
      ? '<h2>阈值判定（与 k6 退出码一致）</h2><table><tr><th>指标</th><th>阈值</th><th>判定</th></tr>\n' + verdictRows + '</table>\n'
      : '')
    + '<h2>三态门禁（判定顺序：证据/窗口 → 阈值，契约 §5）</h2>\n<table><tr><th>门禁</th><th>原因码</th><th>判定</th></tr>\n'
    + gateRows + '</table>\n'
    + soakSection
    + '<h2>环境指纹与判定口径（方案 §4.1 启动准则）</h2>\n<table>\n'
    + fingerprintRows(fp, { probeError: ctx.probeError || '' })
    + row('本次判定参数', 'E2E SLO=' + fmt(board.caliber.sloMs, 0) + 'ms，首字 SLO='
      + (board.caliber.firstAnswerSloMs === null || board.caliber.firstAnswerSloMs === undefined ? '不适用' : fmt(board.caliber.firstAnswerSloMs, 0) + 'ms')
      + '，计价=' + fmt(board.caliber.costPer1k, 2) + ' 元每千 token')
    + row('口径来源', board.caliber.source || '—')
    + row('服务端指纹口径', 'slo_e2e=' + fmt(fp && fp.caliber ? fp.caliber.slo_e2e_ms : null, 0)
      + 'ms，slo_first_answer=' + fmt(fp && fp.caliber ? fp.caliber.slo_first_answer_ms : null, 0)
      + 'ms，token_price=' + fmt(fp && fp.caliber ? fp.caliber.token_price_per_1k : null, 3))
    + mismatchRows
    + row('批次 RUN_ID', ctx.runId || '—')
    + row('k6 版本', ctx.k6Version || '未注入（建议经 k6-suite/run-scenario.sh 运行）')
    + row('git HEAD', ctx.gitHead || '未注入')
    + row('工单集', (ctx.ticketsPath || '—') + '（' + (ctx.ticketCount === undefined ? '—' : ctx.ticketCount) + ' 条）')
    + '</table>\n'
    + '<footer>客服工单系统 k6 套件（共享实现 testing/k6-suite/） · 阶段' + esc(board.stageId) + ' · ' + esc(board.scenario)
    + ' · 生成于 ' + esc(board.stamp) + '（UTC）· 契约版本 ' + esc(board.contractVersion || '—')
    + '；E2E 门禁唯一来源为客户端墙钟 e2e_client_wall_ms，服务端 e2e_server_ms 只作诊断、不参与门禁；'
    + '成本为已知用量×假设单价。</footer>\n'
    + '</body></html>';
}

// 机器可读产物：契约版本 + 聚合看板（含三态门禁）+ 阈值判定 + 原始 k6 指标数据（供报告层对账）
export function renderJson(board, context, rawMetrics) {
  return JSON.stringify({
    contract_version: board.contractVersion || CONTRACT_VERSION,
    scenario: board.scenario,
    stage: board.stageId,
    generated_at: board.stamp,
    context: context || {},
    board: board,
    metrics: rawMetrics || {},
  }, null, 2);
}
