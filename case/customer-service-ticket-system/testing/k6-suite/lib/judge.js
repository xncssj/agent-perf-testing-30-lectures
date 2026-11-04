// 判定公共模块（纯 ESM，无 k6 依赖）：四级判定、安全层、双指标时延与阶段共用助手。
// 口径与三阶段 tests/performance/common.py + src/utils/metrics.py business_levels 对齐：
//   qualified = request_ok AND flow_done AND business_done AND result_ok AND slo_ok
//               AND security_ok is not False
//   - slo_ok：完整答复 E2E ≤ slo_ms；若给出首字门槛（阶段三）则 AND 首字 ≤ first_answer_slo_ms
//   - security_ok：护栏结果存在时 = (违规条数 == 0)；缺失记 null（不适用），不冒充“无违规”
// 兼容约定：为避免 k6（goja）运行时差异，本套件不使用 ?? / ?. 语法。

export const APOLOGY_PREFIX = '抱歉，我暂时无法回答';

// 安全层：从护栏结果取违规条数；缺失或形状异常返回 0 条（与 harness/guardrail.py violation_count 同义）
export function violationCount(guardrail) {
  if (!guardrail || typeof guardrail !== 'object') return 0;
  const v = guardrail.violations;
  return Array.isArray(v) ? v.length : 0;
}

// 业务判定：四级 + 时延（E2E/首字）+ 安全三层，返回逐级结论（对齐 business_levels）
export function businessLevels(input) {
  const requestOk = !!input.requestOk;
  const flowDone = !!input.flowDone;
  const businessDone = !!input.businessDone;
  const resultOk = !!input.resultOk;

  let sloOk = true;
  if (input.sloMs !== null && input.sloMs !== undefined) {
    sloOk = Number(input.latencyMs) <= Number(input.sloMs);
  }
  let firstAnswerOk = null;
  if (input.firstAnswerSloMs !== null && input.firstAnswerSloMs !== undefined
    && input.firstAnswerMs !== null && input.firstAnswerMs !== undefined) {
    firstAnswerOk = Number(input.firstAnswerMs) <= Number(input.firstAnswerSloMs);
    sloOk = sloOk && firstAnswerOk;
  }
  let securityOk = null;
  if (input.guardrail !== null && input.guardrail !== undefined) {
    securityOk = violationCount(input.guardrail) === 0;
  }

  return {
    request_ok: requestOk,
    flow_done: flowDone,
    business_done: businessDone,
    result_ok: resultOk,
    slo_ok: sloOk,
    first_answer_ok: firstAnswerOk,
    security_ok: securityOk,
    qualified: requestOk && flowDone && businessDone && resultOk && sloOk && securityOk !== false,
  };
}

// 故障窗边界判据（B4，2026-11-03 阶段二三真实模型轮）：票据带 reference.fault_scenario 时，
// 答复允许两种形态——①完整事实（自愈后/未命中故障）；②降级边界：已核实事实必须命中，
// 且必须明确声明未核实的那部分。修 09-27 因缺「物流未核实」判 FAIL 的边界缺陷。
export const DEFAULT_UNVERIFIED_PHRASES = ['未核实', '无法核实', '未确认', '无法确认', '无法获取', '未获取', '暂未查询到', '未查询到'];

export function faultBoundary(ticket) {
  var ref = (ticket && ticket.reference) || {};
  var fault = ref.fault_scenario;
  if (!fault || typeof fault !== 'object') return null;
  var spec = fault.judge_boundary || {};
  var verified = Array.isArray(spec.verified_facts) ? spec.verified_facts : null;
  var extra = Array.isArray(spec.unverified_phrases) ? spec.unverified_phrases : [];
  var phrases = DEFAULT_UNVERIFIED_PHRASES.slice();
  for (var i = 0; i < extra.length; i++) {
    if (phrases.indexOf(extra[i]) < 0) phrases.push(extra[i]);
  }
  return { verifiedFacts: verified, phrases: phrases };
}

