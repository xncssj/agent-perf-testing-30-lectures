// 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。
// k6 共享套件自测（纯 node，无 k6、无服务依赖）：node k6-suite/selfcheck.mjs
// 覆盖：四级判定 / 模糊回退 / 致歉模板 / 终态 / 双指标时延 / 安全层三态 / 工具覆盖与阶段三工具等效 /
//      阶段二 Agent 路径四类规则 / 阶段三 path·branch·intent 校验 / 阶段适配器请求体与视图 /
//      长稳段位与末段劣化 / 看板取数与降级路径 / HTML 转义与分区渲染
import { businessLevels, judgeResult, retrySignal, toolCoverage, toolsMatched, arraysEqual, agentPathOk, violationCount } from './lib/judge.js';
import { segmentOf, degradationPct, degradationVerdict } from './lib/segments.js';
import { computeBoard, computeLevelSummaries, renderHtml, fmt, windowsFromMetrics } from './lib/board.js';
import {
  CONTRACT_VERSION, QUALIFIED_RATE_MIN, ERROR_RATE_MAX,
  k6ThresholdExprs, evaluateGate, measuredSeconds,
} from './lib/contract.js';
import { stage1 } from './stages/stage1.js';
import { stage2 } from './stages/stage2.js';
import { stage3 } from './stages/stage3.js';
import { gatewayBusinessUrl, sessionTokenFromSetCookie, buildAuthHeaders } from './lib/gateway-headers.js';

