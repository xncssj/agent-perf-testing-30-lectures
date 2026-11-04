// 阶段适配层公共部分（纯 ESM，无 k6 依赖）：响应归一化 + 判定组合。
// 各阶段适配器只声明“请求体构造 / 响应视图 / 附加判定维度 / 附加观测项”，判定主链共用本文件，
// 保证三阶段口径一致（与各阶段 tests/performance/common.py classify 对齐）。
import { APOLOGY_PREFIX, businessLevels, judgeResult, retrySignal, toolCoverage, violationCount } from '../lib/judge.js';

// 响应 → 统一视图（各阶段可覆写字段来源）
export function readView(body) {
  const b = body || {};
  const metrics = b.metrics || {};
  const trace = b.trace || {};
  // 阶段二/三按 v1 兼容方式把遥测写入 metrics.trace（与顶层 trace 同结构）；阶段一无 metrics.trace
  const telemetry = metrics.trace || trace;
  return {
    response: typeof b.response === 'string' ? b.response : '',
    status: typeof b.status === 'string' ? b.status : '',
    stopReason: b.stop_reason || '',
    runId: b.run_id || '',
    intent: typeof b.intent === 'string' ? b.intent : '',
    metrics: metrics,
    trace: trace,
    telemetry: telemetry,
    e2eMs: (metrics.e2e_ms === undefined) ? null : metrics.e2e_ms,
    firstAnswerMs: (metrics.first_answer_ms === undefined) ? null : metrics.first_answer_ms,
    totalTokens: (metrics.total_tokens === undefined) ? null : metrics.total_tokens,
    guardrail: (metrics.guardrail === undefined) ? null : metrics.guardrail,
    evidenceKind: metrics.evidence_kind || '',
    usageSource: metrics.usage_source || '',
  };
}

// 判定组合：四级 +（阶段附加维度）+ 双指标时延 + 安全层，并汇总通用观测项
export function composeJudgement(ticket, view, opts) {
  const requestOk = !!opts.httpOk;
  const flowDone = view.status === 'completed';
  const businessDone = flowDone && view.response.indexOf(APOLOGY_PREFIX) !== 0;
  const core = judgeResult(ticket, view.response, businessDone);

  // 时延口径（契约 §3.1）：业务门禁（slo_ok）只认客户端墙钟 e2e_client_wall_ms；
  // 服务端 e2e_server_ms 只作诊断，缺失时不再回退混入判定分布（缺失笔数单列 e2e_missing_total）。
  // 墙钟不可测（NaN/缺失）时 Number() 产生 NaN，NaN <= slo 恒为 false → slo_ok 判否，不冒充达标。
  const e2eMissing = (view.e2eMs === null || view.e2eMs === undefined);

  const dims = opts.extraDims || {};            // 阶段特有维度（阶段三：path/branch/tool/intent）
  // 结果口径回退（2026-10-14 v12real1 拍板 B 登记）：relaxResult 置位且业务完成时，
  // 逐字关键词/事实的 result_ok 判定回退为业务完成级——真实模型自由措辞与数据集
  // Mock 模板词不逐字匹配是措辞差异而非业务错误；quality 命中/缺失明细照常产出（单列观测）。
  // 结构维度（pathOk/branchOk）仍为硬判定，不随回退放松。
  // 意图/工具维度摘出合格判定（2026-11-03 老板拍板第二层降断言，登记授权记录 §5）：
  // 阶段三真实模型对「物流查询」族工单的意图分类与数据档期望标注系统性不符
  // （c1low3 轮 LD3000003/07/12/14 四张固定错档，压力无关），意图错档连带工具链
  // 少调属同一归因；两维保留逐笔产出单列观测，path3/rag3 专项照常覆盖，不并入 qualified。
  let resultOk = core.resultOk;
  if (!resultOk && opts.relaxResult === true && businessDone) resultOk = true;
  const dimKeys = Object.keys(dims);
  for (let i = 0; i < dimKeys.length; i++) {
    if (dimKeys[i] === 'intentOk' || dimKeys[i] === 'toolOk') continue; // 降断言：单列观测
    if (dims[dimKeys[i]] === false) resultOk = false;
  }

  const levels = businessLevels({
    requestOk: requestOk,
    flowDone: flowDone,
    businessDone: businessDone,
    resultOk: resultOk,
    latencyMs: view.wallMs,
    sloMs: opts.sloMs,
    firstAnswerMs: view.firstAnswerMs,
    firstAnswerSloMs: opts.firstAnswerSloMs,
    guardrail: view.guardrail,
  });

  const retry = retrySignal(view.trace);
  const obs = {
    toolCoverage: toolCoverage(ticket, view.trace),
    toolCalls: retry.calls,
    toolAttempts: retry.attempts,
    e2eMissing: e2eMissing,
    guardrailViolations: view.guardrail === null || view.guardrail === undefined ? null : violationCount(view.guardrail),
    firstAnswerMs: view.firstAnswerMs,
    firstAnswerOk: levels.first_answer_ok,
  };
  if (opts.extraObs) {
    const extra = opts.extraObs;
    const keys = Object.keys(extra);
    for (let i = 0; i < keys.length; i++) obs[keys[i]] = extra[keys[i]];
  }

  return {
    levels: levels,
    dims: dims,
    quality: core.quality,
    obs: obs,
    wallMs: view.wallMs,
    e2eServerMs: e2eMissing ? null : view.e2eMs,
    firstAnswerMs: view.firstAnswerMs,
    status: view.status,
    stopReason: view.stopReason,
    runId: view.runId,
    tokens: view.totalTokens,
    metrics: view.metrics,
    trace: view.trace,
  };
}
