// 业务调用封装（k6 依赖）：按阶段适配器构造请求 → 判定 → 记录核心与阶段观测指标。
// 口径：
// - 一笔请求 = 一笔业务（非流式接口）
// - 每笔请求写入 batch_id（RUN_ID）与 task_id（工单 ID），与服务端 Trace/Prometheus 对齐
// - 指标 tag 只带 phase/level 等低基数值；task_id 标签默认关闭（TAG_TASK_ID=1 时开启）
// - 业务数与合格数走 business_total / business_measured_*（setup 探活请求不进该计数）；
//   business_qualified_total 记合格笔数绝对值，是 Goodput 与成功业务成本的分子
import http from 'k6/http';
import { Counter, Rate, Trend } from 'k6/metrics';
import { RUN_ID, TAG_TASK_ID, EMIT_JUDGEMENT } from './config.js';
import { gatewayAuthParams, forceGatewayRelogin } from './gateway-auth.js';
import { E2E_CLIENT_WALL_METRIC, E2E_SERVER_METRIC } from './contract.js';
import { actualTools } from './judge.js';

// ---- 核心指标（三阶段共用口径）----
const businessTotal = new Counter('business_total');
const qualifiedTotal = new Counter('business_qualified_total');
const measuredTotal = new Counter('business_measured_total');
const qualifiedRate = new Rate('business_qualified_rate');
const measuredQualified = new Rate('business_measured_qualified_rate');
// 协议层失败单列（第 12 讲断言分层）：HTTP 200 但响应体不可解析——不计业务不合格，
// 也不冒充传输成功；与 business_total、qualified 相加可对账三类信号是否互斥
const payloadBadTotal = new Counter('business_payload_bad_total');
// E2E 双口径（契约 §3.1）：客户端墙钟是唯一门禁来源；服务端值只诊断，两者不落同一分布
const wallTrend = new Trend(E2E_CLIENT_WALL_METRIC, true);   // 客户端墙钟（门禁：P90 ≤ E2E SLO）
const serverE2eTrend = new Trend(E2E_SERVER_METRIC, true);   // 服务端 metrics.e2e_ms（仅诊断）
const firstAnswerTrend = new Trend('server_first_answer_ms', true);
const firstAnswerOkRate = new Rate('business_first_answer_ok');
const tokensTotal = new Counter('tokens_total');
const usageMissing = new Counter('usage_missing');
const toolCallsTotal = new Counter('tool_calls_total');
const toolAttemptsTotal = new Counter('tool_attempts_total');
const toolChecked = new Counter('tool_checked_total');
const toolCoverageRate = new Rate('tool_coverage_rate');
const e2eMissing = new Counter('e2e_missing_total');   // 服务端 e2e_server_ms 缺失笔数（诊断项，不影响门禁）
const securityViolations = new Counter('security_violations_total');
const securityViolating = new Counter('security_violating_total');
const securityMissing = new Counter('security_missing_total');
// 协作行为观测（契约课程场景 E3，不进门禁）：排队代理 = 客户端墙钟 − 服务端 e2e
//（含网络与排队差异，只作代理读数）；积压量由业务监控采样服务端 agent_runs_inflight 对账
const queueProxyTrend = new Trend('collab_queue_proxy_ms', true);

// ---- 阶段二观测（Multi-Agent）----
const maAgentPathOk = new Rate('ma_agent_path_ok');
const maDegraded = new Rate('ma_degraded_rate');
const maDegradedAgents = new Counter('ma_degraded_agents_total');
const maBreakerSkipped = new Counter('ma_breaker_skipped_total');
const maFanoutEfficiency = new Trend('ma_fanout_efficiency');
const maFanoutWall = new Trend('ma_fanout_wall_ms', true);
const maFanoutSerial = new Trend('ma_fanout_serial_ms', true);

// ---- 阶段三观测（Workflow+RAG）----
const wfPathOk = new Rate('wf_path_ok');
const wfBranchOk = new Rate('wf_branch_ok');
const wfIntentOk = new Rate('wf_intent_ok');
const wfToolOk = new Rate('wf_tool_ok');
const wfNodeErrors = new Counter('wf_node_errors_total');
const wfConfidence = new Trend('wf_confidence');
// RAG 链路观测（不进门禁）：仅知识意图请求出样本（无检索跳过不计 0），
// 与服务端 workflow_rag_* Prometheus 指标互为客户端/服务端口径
const wfRagLatency = new Trend('wf_rag_latency_ms', true);
const wfRagHit = new Rate('wf_rag_hit_rate');
const wfRagCitations = new Trend('wf_rag_citations', true);

