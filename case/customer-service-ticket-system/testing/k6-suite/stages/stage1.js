// 阶段一 Single Agent 适配器（纯 ESM）：非流式 /api/v1/chat/complete，
// 请求契约 {user_query, task_id, batch_id, stream}，判定与 tests/performance/common.py 一致（无附加维度）。
import { readView, composeJudgement } from './base.js';

export const stage1 = {
  id: '1',
  name: 'Single Agent',
  defaultBaseUrl: 'http://127.0.0.1:8000',
  defaultSloMs: 10000,          // 方案 §5 实验室档（生产档 15000，可 -e SLO_MS= 覆盖）
  defaultFirstAnswerSloMs: null, // 阶段一不判定首字（仅观测）
  defaultCostPer1k: 2.0,         // mock 假设单价，与阶段一 Python 脚本一致
  endpoint: '/api/v1/chat/complete',

  buildBody: function (runId, ticket) {
    return {
      user_query: ticket.user_query,
      task_id: ticket.ticket_id,
      batch_id: runId,
      stream: false,
    };
  },

  readView: function (body) {
    return readView(body);
  },

  // 阶段一无附加判定维度；附加观测为空
  extraDims: function () { return {}; },
  extraObs: function () { return {}; },
  boardSections: function () { return []; },

  // 判定入口（api.js 与 selfcheck.mjs 共用）：阶段差异只体现在 extraDims / extraObs
  judgeTicket: function (ticket, view, opts) {
    return composeJudgement(ticket, view, {
      httpOk: opts.httpOk,
      sloMs: opts.sloMs,
      firstAnswerSloMs: opts.firstAnswerSloMs,
      extraDims: stage1.extraDims(ticket, view),
      extraObs: stage1.extraObs(ticket, view),
      relaxResult: opts.relaxResult === true,
    });
  },
};