// 四级判定的“结果合格”部分：关键词 AND 事实 AND 行为；关键词与事实均空时回退为业务完成
export function judgeResult(ticket, response, businessDone) {
  const text = typeof response === 'string' ? response : '';
  const keywords = (ticket.expected_answer_keywords || ticket.expected_answer_contains || [])
    .filter(function (k) { return !!k; });
  const factKeys = Object.keys(ticket.expected_facts || {});
  const facts = [];
  for (let i = 0; i < factKeys.length; i++) {
    const v = ticket.expected_facts[factKeys[i]];
    if (v !== null && v !== undefined && String(v) !== '') facts.push(String(v));
  }

  let resultOk;
  if (keywords.length || facts.length) {
    resultOk = keywords.every(function (k) { return text.indexOf(k) >= 0; })
      && facts.every(function (v) { return text.indexOf(v) >= 0; });
  } else {
    resultOk = !!businessDone; // 模糊工单回退（与 common.py 一致）
  }
  const behaviorHit = ticket.expected_behavior ? text.indexOf(ticket.expected_behavior) >= 0 : null;
  if (ticket.expected_behavior) resultOk = resultOk && behaviorHit;

  // 故障窗降级边界：完整事实不成立时，已核实事实命中 + 明确声明未核实 也算结果合格
  const fault = faultBoundary(ticket);
  const fullOk = resultOk;
  let degradedOk = null;
  let unverifiedHit = null;
  if (fault) {
    const verifiedKeys = fault.verifiedFacts || factKeys;
    const verifiedValues = [];
    for (let i = 0; i < verifiedKeys.length; i++) {
      const value = (ticket.expected_facts || {})[verifiedKeys[i]];
      if (value !== null && value !== undefined && String(value) !== '') verifiedValues.push(String(value));
    }
    unverifiedHit = false;
    for (let j = 0; j < fault.phrases.length; j++) {
      if (text.indexOf(fault.phrases[j]) >= 0) { unverifiedHit = true; break; }
    }
    const verifiedHit = verifiedValues.every(function (v) { return text.indexOf(v) >= 0; });
    degradedOk = unverifiedHit && verifiedHit;
    resultOk = fullOk || degradedOk;
  }

  // 评测回填：逐笔要素命中/缺失明细（与 common.py quality 字段同义）；
  // missed 列表供逐笔判定输出（EMIT_JUDGEMENT）复核「未命中具体是哪几条」
  const hitKeywords = keywords.filter(function (k) { return text.indexOf(k) >= 0; });
  const hitFacts = facts.filter(function (v) { return text.indexOf(v) >= 0; });
  const missedKeywords = keywords.filter(function (k) { return text.indexOf(k) < 0; });
  const missedFacts = facts.filter(function (v) { return text.indexOf(v) < 0; });
  return {
    resultOk: resultOk,
    quality: {
      keywords_expected: keywords.length,
      keywords_hit: hitKeywords.length,
      keywords_missed: missedKeywords,
      facts_expected: facts.length,
      facts_hit: hitFacts.length,
      facts_missed: missedFacts,
      behavior_expected: ticket.expected_behavior || '',
      behavior_hit: behaviorHit,
      fault_boundary: fault ? {
        full_match: fullOk,
        degraded_match: degradedOk,
        unverified_phrase_hit: unverifiedHit,
      } : null,
    },
  };
}

// 工具重试放大信号：放大系数 = 尝试总数 ÷ 逻辑调用数（尝试 > 调用说明发生了自动重试）
export function retrySignal(trace) {
  const calls = (trace && trace.tool_calls) || [];
  let attempts = 0;
  for (let i = 0; i < calls.length; i++) {
    const c = calls[i];
    attempts += Number(c.attempt || c.attempts || 1);
  }
  return { calls: calls.length, attempts: attempts };
}

// 工具覆盖（阶段一/二观测项，非门禁）：实际调用的工具集合是否覆盖 expected_tools。
// 口径说明：课程性能指标口径 §1 的 Tool Accuracy 为「正确工具选择数 ÷ 总选择数」，
// 本处为「expected_tools 全覆盖的业务占比」（分母为期望非空的业务），两者不可直接比较。
export function toolCoverage(ticket, trace) {
  const expected = (ticket.expected_tools || []).filter(function (t) { return !!t; });
  if (!expected.length) return null; // 空期望（如澄清类工单）不计入分母
  return containsAll(expected, actualTools(trace));
}

// 阶段三工具校验：search_knowledge 以路径含 knowledge_retrieval 节点等效满足（对齐 common.py tools_matched）
export function toolsMatched(ticket, tools, path) {
  const expected = (ticket.expected_tools || []).filter(function (t) { return !!t; });
  if (!expected.length) return true;
  if (expected.indexOf('search_knowledge') >= 0) {
    return (path || []).indexOf('knowledge_retrieval') >= 0;
  }
  return containsAll(expected, tools || []);
}

export function actualTools(trace) {
  const calls = (trace && trace.tool_calls) || [];
  const names = [];
  for (let i = 0; i < calls.length; i++) {
    const n = calls[i] && calls[i].tool;
    if (n && names.indexOf(n) < 0) names.push(n);
  }
  return names;
}

function containsAll(expected, actual) {
  const have = {};
  for (let i = 0; i < (actual || []).length; i++) have[actual[i]] = true;
  for (let j = 0; j < expected.length; j++) {
    if (!have[expected[j]]) return false;
  }
  return true;
}

// 数组逐项相等（阶段三 path 校验）
export function arraysEqual(a, b) {
  const x = a || [];
  const y = b || [];
  if (x.length !== y.length) return false;
  for (let i = 0; i < x.length; i++) {
    if (x[i] !== y[i]) return false;
  }
  return true;
}

// 阶段二 Agent 路径校验（对齐 common.py agent_path_ok）：
// - supervisor 为隐含执行者，不参与比对；
// - 专业 Agent 以其负责的工具为准，expected_agents 中出现但工具不在 expected_tools 的“幽灵期望”不强制；
// - validation_agent 以 trace.validation.status 非空为满足。
export const AGENT_TOOLS = {
  order_agent: 'query_order',
  logistics_agent: 'query_logistics',
  knowledge_agent: 'search_knowledge',
};

export function agentPathOk(ticket, telemetry) {
  const t = telemetry || {};
  const plan = t.plan_agents || [];
  const validation = t.validation || {};
  const expectedTools = ticket.expected_tools || [];
  const expectedAgents = ticket.expected_agents || [];
  for (let i = 0; i < expectedAgents.length; i++) {
    const expected = expectedAgents[i];
    if (expected === 'supervisor') continue;
    if (expected === 'validation_agent') {
      if (!validation.status) return false;
      continue;
    }
    const tool = AGENT_TOOLS[expected];
    if (tool !== undefined && expectedTools.indexOf(tool) < 0) continue; // 幽灵期望
    if (plan.indexOf(expected) < 0) return false;
  }
  return true;
}