// 观测项注册表：adapter 返回的 obs 键 → k6 指标；null 表示“不适用”，跳过而不计 0
const OBS_REGISTRY = {
  toolCoverage: { kind: 'rate', metric: toolCoverageRate, countMetric: toolChecked },
  e2eMissing: { kind: 'counter', metric: e2eMissing },
  agentPathOk: { kind: 'rate', metric: maAgentPathOk },
  degraded: { kind: 'rate', metric: maDegraded },
  degradedAgents: { kind: 'counter', metric: maDegradedAgents },
  breakerSkipped: { kind: 'counter', metric: maBreakerSkipped },
  fanoutEfficiency: { kind: 'trend', metric: maFanoutEfficiency },
  fanoutWallMs: { kind: 'trend', metric: maFanoutWall },
  fanoutSerialMs: { kind: 'trend', metric: maFanoutSerial },
  pathOk: { kind: 'rate', metric: wfPathOk },
  branchOk: { kind: 'rate', metric: wfBranchOk },
  intentOk: { kind: 'rate', metric: wfIntentOk },
  toolOk: { kind: 'rate', metric: wfToolOk },
  nodeExecErrors: { kind: 'counter', metric: wfNodeErrors },
  confidence: { kind: 'trend', metric: wfConfidence },
  ragLatencyMs: { kind: 'trend', metric: wfRagLatency },
  ragHit: { kind: 'rate', metric: wfRagHit },
  ragCitations: { kind: 'trend', metric: wfRagCitations },
  firstAnswerMs: { kind: 'trend', metric: firstAnswerTrend },
  firstAnswerOk: { kind: 'rate', metric: firstAnswerOkRate },
};