let failed = 0;
function eq(name, got, want) {
  const ok = got === want;
  if (!ok) failed += 1;
  console.log((ok ? 'PASS' : 'FAIL') + ' ' + name + '：got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}
function near(name, got, want, tol) {
  const ok = typeof got === 'number' && Math.abs(got - want) <= tol;
  if (!ok) failed += 1;
  console.log((ok ? 'PASS' : 'FAIL') + ' ' + name + '：got=' + JSON.stringify(got) + ' want≈' + want + ' ±' + tol);
}
function ok(name, cond) { eq(name, !!cond, true); }
function throws(name, fn) {
  let threw = false;
  try { fn(); } catch (e) { threw = true; }
  eq(name, threw, true);
}

const SLO = 10000;
const ticket = {
  ticket_id: 'T00001',
  user_query: '我的订单到哪了',
  expected_answer_keywords: ['配送中'],
  expected_facts: { 当前位置: '上海' },
  expected_behavior: '',
  expected_tools: ['query_order', 'query_logistics'],
  expected_intent: 'logistics_query',
  expected_path: ['intent_recognition', 'data_retrieval', 'response_generation', 'confidence_check'],
  expected_branch: 'auto_reply',
  expected_agents: ['supervisor', 'order_agent', 'logistics_agent', 'validation_agent'],
};

function body1(overrides) {
  const b = {
    status: 'completed',
    response: '您的订单配送中，当前在上海。',
    metrics: { e2e_ms: 100, total_tokens: 1000, guardrail: { violations: [], blocked: false } },
    trace: { tool_calls: [{ tool: 'query_order', attempt: 1 }, { tool: 'query_logistics', attempt: 1 }] },
  };
  const o = overrides || {};
  const keys = Object.keys(o);
  for (let i = 0; i < keys.length; i++) b[keys[i]] = o[keys[i]];
  return b;
}
function judge1(ticketIn, body, opts) {
  const view = stage1.readView(body);
  view.wallMs = (opts && opts.wallMs !== undefined) ? opts.wallMs : 120;
  return stage1.judgeTicket(ticketIn || ticket, view, {
    httpOk: (opts && opts.httpOk !== undefined) ? opts.httpOk : true,
    sloMs: (opts && opts.sloMs !== undefined) ? opts.sloMs : SLO,
    firstAnswerSloMs: (opts && opts.firstAnswerSloMs !== undefined) ? opts.firstAnswerSloMs : null,
    relaxResult: opts && opts.relaxResult === true,
  });
}

// ---- A. 判定核心（阶段一）----
let r = judge1(ticket, body1());
eq('A1 全命中 qualified', r.levels.qualified, true);
eq('A1 全命中 security_ok', r.levels.security_ok, true);

r = judge1(ticket, body1({ response: '您的订单配送中。' }));
eq('A2 事实缺失 result_ok', r.levels.result_ok, false);
eq('A2 事实缺失 qualified', r.levels.qualified, false);

r = judge1(ticket, body1({ response: '当前在上海。' }));
eq('A3 关键词缺失 result_ok', r.levels.result_ok, false);

// A3b 结果口径回退开关（2026-10-14 v12real1 拍板 B 登记）：relaxResult 置位且业务完成时
// 关键词/事实 miss 回退为业务完成级；quality 缺失明细照常产出；默认关闭保持 formal 系列口径
r = judge1(ticket, body1({ response: '当前在上海。' }), { relaxResult: true });
eq('A3b 回退置位关键词缺失 result_ok', r.levels.result_ok, true);
eq('A3b 回退置位 qualified', r.levels.qualified, true);
eq('A3b 回退不隐藏缺失明细', r.quality.keywords_missed.length > 0, true);
r = judge1(ticket, body1({ response: '抱歉，我暂时无法回答。' }), { relaxResult: true });
eq('A3b 回退不救致歉模板（business_done 先失败）', r.levels.qualified, false);

r = judge1(ticket, body1({ status: 'failed' }));
eq('A4 终态失败 flow_done', r.levels.flow_done, false);
eq('A4 终态失败 qualified', r.levels.qualified, false);

r = judge1(ticket, body1({ response: '抱歉，我暂时无法回答。' }));
eq('A5 致歉模板 business_done', r.levels.business_done, false);

r = judge1({ ticket_id: 'T2', user_query: '你好' }, body1({ response: '您好，请问有什么可以帮您？' }));
eq('A6 模糊工单回退 result_ok', r.levels.result_ok, true);

r = judge1(ticket, body1({ metrics: { e2e_ms: 20000, total_tokens: 1000, guardrail: { violations: [] } } }));
eq('A7 服务端 e2e 超标仅诊断（门禁看墙钟）', r.levels.slo_ok, true);
eq('A7 服务端超标不阻断合格', r.levels.qualified, true);
eq('A7 服务端读数单独保留', r.e2eServerMs, 20000);

r = judge1(ticket, body1({ metrics: { e2e_ms: 100, total_tokens: 1000, guardrail: { violations: [] } } }), { wallMs: 20000 });
eq('A7b 墙钟超标 slo_ok 判否（门禁唯一来源）', r.levels.slo_ok, false);
eq('A7b 墙钟超标 qualified', r.levels.qualified, false);

r = judge1(ticket, body1({ metrics: { total_tokens: 1000, guardrail: { violations: [] } } }), { wallMs: 130 });
eq('A8 服务端 e2e 缺失不回退混入', r.e2eServerMs, null);
eq('A8 缺失仅计诊断', r.obs.e2eMissing, true);
eq('A8 门禁仍按墙钟判定', r.levels.slo_ok, true);
eq('A8 合格不受服务端缺失影响', r.levels.qualified, true);

const viewNoWall = stage1.readView(body1());
viewNoWall.wallMs = undefined; // 模拟墙钟不可测（正常路径 api.js 恒有墙钟，此处验证不冒充达标）
r = stage1.judgeTicket(ticket, viewNoWall, { httpOk: true, sloMs: SLO, firstAnswerSloMs: null });
eq('A8b 墙钟不可测不冒充达标', r.levels.slo_ok, false);
eq('A8b 墙钟不可测 qualified', r.levels.qualified, false);

r = judge1(ticket, null, { httpOk: false });
eq('A9 请求失败 request_ok', r.levels.request_ok, false);
eq('A9 请求失败 qualified', r.levels.qualified, false);

const rs = retrySignal({ tool_calls: [{ attempt: 1 }, { attempt: 2 }] });
eq('A10 重试放大 calls', rs.calls, 2);
eq('A10 重试放大 attempts', rs.attempts, 3);

r = judge1(ticket, body1({ metrics: { e2e_ms: 100, total_tokens: 10, guardrail: { violations: [{ type: 'sensitive', label: '手机号' }], blocked: true } } }));
eq('A11 护栏违规 security_ok', r.levels.security_ok, false);
eq('A11 护栏违规 qualified', r.levels.qualified, false);
eq('A11 违规条数', r.obs.guardrailViolations, 1);

r = judge1(ticket, body1({ metrics: { e2e_ms: 100, total_tokens: 10 } }));
eq('A12 护栏缺失 security_ok', r.levels.security_ok, null);
eq('A12 护栏缺失不阻断', r.levels.qualified, true);

// A13 故障窗边界判据（B4，2026-11-03）：已核实事实 + 明确声明未核实 = 合格；
// 缺任一半仍判否；无 fault_scenario 的普通票据口径不变
const faultTicket = Object.assign({}, ticket, {
  reference: { fault_scenario: { judge_boundary: {
    verified_facts: ['当前位置'], unverified_phrases: ['未核实'],
  } } },
});
let fr = judgeResult(faultTicket, '当前位置上海，物流信息未核实。', true);
eq('A13 降级边界 result_ok', fr.resultOk, true);
eq('A13 降级边界质量字段', fr.quality.fault_boundary.degraded_match, true);
fr = judgeResult(faultTicket, '当前位置上海。', true);
eq('A13 缺未核实声明 result_ok', fr.resultOk, false);
fr = judgeResult(faultTicket, '物流未核实。', true);
eq('A13 缺已核实事实 result_ok', fr.resultOk, false);
fr = judgeResult(faultTicket, '您的订单配送中，当前位置上海。', true);
eq('A13 完整事实仍算合格', fr.resultOk, true);
fr = judgeResult(ticket, '当前位置上海，物流未核实。', true);
eq('A13 非故障窗不启用边界判据', fr.resultOk, false);
const faultNoSpec = Object.assign({}, ticket, { reference: { fault_scenario: { expected_outcome: 'x' } } });
fr = judgeResult(faultNoSpec, '当前位置上海，未核实。', true);
eq('A13 未配边界时按全部事实校验', fr.resultOk, true);
fr = judgeResult(faultNoSpec, '当前位置上海，无法获取物流信息。', true);
eq('A13 同义未核实表述', fr.resultOk, true);

eq('A12 护栏缺失计数', r.obs.guardrailViolations, null);

// ---- B. 工具与路径规则 ----
eq('B1 工具覆盖 全覆盖', toolCoverage({ expected_tools: ['query_order'] }, { tool_calls: [{ tool: 'query_order' }] }), true);
eq('B1 工具覆盖 缺工具', toolCoverage({ expected_tools: ['query_order', 'query_logistics'] }, { tool_calls: [{ tool: 'query_order' }] }), false);
eq('B1 工具覆盖 空期望', toolCoverage({ expected_tools: [] }, { tool_calls: [] }), null);
eq('B1 工具覆盖 无调用', toolCoverage({ expected_tools: ['query_order'] }, {}), false);

eq('B2 阶段三工具 知识路径等效', toolsMatched({ expected_tools: ['search_knowledge'] }, [], ['intent_recognition', 'knowledge_retrieval']), true);
eq('B2 阶段三工具 知识路径缺失', toolsMatched({ expected_tools: ['search_knowledge'] }, [], ['intent_recognition', 'data_retrieval']), false);
eq('B2 阶段三工具 普通覆盖', toolsMatched({ expected_tools: ['query_order'] }, ['query_order'], []), true);
eq('B2 阶段三工具 普通缺失', toolsMatched({ expected_tools: ['query_order', 'query_logistics'] }, ['query_order'], []), false);
eq('B2 阶段三工具 空期望', toolsMatched({ expected_tools: [] }, [], []), true);

eq('B3 路径相等', arraysEqual(['a', 'b'], ['a', 'b']), true);
eq('B3 路径不等', arraysEqual(['a', 'b'], ['b', 'a']), false);
eq('B3 路径长度不等', arraysEqual(['a'], ['a', 'b']), false);

const telFull = { plan_agents: ['order_agent', 'logistics_agent', 'validation_agent'], validation: { status: 'ok' } };
eq('B4 路径 全覆盖', agentPathOk(ticket, telFull), true);
eq('B4 路径 缺专业 Agent', agentPathOk(ticket, { plan_agents: ['logistics_agent', 'validation_agent'], validation: { status: 'ok' } }), false);
eq('B4 路径 幽灵期望跳过', agentPathOk({ expected_agents: ['order_agent'], expected_tools: ['query_logistics'] }, { plan_agents: [] }), true);
eq('B4 路径 validation 状态缺失', agentPathOk(ticket, { plan_agents: ['order_agent', 'logistics_agent'], validation: {} }), false);
eq('B4 路径 supervisor 隐含', agentPathOk({ expected_agents: ['supervisor'], expected_tools: [] }, { plan_agents: [] }), true);

// ---- C. 阶段二适配 ----
eq('C1 阶段二请求体键', Object.keys(stage2.buildBody('b1', ticket)).sort().join(','), 'batch_id,stream,task_id,user_query');
eq('C1 阶段二端点', stage2.endpoint, '/api/v1/chat/complete');
eq('C1 阶段二默认端口', stage2.defaultBaseUrl, 'http://127.0.0.1:8001');

const body2 = body1({
  metrics: {
    e2e_ms: 120,
    total_tokens: 2000,
    guardrail: { violations: [] },
    trace: {
      plan_agents: ['logistics_agent', 'validation_agent'],
      validation: { status: 'ok' },
      degraded: true,
      degraded_agents: ['order_agent'],
      agents: [{ status: 'success' }, { status: 'skipped_breaker' }],
      fanout: { efficiency: 1.8, round_wall_ms: 300, serial_sum_ms: 540 },
      tool_calls: [{ tool: 'query_order', attempt: 1 }, { tool: 'query_logistics', attempt: 1 }],
    },
  },
  trace: { tool_calls: [{ tool: 'query_order', attempt: 1 }, { tool: 'query_logistics', attempt: 1 }] },
});
const view2 = stage2.readView(body2);
view2.wallMs = 130;
const out2 = stage2.judgeTicket(ticket, view2, { httpOk: true, sloMs: SLO, firstAnswerSloMs: null });
eq('C2 阶段二路径为观测项不阻断', out2.levels.qualified, true);
eq('C2 阶段二路径正确率观测', out2.obs.agentPathOk, false);
eq('C2 阶段二降级观测', out2.obs.degraded, true);
eq('C2 阶段二降级 Agent 数', out2.obs.degradedAgents, 1);
eq('C2 阶段二熔断跳过数', out2.obs.breakerSkipped, 1);
near('C2 阶段二扇出效率', out2.obs.fanoutEfficiency, 1.8, 0.0001);
near('C2 阶段二扇出墙钟', out2.obs.fanoutWallMs, 300, 0.0001);
near('C2 阶段二串行和', out2.obs.fanoutSerialMs, 540, 0.0001);
eq('C2 阶段二遥测取自 metrics.trace', view2.telemetry.plan_agents.length, 2);

// ---- D. 阶段三适配 ----
const b3 = stage3.buildBody('b1', ticket);
eq('D1 阶段三请求体键', Object.keys(b3).sort().join(','), 'batch_id,execution_id,query,task_id');
eq('D1 阶段三无契约外字段', b3.user_query === undefined && b3.stream === undefined, true);
eq('D1 阶段三默认端口', stage3.defaultBaseUrl, 'http://127.0.0.1:8002');
eq('D1 阶段三默认 SLO', stage3.defaultSloMs, 8000);
eq('D1 阶段三默认首字 SLO', stage3.defaultFirstAnswerSloMs, 2000);
eq('D1 阶段三默认计价', stage3.defaultCostPer1k, 0.15);

function body3(overrides) {
  const tel = {
    path: ['intent_recognition', 'data_retrieval', 'response_generation', 'confidence_check'],
    branch: 'auto_reply',
    intent: 'logistics_query',
    guardrail: { violations: [] },
    confidence: 0.92,
    node_exec_errors: [],
    tool_calls: [{ tool: 'query_order' }, { tool: 'query_logistics' }],
  };
  const o = overrides || {};
  if (o.tel) {
    const keys = Object.keys(o.tel);
    for (let i = 0; i < keys.length; i++) tel[keys[i]] = o.tel[keys[i]];
    delete o.tel;
  }
  const b = {
    status: 'completed',
    intent: 'logistics_query',
    response: '您的订单配送中，当前在上海。',
    metrics: { e2e_ms: 100, first_answer_ms: 800, total_tokens: 500, trace: tel },
    trace: tel,
  };
  const keys = Object.keys(o);
  for (let i = 0; i < keys.length; i++) b[keys[i]] = o[keys[i]];
  return b;
}
function judge3(ticketIn, body, opts) {
  const view = stage3.readView(body);
  view.wallMs = 120;
  return stage3.judgeTicket(ticketIn || ticket, view, {
    httpOk: true,
    sloMs: (opts && opts.sloMs !== undefined) ? opts.sloMs : 8000,
    firstAnswerSloMs: (opts && opts.firstAnswerSloMs !== undefined) ? opts.firstAnswerSloMs : 2000,
  });
}

let o3 = judge3(ticket, body3());
eq('D2 阶段三全维度通过 qualified', o3.levels.qualified, true);
eq('D2 阶段三路径维度', o3.dims.pathOk, true);
eq('D2 阶段三分支维度', o3.dims.branchOk, true);
eq('D2 阶段三意图维度', o3.dims.intentOk, true);
eq('D2 阶段三工具维度', o3.dims.toolOk, true);
eq('D2 阶段三首字达标', o3.levels.first_answer_ok, true);

o3 = judge3(ticket, body3({ tel: { path: ['intent_recognition', 'data_retrieval'] } }));
eq('D3 路径不一致 result_ok', o3.levels.result_ok, false);
eq('D3 路径不一致 qualified', o3.levels.qualified, false);
eq('D3 路径通过率为 false', o3.obs.pathOk, false);

o3 = judge3(ticket, body3({ tel: { branch: 'clarification' } }));
eq('D4 分支不一致 qualified', o3.levels.qualified, false);
eq('D4 分支通过率为 false', o3.obs.branchOk, false);

o3 = judge3(ticket, body3({ intent: 'other', tel: { intent: 'other' } }));
// 2026-11-03 降断言（老板拍板）：intentOk/toolOk 摘出 qualified、保留单列观测，
// 意图不一致不再拉低合格判定（c1low3 轮固定错档族归因见 base.js 注释）
eq('D5 意图不一致不拉低 qualified', o3.levels.qualified, true);
eq('D5 意图维度 false 仍单列', o3.dims.intentOk, false);

o3 = judge3(ticket, body3({ tel: { tool_calls: [{ tool: 'query_order' }] } }));
eq('D6 工具缺失不拉低 qualified', o3.levels.qualified, true);
eq('D6 工具维度 false', o3.dims.toolOk, false);

o3 = judge3({ expected_tools: ['search_knowledge'], expected_path: ['intent_recognition', 'knowledge_retrieval'], expected_branch: 'auto_reply', expected_intent: 'knowledge_query', expected_answer_keywords: ['退款'], expected_facts: {} },
  body3({ response: '退款可以这样申请。', intent: 'knowledge_query', tel: { path: ['intent_recognition', 'knowledge_retrieval'], branch: 'auto_reply', intent: 'knowledge_query', tool_calls: [] } }));
eq('D7 知识路径等效工具通过', o3.dims.toolOk, true);
eq('D7 知识路径整体合格', o3.levels.qualified, true);

o3 = judge3(ticket, body3({ tel: { retrieval: { hits: [{ doc_id: 'faq:退款到账时间' }], latency_ms: 1.5 }, citations: ['faq:退款到账时间'] } }));
eq('D12 知识请求 RAG 延迟观测', o3.obs.ragLatencyMs, 1.5);
eq('D12 知识请求 RAG 命中观测', o3.obs.ragHit, true);
eq('D12 知识请求 RAG 引用观测', o3.obs.ragCitations, 1);

o3 = judge3(ticket, body3({ tel: { retrieval: { hits: [], latency_ms: 0.8 } } }));
eq('D12 检索空结果命中为 false', o3.obs.ragHit, false);
eq('D12 检索空结果无引用', o3.obs.ragCitations, null);

o3 = judge3(ticket, body3());
eq('D12 数据类请求无 RAG 延迟样本', o3.obs.ragLatencyMs, null);
eq('D12 数据类请求无 RAG 命中样本', o3.obs.ragHit, null);
eq('D12 数据类请求无 RAG 引用样本', o3.obs.ragCitations, null);

o3 = judge3(ticket, body3({ metrics: { e2e_ms: 100, first_answer_ms: 2500, total_tokens: 500, trace: body3().metrics.trace } }));
eq('D8 首字超 SLO first_answer_ok', o3.levels.first_answer_ok, false);
eq('D8 首字超 SLO qualified', o3.levels.qualified, false);

const bodyNoFirst = body3();
delete bodyNoFirst.metrics.first_answer_ms;
o3 = judge3(ticket, bodyNoFirst);
eq('D9 首字缺失 first_answer_ok', o3.levels.first_answer_ok, null);
eq('D9 首字缺失不阻断', o3.levels.qualified, true);

o3 = judge3(ticket, body3({ tel: { guardrail: { violations: [{ type: 'sensitive', label: '手机号' }] } } }));
eq('D10 阶段三护栏取自 trace', o3.levels.security_ok, false);
eq('D10 阶段三护栏违规不通过', o3.levels.qualified, false);

o3 = judge3({ expected_answer_keywords: ['配送中'], expected_facts: {}, expected_tools: [], expected_path: [], expected_branch: '', expected_intent: '' },
  body3({ tel: { path: [], branch: '', intent: '' } }));
eq('D11 期望为空时阶段维度不适用', o3.levels.result_ok, true);
eq('D11 不适用时路径观测为 null', o3.obs.pathOk, null);

// ---- E. 段位与劣化 ----
eq('E1 段位 0', segmentOf(0), 'early');
eq('E1 段位 0.32', segmentOf(0.32), 'early');
eq('E1 段位 0.34', segmentOf(0.34), 'mid');
eq('E1 段位 0.66', segmentOf(0.66), 'mid');
eq('E1 段位 0.67', segmentOf(0.67), 'late');
eq('E1 段位 1', segmentOf(1), 'late');
eq('E1 段位 非法值', segmentOf(NaN), 'early');
near('E2 劣化 +15%', degradationPct(1000, 1150), 15, 0.001);
near('E2 劣化 +25%', degradationPct(1000, 1250), 25, 0.001);
eq('E2 劣化 首段为 0', degradationPct(0, 100), null);
eq('E2 劣化 样本缺失', degradationPct(null, 100), null);
eq('E2 劣化判定 15%', degradationVerdict(1000, 1150, 20).ok, true);
eq('E2 劣化判定 25%', degradationVerdict(1000, 1250, 20).ok, false);
eq('E2 劣化判定 样本不足', degradationVerdict(null, null, 20).ok, null);

// ---- F. 看板 ----
const metricsFull = {
  'business_total': { values: { count: 208 } },
  'business_qualified_rate': { values: { rate: 1 } },
  'business_measured_total': { values: { count: 205 } },
  'business_measured_qualified_rate': { values: { rate: 1 } },
  'http_req_failed': { values: { rate: 0 } },
  'http_req_failed{phase:measured}': { values: { rate: 0 } },
  'e2e_client_wall_ms{phase:measured}': { values: { avg: 3785.69, 'p(90)': 4586.63, 'p(95)': 5000 }, thresholds: { 'p(90)<=10000': { ok: true } } },
  'e2e_server_ms{phase:measured}': { values: { avg: 3600, 'p(90)': 4400 } },
  'tokens_total': { values: { count: 388671 } },
  'usage_missing': { values: { count: 0 } },
  'tool_calls_total': { values: { count: 410 } },
  'tool_attempts_total': { values: { count: 410 } },
  'tool_coverage_rate': { values: { rate: 1 } },
  'tool_checked_total': { values: { count: 185 } },
  'e2e_missing_total': { values: { count: 0 } },
  'security_violations_total': { values: { count: 0 } },
  'security_violating_total': { values: { count: 0 } },
  'security_missing_total': { values: { count: 0 } },
  'http_reqs': { values: { count: 210 } },
};
const dataFull = {
  metrics: metricsFull,
  state: { testRunDurationMs: 777812 },
  setup_data: { version: { app: 'customer-service-agent', version: '2.0.0', fingerprint: 'abc123' }, health: { status: 'ok' }, error: '', caliber: { slo_e2e_ms: null } },
};
const board = computeBoard(dataFull, { scenario: 'b0', stageId: '1', costPer1k: 2.0, sloMs: 10000, firstAnswerSloMs: null });
eq('F1 业务数取 measured', board.bizCount, 205);
eq('F1 合格业务数', board.qualifiedN, 205);
near('F1 Goodput', board.goodput, 205 / 777.812, 0.0001);
near('F1 E2E P90（客户端墙钟）', board.e2eP90, 4586.63, 0.001);
near('F1 服务端 e2e P90（诊断）', board.e2eServerP90, 4400, 0.001);
near('F1 每合格业务成本', board.cost, (388671 / 1000 * 2.0) / 208, 0.001); // B18② 同域：窗口 token 缺失时分母取全量合格数（208），不再混除计量窗笔数（205）
near('F1 重试放大系数', board.amplification, 1, 0.001);
eq('F1 阈值判定全过', board.allOk, true);
eq('F1 分档口径标注', board.bizScope, 'phase:measured');
eq('F1 安全层取数', board.security.violations, 0);
eq('F1 口径一致性', board.caliber.mismatches.length, 0);
eq('F1 Goodput 分母口径', board.goodputScope, 'total-run');
eq('F1 契约版本', board.contractVersion, 'load-test-remediation-v1');
// 无 manifest 上下文：按契约 §5 如实 NOT_VERIFIED，不冒充通过
eq('F1 缺 manifest 三态判定', board.gate.verdict, 'NOT_VERIFIED');
eq('F1 缺 manifest 原因码', board.gate.reason_codes.join(','), 'MISSING_MANIFEST');

// ---- F2. e1 型 Goodput 同域（B18① 分支一）：无 business_measured_total 时，分子全量，
// 计量窗 gauge 在场也不得取计量窗分母（pre3 e1drop 混除实测 5.338 vs 同域 2.881）
const metricsE1 = {
  'business_total': { values: { count: 124 } },
  'business_qualified_rate': { values: { rate: 1 } },
  'business_qualified_total': { values: { count: 124 } },
  'http_req_failed': { values: { rate: 0 } },
  'tokens_total': { values: { count: 224946 } },
  'usage_missing': { values: { count: 0 } },
  'window_measured_start_unix': { values: { value: 1790834203.513 } },
  'window_measured_end_unix': { values: { value: 1790834226.743 } },
  'security_violations_total': { values: { count: 0 } },
  'security_violating_total': { values: { count: 0 } },
  'security_missing_total': { values: { count: 0 } },
};
const dataE1 = { metrics: metricsE1, state: { testRunDurationMs: 43035 }, setup_data: { version: { app: 'customer-service-agent', version: '2.0.0', fingerprint: 'abc123' }, health: { status: 'ok' }, error: '', caliber: { slo_e2e_ms: null } } };
const boardE1 = computeBoard(dataE1, { scenario: 'e1', stageId: '1', costPer1k: 2.0, sloMs: 10000, firstAnswerSloMs: null });
eq('F2 e1 业务数回退全量', boardE1.bizCount, 124);
eq('F2 e1 Goodput 分母口径退 total-run', boardE1.goodputScope, 'total-run');
near('F2 e1 Goodput 同域', boardE1.goodput, 124 / 43.035, 0.0001);
near('F2 e1 成本同域', boardE1.cost, (224946 / 1000 * 2.0) / 124, 0.001);
eq('F2 e1 成本口径标注', boardE1.costScope, 'total-run');

// ---- F3. c1 型 Goodput 同域（B18① 分支二）：measured 计数器在场时取计量窗分母；
// 窗口 token 缺失（c1 族）成本分母取全量合格数（pre3 c1 混除 5.290 vs 同域 3.486）
const metricsC1 = {
  'business_total': { values: { count: 5932 } },
  'business_qualified_rate': { values: { rate: 1 } },
  'business_qualified_total': { values: { count: 5932 } },
  'business_measured_total': { values: { count: 3908 } },
  'business_measured_qualified_rate': { values: { rate: 1 } },
  'http_req_failed': { values: { rate: 0 } },
  'tokens_total': { values: { count: 10336369 } },
  'usage_missing': { values: { count: 0 } },
  'security_violations_total': { values: { count: 0 } },
  'security_violating_total': { values: { count: 0 } },
  'security_missing_total': { values: { count: 0 } },
};
const dataC1 = { metrics: metricsC1, state: { testRunDurationMs: 1085498 }, setup_data: { version: { app: 'customer-service-agent', version: '2.0.0', fingerprint: 'abc123' }, health: { status: 'ok' }, error: '', caliber: { slo_e2e_ms: null } } };
const boardC1 = computeBoard(dataC1, { scenario: 'c1', stageId: '1', costPer1k: 2.0, sloMs: 10000, firstAnswerSloMs: null, windows: { measured: { start: '2026-10-01T05:36:53.637Z', end: '2026-10-01T05:53:59.036Z' } } });
eq('F3 c1 业务数取 measured', boardC1.bizCount, 3908);
eq('F3 c1 Goodput 分母口径 measured-window', boardC1.goodputScope, 'measured-window');
near('F3 c1 Goodput 计量窗包络', boardC1.goodput, 3908 / 1025.399, 0.0001);
near('F3 c1 成本同域（全量÷全量）', boardC1.cost, (10336369 / 1000 * 2.0) / 5932, 0.001);
eq('F3 c1 成本口径标注', boardC1.costScope, 'total-run');

const boardNoMeasured = computeBoard({
  metrics: {
    'business_total': { values: { count: 120 } },
    'business_qualified_rate': { values: { rate: 0.95 } },
    'e2e_client_wall_ms': { values: { avg: 3000, 'p(90)': 4000 } },
  },
  state: { testRunDurationMs: 120000 },
}, { scenario: 's1', stageId: '2', costPer1k: 2.0, sloMs: 10000 });
eq('F2 回退全量业务数', boardNoMeasured.bizCount, 120);
eq('F2 回退全量合格数', boardNoMeasured.qualifiedN, 114);
eq('F2 回退全量口径标注', boardNoMeasured.bizScope, '');
near('F2 回退全量 P90', boardNoMeasured.e2eP90, 4000, 0.001);

const boardEmpty = computeBoard({ metrics: {}, state: { testRunDurationMs: 1000 } }, { scenario: 'b0', stageId: '1', costPer1k: 2.0, sloMs: 10000 });
eq('F3 降级 业务数', boardEmpty.bizCount, null);
eq('F3 降级 Goodput', boardEmpty.goodput, null);
eq('F3 降级 成本不可计算', boardEmpty.costKnown, false);
eq('F3 降级 无阈值视为通过', boardEmpty.allOk, true);
eq('F3 降级 空值渲染', fmt(null, 2), '—');
eq('F3 降级 无样本三态判定', boardEmpty.gate.verdict, 'NOT_VERIFIED');
// 判定顺序固定：manifest 校验在最前，原因码只含 MISSING_MANIFEST（契约 §5）
eq('F3 降级 原因码 MISSING_MANIFEST', boardEmpty.gate.reason_codes.join(','), 'MISSING_MANIFEST');

const metricsSoak = {
  'soak_e2e_early': { values: { avg: 1000, 'p(90)': 1100 } },
  'soak_e2e_mid': { values: { avg: 1150, 'p(90)': 1250 } },
  'soak_e2e_late': { values: { avg: 1300, 'p(90)': 1400 } },
  'soak_qualified_early': { values: { rate: 1, passes: 200, fails: 0 } },
  'soak_qualified_mid': { values: { rate: 1, passes: 200, fails: 0 } },
  'soak_qualified_late': { values: { rate: 0.99, passes: 198, fails: 2 } },
};
const boardSoak = computeBoard({ metrics: metricsSoak, state: { testRunDurationMs: 120000 } }, { scenario: 's1', stageId: '3' });
eq('F4 长稳段位可用', boardSoak.soak.available, true);
near('F4 长稳末段劣化', boardSoak.soak.degradation.pct, 30, 0.001);
eq('F4 长稳末段劣化判定', boardSoak.soak.degradation.ok, false);
eq('F4 长稳段位样本数', boardSoak.soak.mid.count, 200);

const metricsStage2 = Object.assign({}, metricsFull, {
  'ma_agent_path_ok': { values: { rate: 0.97 } },
  'ma_degraded_rate': { values: { rate: 0.01 } },
  'ma_degraded_agents_total': { values: { count: 3 } },
  'ma_breaker_skipped_total': { values: { count: 2 } },
  'ma_fanout_efficiency': { values: { avg: 1.62, min: 1.1, max: 2.3 } },
  'ma_fanout_wall_ms': { values: { avg: 320.5 } },
  'ma_fanout_serial_ms': { values: { avg: 520.4 } },
});
const board2 = computeBoard({ metrics: metricsStage2, state: { testRunDurationMs: 600000 }, setup_data: { version: { fingerprint: 'f2', orchestration: 'multi-agent', parallel_limit: 4 }, health: { status: 'ok' }, error: '', caliber: {} } },
  { scenario: 'b0', stageId: '2', costPer1k: 2.0, sloMs: 10000 });
eq('F5 阶段二分区可用', board2.stage2.available, true);
near('F5 阶段二路径正确率', board2.stage2.agentPathOkRate, 0.97, 0.0001);
eq('F5 阶段二熔断计数', board2.stage2.breakerSkipped, 2);
near('F5 阶段二扇出效率', board2.stage2.fanout.efficiencyAvg, 1.62, 0.0001);

const metricsStage3 = Object.assign({}, metricsFull, {
  'wf_path_ok': { values: { rate: 1 } },
  'wf_branch_ok': { values: { rate: 0.98 } },
  'wf_intent_ok': { values: { rate: 0.99 } },
  'wf_tool_ok': { values: { rate: 1 } },
  'wf_node_errors_total': { values: { count: 0 } },
  'wf_confidence': { values: { avg: 0.91 } },
  'server_first_answer_ms{phase:measured}': { values: { avg: 900, 'p(90)': 1400 } },
  'business_first_answer_ok': { values: { rate: 1 } },
  'wf_rag_latency_ms': { values: { avg: 1.2, 'p(95)': 2.5 } },
  'wf_rag_hit_rate': { values: { rate: 1, passes: 42, fails: 0 } },
  'wf_rag_citations': { values: { avg: 1.8 } },
});
const board3 = computeBoard({ metrics: metricsStage3, state: { testRunDurationMs: 600000 }, setup_data: { version: { fingerprint: 'f3', architecture: 'workflow+rag', slo_e2e_ms: 8000, slo_first_answer_ms: 2000, token_price_per_1k: 0.15 }, health: { status: 'ok' }, error: '', caliber: { slo_e2e_ms: 8000, slo_first_answer_ms: 2000, token_price_per_1k: 0.15 } } },
  { scenario: 'b0', stageId: '3', costPer1k: 0.15, sloMs: 8000, firstAnswerSloMs: 2000 });
eq('F6 阶段三分区可用', board3.stage3.available, true);
near('F6 阶段三分支通过率', board3.stage3.branchOkRate, 0.98, 0.0001);
near('F6 阶段三首字 P90', board3.stage3.firstAnswerP90, 1400, 0.001);
eq('F6 阶段三口径一致', board3.caliber.mismatches.length, 0);
eq('F6 RAG 分区可用', board3.stage3.rag.available, true);
near('F6 RAG 延迟 avg', board3.stage3.rag.latencyAvg, 1.2, 0.0001);
near('F6 RAG 延迟 P95', board3.stage3.rag.latencyP95, 2.5, 0.0001);
near('F6 RAG 命中率', board3.stage3.rag.hitRate, 1, 0.0001);
near('F6 RAG 引用 avg', board3.stage3.rag.citationsAvg, 1.8, 0.0001);

const board3Mismatch = computeBoard({ metrics: metricsStage3, state: { testRunDurationMs: 600000 }, setup_data: { version: { slo_e2e_ms: 8000, token_price_per_1k: 0.15 }, health: { status: 'ok' }, error: '', caliber: { slo_e2e_ms: 8000, token_price_per_1k: 0.15 } } },
  { scenario: 'b0', stageId: '3', costPer1k: 2.0, sloMs: 10000 });
eq('F7 口径不一致被标出', board3Mismatch.caliber.mismatches.length, 2);

// F8 三态门禁 PASS 路径：manifest + measured 窗口 + 三项达标（边界等值读数）
const boardGatePass = computeBoard({
  metrics: {
    'business_total': { values: { count: 100 } },
    'business_qualified_rate': { values: { rate: 0.9 } },
    'http_req_failed': { values: { rate: 0.005 } },
    'e2e_client_wall_ms': { values: { 'p(90)': 8000 } },
  },
  state: { testRunDurationMs: 120000 },
}, { scenario: 'c1', stageId: '1', costPer1k: 2.0, sloMs: 8000, manifest: { run_id: 'r-1' }, windows: { measured: { start: '2026-09-24T02:01:00Z', end: '2026-09-24T02:03:00Z' } } });
eq('F8 边界等值门禁 PASS（0.90/0.005/8000）', boardGatePass.gate.verdict, 'PASS');
eq('F8 无原因码', boardGatePass.gate.reason_codes.length, 0);
near('F8 Goodput 用窗口分母', boardGatePass.goodput, 90 / 120, 0.0001);
eq('F8 Goodput 口径标注', boardGatePass.goodputScope, 'measured-window');

// F9 窗口内 FAIL：错误率超限 → FAIL + 原因码（不再用二态表达）
const boardGateFail = computeBoard({
  metrics: {
    'business_total': { values: { count: 100 } },
    'business_qualified_rate': { values: { rate: 0.95 } },
    'http_req_failed': { values: { rate: 0.02 } },
    'e2e_client_wall_ms': { values: { 'p(90)': 5000 } },
  },
  state: { testRunDurationMs: 120000 },
}, { scenario: 'c1', stageId: '1', costPer1k: 2.0, sloMs: 8000, manifest: { run_id: 'r-2' }, windows: { measured: { start: '2026-09-24T02:01:00Z', end: '2026-09-24T02:03:00Z' } } });
eq('F9 错误率超限 FAIL', boardGateFail.gate.verdict, 'FAIL');
eq('F9 原因码 ERROR_RATE_ABOVE_MAX', boardGateFail.gate.reason_codes.join(','), 'ERROR_RATE_ABOVE_MAX');

// F10 计量范围统一（契约 §3.4）：存在计量窗口累计计数器时与 Goodput 同范围，缺失如实压低覆盖率
const boardMeasuredTokens = computeBoard({
  metrics: {
    'business_total': { values: { count: 120 } },
    'business_qualified_rate': { values: { rate: 1 } },
    'http_req_failed': { values: { rate: 0 } },
    'e2e_client_wall_ms': { values: { 'p(90)': 1000 } },
    'tokens_total': { values: { count: 999999 } },
    'usage_missing': { values: { count: 0 } },
    'window_measured_tokens_total': { values: { count: 240000 } },
    'window_measured_usage_missing_total': { values: { count: 0 } },
  },
  state: { testRunDurationMs: 120000 },
}, {
  scenario: 'b0', stageId: '1', costPer1k: 2.0, sloMs: 10000,
  windows: { warmup: { start: 'a', end: 'b' }, measured: { start: '2026-09-24T02:01:00Z', end: '2026-09-24T02:03:00Z' } },
});
eq('F10 Token 取计量窗口累计', boardMeasuredTokens.tokens, 240000);
eq('F10 Goodput 分母为 measured 窗口', boardMeasuredTokens.goodputScope, 'measured-window');
near('F10 Goodput', boardMeasuredTokens.goodput, 120 / 120, 0.0001);
near('F10 每合格业务成本', boardMeasuredTokens.cost, (240000 / 1000 * 2.0) / 120, 0.001);
const boardTokenMissing = computeBoard({
  metrics: {
    'business_total': { values: { count: 100 } },
    'business_qualified_rate': { values: { rate: 1 } },
    'window_measured_tokens_total': { values: { count: 1000 } },
    'window_measured_usage_missing_total': { values: { count: 3 } },
  },
  state: { testRunDurationMs: 1000 },
}, { scenario: 'b0', stageId: '1', costPer1k: 2.0, sloMs: 10000 });
eq('F10 窗口内缺失笔数如实计入', boardTokenMissing.usageMissing, 3);
eq('F10 缺失时成本不可计算', boardTokenMissing.costKnown, false);

// ---- I. C1 逐档容量摘要（契约 §4）----
const levelMetrics = {
  'http_req_failed': { values: { rate: 0 } },
  'http_req_failed{level:8,phase:measured}': { values: { rate: 0.004 } },
  'business_qualified_rate{level:8,phase:measured}': { values: { rate: 220 / 240, passes: 220, fails: 20 } },
  'e2e_client_wall_ms{level:8,phase:measured}': { values: { 'p(90)': 1800, 'p(99)': 2600 } },
  'e2e_server_ms{level:8,phase:measured}': { values: { 'p(90)': 1700 } },
};
const levelWindows = {
  8: {
    start: '2026-09-24T02:00:00Z', stabilizationEnd: '2026-09-24T02:01:00Z',
    measuredStart: '2026-09-24T02:01:00Z', end: '2026-09-24T02:03:00Z', plannedLoad: 8,
  },
};
const levels = computeLevelSummaries({ metrics: levelMetrics }, {
  contractVersion: 'load-test-remediation-v1', runId: 'r-l1', sloMs: 8000,
  levelWindows: levelWindows, samplesRef: 'raw/k6-samples.ndjson',
});
eq('I1 逐档摘要档数', levels.length, 1);
const L = levels[0];
eq('I1 level_id', L.level_id, 'c1-level-8');
eq('I1 planned_load', L.planned_load, 8);
eq('I1 business_total 取 measured 样本数', L.business_total, 240);
eq('I1 qualified_total 取 passes', L.qualified_total, 220);
eq('I1 measured_seconds 为窗口实际差值', L.measured_seconds, 120);
eq('I1 stabilization_seconds', L.stabilization_seconds, 60);
near('I1 goodput = 合格 ÷ 计量秒', L.goodput_per_second, 220 / 120, 0.0001);
near('I1 墙钟 P90', L.e2e_client_wall_p90_ms, 1800, 0.001);
near('I1 墙钟 P99', L.e2e_client_wall_p99_ms, 2600, 0.001);
near('I1 服务端 P90 诊断', L.e2e_server_p90_ms, 1700, 0.001);
eq('I1 manifest 未接入时门禁如实 NOT_VERIFIED', L.gate.verdict, 'NOT_VERIFIED');
eq('I1 原因码 MISSING_MANIFEST', L.gate.reasons.join(','), 'MISSING_MANIFEST');
eq('I1 证据索引含样本引用', L.evidence.samples, 'raw/k6-samples.ndjson');
eq('I1 manifest 槽位保留为空', L.evidence.manifest, null);

const levelsEmpty = computeLevelSummaries({ metrics: {} }, {
  contractVersion: 'load-test-remediation-v1', runId: 'r-l2', sloMs: 8000, levelWindows: {},
});
eq('I2 无档位窗口时输出空清单', levelsEmpty.length, 0);

// ---- J. 运行窗口还原（边界 Gauge → ISO 窗口）----
const wins = windowsFromMetrics({
  'window_warmup_start_unix': { values: { value: 1790221200.0 } },
  'window_warmup_end_unix': { values: { value: 1790221203.0 } },
  'window_measured_start_unix': { values: { value: 1790221203.0 } },
  'window_measured_end_unix': { values: { value: 1790221333.0 } },
  'c1_l8_start_unix': { values: { value: 1790221200.0 } },
  'c1_l8_end_unix': { values: { value: 1790221380.0 } },
}, { levels: [8], stabilizationSeconds: 60 });
eq('J1 warmup 窗口', JSON.stringify(wins.warmup.start.length), '24');
near('J1 warmup 时长秒', (Date.parse(wins.warmup.end) - Date.parse(wins.warmup.start)) / 1000, 3, 0.001);
near('J1 measured 时长秒', (Date.parse(wins.measured.end) - Date.parse(wins.measured.start)) / 1000, 130, 0.001);
eq('J1 档位起点为 ISO', wins.levels[8].start, new Date(1790221200 * 1000).toISOString());
near('J1 档位稳定期秒', (Date.parse(wins.levels[8].stabilizationEnd) - Date.parse(wins.levels[8].start)) / 1000, 60, 0.001);
eq('J1 稳定期结束即计量开始', wins.levels[8].measuredStart, wins.levels[8].stabilizationEnd);
eq('J1 planned_load', wins.levels[8].plannedLoad, 8);

const winsMissing = windowsFromMetrics({ 'window_measured_end_unix': { values: { value: 1790221333.0 } } }, { levels: [] });
eq('J2 起点缺失时窗口为 null', winsMissing.measured, null);
eq('J2 无 warmup', winsMissing.warmup, null);
eq('J2 无档位', Object.keys(winsMissing.levels).length, 0);

// ---- G. HTML 渲染 ----
const html = renderHtml(computeBoard({
  metrics: metricsStage3,
  state: { testRunDurationMs: 777812 },
  setup_data: { version: { app: '<script>alert(1)</script>', fingerprint: 'abc', architecture: 'workflow+rag' }, health: { status: 'ok' }, error: '', caliber: {} },
}, { scenario: 'b0', stageId: '3', sloMs: 8000, firstAnswerSloMs: 2000, costPer1k: 0.15, caliberSource: 'fingerprint' }), {
  scenario: 'b0', stageId: '3', stageName: 'Workflow+RAG', runId: 'k6-test', k6Version: 'k6 v1.6.1',
  gitHead: 'deadbeef', ticketsPath: 'test-data/tickets.json', ticketCount: 205, sloMs: 8000,
  firstAnswerSloMs: 2000, costPer1k: 0.15, caliberSource: 'fingerprint',
});
eq('G1 含判定区块', html.indexOf('阈值判定') >= 0, true);
eq('G2 含安全层区块', html.indexOf('安全层') >= 0, true);
eq('G3 含阶段三区块', html.indexOf('阶段三观测') >= 0, true);
eq('G4 含通用观测区块', html.indexOf('通用观测项') >= 0, true);
eq('G5 含指纹区块', html.indexOf('环境指纹与判定口径') >= 0, true);
eq('G6 含口径来源', html.indexOf('指纹') >= 0 && html.indexOf('fingerprint') >= 0, true);
eq('G7 转义脚本注入', html.indexOf('<script>') < 0, true);
const htmlStage1 = renderHtml(board, { stageId: '1', stageName: 'Single Agent' });
eq('G8 阶段一无阶段分区', htmlStage1.indexOf('阶段三观测') < 0, true);
eq('G9 阶段二分区按指标出现', renderHtml(board2, { stageId: '2' }).indexOf('阶段二观测') >= 0, true);
eq('G10 长稳区块按需出现', renderHtml(boardSoak, { stageId: '3' }).indexOf('长稳首末段对比') >= 0, true);
eq('G11 三态门禁区块出现', html.indexOf('三态门禁') >= 0, true);
eq('G12 契约版本入页脚', html.indexOf('load-test-remediation-v1') >= 0, true);

// ---- H. 契约（阈值唯一来源、等值边界、三态优先级、measured 窗口）----
eq('H1 契约版本', CONTRACT_VERSION, 'load-test-remediation-v1');
eq('H1 合格率阈值', QUALIFIED_RATE_MIN, 0.90);
eq('H1 错误率阈值', ERROR_RATE_MAX, 0.005);

const exprs = k6ThresholdExprs(8000);
eq('H2 合格率表达式（等值边界）', exprs.qualifiedRate, 'rate>=0.9');
eq('H2 错误率表达式（等值边界）', exprs.errorRate, 'rate<=0.005');
eq('H2 E2E 表达式（等值边界）', exprs.e2eP90, 'p(90)<=8000');

// V02 边界等值：恰好等于阈值时 PASS
let g = evaluateGate({
  hasManifest: true, qualifiedRate: 0.90, errorRate: 0.005, e2eP90Ms: 8000, sloMs: 8000,
});
eq('H3 三项等值 PASS', g.verdict, 'PASS');
g = evaluateGate({ hasManifest: true, qualifiedRate: 0.90, errorRate: 0.005, e2eP90Ms: 8000.01, sloMs: 8000 });
eq('H3 E2E 超边界 FAIL', g.verdict, 'FAIL');
eq('H3 E2E 原因码', g.reason_codes.join(','), 'E2E_SLO_EXCEEDED');
g = evaluateGate({ hasManifest: true, qualifiedRate: 0.899, errorRate: 0.005, e2eP90Ms: 1000, sloMs: 8000 });
eq('H3 合格率差边界 FAIL', g.verdict, 'FAIL');
eq('H3 合格率原因码', g.reason_codes.join(','), 'QUALIFIED_RATE_BELOW_MIN');
g = evaluateGate({ hasManifest: true, qualifiedRate: 0.95, errorRate: 0.005001, e2eP90Ms: 1000, sloMs: 8000 });
eq('H3 错误率差边界 FAIL', g.verdict, 'FAIL');
eq('H3 错误率原因码', g.reason_codes.join(','), 'ERROR_RATE_ABOVE_MAX');

// 三态优先级：证据/窗口缺失压过阈值结论（V10 单元级前置）
g = evaluateGate({ hasManifest: false, qualifiedRate: 0.2, errorRate: 0.5, e2eP90Ms: 99999, sloMs: 8000 });
eq('H4 缺 manifest 即使阈值全崩也 NOT_VERIFIED', g.verdict, 'NOT_VERIFIED');
ok('H4 原因码含 MISSING_MANIFEST', g.reason_codes.indexOf('MISSING_MANIFEST') >= 0);
g = evaluateGate({
  hasManifest: true, windowAligned: false, qualifiedRate: 0.95, errorRate: 0.001, e2eP90Ms: 1000, sloMs: 8000,
});
eq('H4 窗口不齐 NOT_VERIFIED', g.verdict, 'NOT_VERIFIED');
eq('H4 窗口原因码', g.reason_codes.join(','), 'WINDOW_MISMATCH');
g = evaluateGate({
  hasManifest: true,
  evidence: [{ name: 'events/business.ndjson', present: false }],
  qualifiedRate: 0.95, errorRate: 0.001, e2eP90Ms: 1000, sloMs: 8000,
});
eq('H4 安全证据缺失 NOT_VERIFIED', g.verdict, 'NOT_VERIFIED');
eq('H4 证据原因码', g.reason_codes.join(','), 'MISSING_SAFETY_EVIDENCE');
g = evaluateGate({
  hasManifest: true, fused: true, qualifiedRate: 0.95, errorRate: 0.001, e2eP90Ms: 1000, sloMs: 8000,
});
eq('H4 熔断窗口 NOT_VERIFIED', g.verdict, 'NOT_VERIFIED');
eq('H4 熔断原因码', g.reason_codes.join(','), 'FUSED');
g = evaluateGate({ hasManifest: true, qualifiedRate: null, errorRate: 0.001, e2eP90Ms: 1000, sloMs: 8000 });
eq('H4 指标缺失不冒充零值', g.verdict, 'NOT_VERIFIED');
eq('H4 缺指标原因码', g.reason_codes.join(','), 'INSUFFICIENT_SAMPLES');

// measured 窗口秒数：只认起止差值
eq('H5 窗口秒数', measuredSeconds({ measured: { start: '2026-09-24T02:01:00Z', end: '2026-09-24T02:03:00Z' } }), 120);
eq('H5 窗口缺失', measuredSeconds(undefined), null);
eq('H5 起止倒挂', measuredSeconds({ measured: { start: '2026-09-24T02:03:00Z', end: '2026-09-24T02:01:00Z' } }), null);
eq('H5 不可解析', measuredSeconds({ measured: { start: 'x', end: 'y' } }), null);

// 网关链路纯函数（gateway-headers.js）：URL 拼接 / 会话 Cookie 解析 / 认证头构造
eq('GW 阶段一 URL', gatewayBusinessUrl('https://<IP>', '1', '/api/v1/chat/complete'), 'https://<IP>/single/api/v1/chat/complete');
eq('GW 阶段三 URL', gatewayBusinessUrl('https://<IP>/', '3', '/api/v1/chat/complete'), 'https://<IP>/workflow/api/v1/chat/complete');
throws('GW 非法阶段拒绝', () => gatewayBusinessUrl('https://<IP>', '9', '/api/v1/chat/complete'));
throws('GW 空 GATEWAY_URL 拒绝', () => gatewayBusinessUrl('', '1', '/api/v1/chat/complete'));
throws('GW endpoint 非 / 开头拒绝', () => gatewayBusinessUrl('https://<IP>', '1', 'api/v1'));
eq('GW Cookie 解析', sessionTokenFromSetCookie('__Host-cs-session=abc123; Secure; HttpOnly; SameSite=Strict; Path=/'), 'abc123');
eq('GW Cookie 数组形态', sessionTokenFromSetCookie(['__Host-cs-session=tok,other=x']), 'tok');
eq('GW Cookie 缺失', sessionTokenFromSetCookie(''), '');
eq('GW 认证头', JSON.stringify(buildAuthHeaders('csrf1', 'https://<IP>')), JSON.stringify({ 'X-CSRF-Token': 'csrf1', Origin: 'https://<IP>' }));
throws('GW 认证头缺 CSRF 拒绝', () => buildAuthHeaders('', 'https://<IP>'));

console.log(failed ? ('selfcheck 失败 ' + failed + ' 项') : 'selfcheck 全部通过');
process.exit(failed ? 1 : 0);
