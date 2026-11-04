// 阶段三 Workflow/Flow + RAG 适配器（纯 ESM）：
// - 请求契约 {query, execution_id, task_id, batch_id}（无 user_query/stream，避免契约外字段）
// - 判定在四级判定之上 AND 四项维度（对齐 common.py classify）：
//     path_ok   = 实际节点路径 == expected_path（expected_path 为空则不适用）
//     branch_ok = 最终分支 == expected_branch（为空则不适用）
//     tool_ok   = expected_tools 覆盖；search_knowledge 以路径含 knowledge_retrieval 等效
//     intent_ok = 最终意图 == expected_intent（为空则不适用）
// - 安全层：护栏结果位于 metrics.trace.guardrail（阶段三不经共享 runtime 的 metrics.guardrail）
// - 首字门槛：metrics.first_answer_ms ≤ first_answer_slo_ms（来自服务端 slo 配置，缺失则该层不适用）
// - 计价：token_price_per_1k（服务端指纹，默认 0.15）
import { readView, composeJudgement } from './base.js';
import { arraysEqual, toolsMatched, actualTools } from '../lib/judge.js';

// 检查点续跑注入（B10）：-e EXECUTION_ID 固定恢复标识后随请求体顶层字段下发；
// 默认空串保持常规压测契约（与改造前逐字段等价）。本适配器同时被 node selfcheck
// 加载，__ENV 只在 k6 运行时存在，故用 typeof 守卫按需读取。
const EXECUTION_ID = (typeof __ENV !== 'undefined' && __ENV.EXECUTION_ID)
  ? String(__ENV.EXECUTION_ID) : '';

export const stage3 = {
  id: '3',
  name: 'Workflow+RAG',
  defaultBaseUrl: 'http://127.0.0.1:8002',
  defaultSloMs: 8000,               // workflow_config.yaml slo.e2e_ms（由封装器按 /version 指纹覆盖）
  defaultFirstAnswerSloMs: 2000,    // workflow_config.yaml slo.first_answer_ms
  defaultCostPer1k: 0.15,           // token_price_per_1k（服务端指纹可取）
  endpoint: '/api/v1/chat/complete',

  buildBody: function (runId, ticket) {
    return {
      query: ticket.user_query,
      execution_id: EXECUTION_ID,  // 常规压测为空；检查点续跑由 -e EXECUTION_ID 注入
      task_id: ticket.ticket_id,
      batch_id: runId,
    };
  },

  readView: function (body) {
    const view = readView(body);
    const b = body || {};
    // 阶段三 top-level 优先（RunResult 无 e2e_ms，但保留 v1 /chat 形态兼容）
    if (b.e2e_ms !== undefined && b.e2e_ms !== null) view.e2eMs = b.e2e_ms;
    if (b.total_tokens !== undefined && b.total_tokens !== null) view.totalTokens = b.total_tokens;
    const t = view.telemetry || {};
    view.path = Array.isArray(t.path) ? t.path : [];
    view.branch = typeof t.branch === 'string' ? t.branch : '';
    view.intent = (b.intent === undefined || b.intent === null || b.intent === '') ? (t.intent || '') : b.intent;
    if (t.guardrail !== undefined) view.guardrail = t.guardrail; // 阶段三护栏在 trace 内
    return view;
  },

  extraDims: function (ticket, view) {
    const dims = {};
    if (ticket.expected_path && ticket.expected_path.length) {
      dims.pathOk = arraysEqual(view.path, ticket.expected_path);
    }
    if (ticket.expected_branch) dims.branchOk = view.branch === ticket.expected_branch;
    dims.toolOk = toolsMatched(ticket, actualTools(view.trace), view.path);
    if (ticket.expected_intent) dims.intentOk = view.intent === ticket.expected_intent;
    return dims;
  },

  extraObs: function (ticket, view) {
    const t = view.telemetry || {};
    const nodeErrors = t.node_exec_errors || [];
    // RAG 观测：仅知识意图请求有 retrieval/citations；数据类请求返回 null 跳过（不计 0）。
    // retrieval 需带 latency_ms 或 hits 才视为有效检索观测（degraded/异常路径可能给空对象）
    const retrieval = t.retrieval || null;
    const hasRetrieval = !!(retrieval && (retrieval.latency_ms !== undefined || Array.isArray(retrieval.hits)));
    const citations = Array.isArray(t.citations) ? t.citations : null;
    return {
      pathOk: (ticket.expected_path && ticket.expected_path.length) ? arraysEqual(view.path, ticket.expected_path) : null,
      branchOk: ticket.expected_branch ? (view.branch === ticket.expected_branch) : null,
      intentOk: ticket.expected_intent ? (view.intent === ticket.expected_intent) : null,
      toolOk: toolsMatched(ticket, actualTools(view.trace), view.path),
      nodeExecErrors: nodeErrors.length,
      confidence: (t.confidence === undefined ? null : Number(t.confidence)),
      ragLatencyMs: hasRetrieval && retrieval.latency_ms !== undefined ? Number(retrieval.latency_ms) : null,
      ragHit: hasRetrieval ? ((retrieval.hits || []).length > 0) : null,
      ragCitations: citations && citations.length > 0 ? citations.length : null,
    };
  },

  boardSections: function () { return ['stage3']; },

  // 判定入口：path/branch/tool/intent 为硬判定维度（expected_* 为空则该项不适用）
  judgeTicket: function (ticket, view, opts) {
    return composeJudgement(ticket, view, {
      httpOk: opts.httpOk,
      sloMs: opts.sloMs,
      firstAnswerSloMs: opts.firstAnswerSloMs,
      extraDims: stage3.extraDims(ticket, view),
      extraObs: stage3.extraObs(ticket, view),
      relaxResult: opts.relaxResult === true,
    });
  },
};