// 创建绑定「阶段适配器 + 运行口径」的调用器
export function createRunner(stage, runtime) {
  function recordObs(obs, tags) {
    const keys = Object.keys(obs);
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i];
      const value = obs[key];
      if (value === null || value === undefined) continue;
      const entry = OBS_REGISTRY[key];
      if (!entry) continue;
      if (entry.kind === 'counter') {
        entry.metric.add(Number(value), tags);
      } else if (entry.kind === 'rate') {
        entry.metric.add(!!value, tags);
        if (entry.countMetric) entry.countMetric.add(1, tags);
      } else {
        entry.metric.add(Number(value), tags);
      }
    }
  }

  function runTicket(ticket, extraTags) {
    const tags = Object.assign({}, extraTags || {});
    // 性能集仅用低基数类别和预期路径分组；实际路径仍从逐笔执行轨迹复核。
    if (ticket.family) tags.case_family = ticket.family;
    if (ticket.perf_tags && ticket.perf_tags.path_class) {
      tags.expected_path_class = ticket.perf_tags.path_class;
    }
    if (TAG_TASK_ID) tags.task_id = ticket.ticket_id;
    let res = null;
    let body = null;
    const started = Date.now();
    // 链路口径：gateway.enabled 时业务请求经网关（真实用户链路：网关→前端→Agent），
    // 直连 baseUrl+endpoint 仅控制面使用。TLS 主机名跳过由启动器 --insecure-skip-tls-verify 全局生效。
    const viaGateway = runtime.gateway && runtime.gateway.enabled;
    const requestUrl = viaGateway ? runtime.gateway.businessUrl : runtime.baseUrl + runtime.endpoint;
    // 请求体只构造一次：网关重登重试复用同一笔业务身份（execution_id 等顶层字段
    // 供 judgements 逐笔回填；stage.buildBody 为确定性纯函数，行为与旧实现一致）
    let requestBody = null;
    const sendOnce = () => {
      const params = viaGateway ? gatewayAuthParams() : {};
      params.headers = Object.assign({ 'Content-Type': 'application/json' }, params.headers || {});
      params.timeout = '120s';
      params.tags = tags;
      if (requestBody === null) requestBody = stage.buildBody(RUN_ID, ticket);
      return http.post(requestUrl, JSON.stringify(requestBody), params);
    };
    try {
      res = sendOnce();
      // 会话失效兜底：网关 401（会话无效/过期）或 403（CSRF 校验失败）强制重登后重试一次；
      // 再失败按业务失败判定，不无限循环。网关 503（阶段暂停）不触发重登，按业务失败处理。
      if (viaGateway && (res.status === 401 || res.status === 403)) {
        forceGatewayRelogin();
        res = sendOnce();
      }
    } catch (e) {
      res = null; // 连接层异常按请求失败计入，不中断采样
    }
    const latencyMs = Date.now() - started;
    if (res && res.status === 200) {
      try { body = res.json(); } catch (e) { body = null; }
      if (body === null) payloadBadTotal.add(1, tags); // 协议层失败单列：200 但响应体不可解析
    }

    const view = stage.readView(body);
    view.wallMs = latencyMs; // 服务端 e2e_ms 缺失时退到客户端墙钟（比 Python 侧「缺失记 0」更严）
    const httpOk = !!res && res.status === 200;
    const out = stage.judgeTicket(ticket, view, {
      httpOk: httpOk,
      sloMs: runtime.sloMs,
      firstAnswerSloMs: runtime.firstAnswerSloMs,
      relaxResult: runtime.relaxResult === true,
    });

    businessTotal.add(1, tags);
    qualifiedRate.add(out.levels.qualified, tags);
    if (out.levels.qualified) qualifiedTotal.add(1, tags);
    if (tags.phase === 'measured') {
      measuredTotal.add(1, tags);
      measuredQualified.add(out.levels.qualified, tags);
    }
    wallTrend.add(latencyMs, tags); // 门禁来源：每笔必记（含服务端 e2e 缺失的笔）
    if (out.e2eServerMs !== null && out.e2eServerMs !== undefined) {
      serverE2eTrend.add(out.e2eServerMs, tags);
      const proxy = latencyMs - out.e2eServerMs;
      if (proxy >= 0) queueProxyTrend.add(proxy, tags);
    }
    if (out.tokens === null || out.tokens === undefined) usageMissing.add(1, tags);
    else tokensTotal.add(Number(out.tokens), tags);
    if (out.obs.toolCalls) {
      toolCallsTotal.add(out.obs.toolCalls, tags);
      toolAttemptsTotal.add(out.obs.toolAttempts, tags);
    }
    // 安全层：有护栏结果时计违规条数与违规业务数；缺失时单列缺失笔数（不冒充无违规）。
    // 三个计数逐笔都落样本（含 0），保证摘要页能区分「零违规」与「未采集」。
    if (out.obs.guardrailViolations === null || out.obs.guardrailViolations === undefined) {
      securityMissing.add(1, tags);
      securityViolations.add(0, tags);
      securityViolating.add(0, tags);
    } else {
      const violations = Number(out.obs.guardrailViolations);
      securityMissing.add(0, tags);
      securityViolations.add(violations, tags);
      securityViolating.add(violations > 0 ? 1 : 0, tags);
    }
    recordObs(out.obs, tags);

    // B15 逐笔判定明细：EMIT_JUDGEMENT=1 时按 JSON 行打印（run_id/ticket_id/四级判定/
    // 要素命中与缺失/分段耗时），编排器以 --console-output 落盘 raw/judgements.ndjson。
    // 解析端按 event=ticket_judged 过滤（console 流会混入 k6 自身输出）。
    // B8（2026-11 v13 真实模型轮）：逐笔唯一关联字段——server_run_id 取服务端 run_id
    // （高基数，不进 HTTP tags，走判定明细双通道对账）；execution_id 回填请求体顶层字段
    // （检查点续跑窗由 -e EXECUTION_ID 注入）；started_at/ended_at 由 ts 与 wall_ms 推；
    // path/branch/intent/tools 供阶段二/三逐笔轨迹复核，缺失给空值不报错。
    if (EMIT_JUDGEMENT) {
      const ts = new Date().toISOString();
      const startedAt = new Date(Date.parse(ts) - latencyMs).toISOString();
      const executionId = (requestBody && requestBody.execution_id !== undefined
        && requestBody.execution_id !== null) ? String(requestBody.execution_id) : '';
      console.log(JSON.stringify({
        event: 'ticket_judged',
        ts: ts,
        started_at: startedAt,
        ended_at: ts,
        run_id: RUN_ID,
        server_run_id: view.runId || '',
        execution_id: executionId,
        ticket_id: ticket.ticket_id,
        case_family: ticket.family || '',
        phase: tags.phase || '',
        level: tags.level || '',
        levels: out.levels,
        quality: out.quality || null,
        result_caliber: runtime.relaxResult ? 'business-fallback' : 'keyword-literal',
        wall_ms: latencyMs,
        e2e_server_ms: (out.e2eServerMs === undefined ? null : out.e2eServerMs),
        first_answer_ms: (out.obs.firstAnswerMs === undefined ? null : out.obs.firstAnswerMs),
        tokens: (out.tokens === undefined ? null : out.tokens),
        tool_calls: out.obs.toolCalls || 0,
        guardrail_violations: (out.obs.guardrailViolations === undefined || out.obs.guardrailViolations === null ? null : Number(out.obs.guardrailViolations)),
        path: Array.isArray(view.path) ? view.path : [],
        branch: typeof view.branch === 'string' ? view.branch : '',
        intent: typeof view.intent === 'string' ? view.intent : '',
        tools: actualTools(view.trace),
      }));
    }

    out.wall_ms = latencyMs;
    out.run_id = view.runId;
    return out;
  }

  return { runTicket: runTicket, runtime: runtime, stage: stage };
}
