// 阶段二 Multi-Agent 适配器（纯 ESM）：请求契约与阶段一相同，
// 判定主链与阶段一一致（四级判定口径用于跨架构对比），另加 Multi-Agent 专项**观测项**（不进 qualified）：
// - agent_path_ok：实际编排 Agent 覆盖工单必需执行者（对齐 common.py agent_path_ok）
// - 降级 / 熔断：degraded、degraded_agents、子 Agent status=skipped_breaker 计数
// - 扇出并行效率：fanout.efficiency = 串行和 ÷ 并行墙钟，以及 round_wall_ms / serial_sum_ms
import { readView, composeJudgement } from './base.js';
import { agentPathOk } from '../lib/judge.js';

export const stage2 = {
  id: '2',
  name: 'Multi-Agent',
  defaultBaseUrl: 'http://127.0.0.1:8001',
  defaultSloMs: 10000,          // 与阶段一同档（方案 §5 实验室档），保证跨架构可比
  defaultFirstAnswerSloMs: null,
  defaultCostPer1k: 2.0,         // 与阶段二 Python 脚本 DEFAULT_COST_PER_1K_TOKENS 一致
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

  extraDims: function () { return {}; }, // 阶段二路径校验为观测项，不进合格判定（与 common.py 一致）

  extraObs: function (ticket, view) {
    const t = view.telemetry || {};
    const agents = t.agents || [];
    let breakerSkipped = 0;
    for (let i = 0; i < agents.length; i++) {
      const a = agents[i] || {};
      if (a.status === 'skipped_breaker') breakerSkipped += 1;
    }
    const degradedAgents = t.degraded_agents || [];
    const fanout = t.fanout || {};
    const obs = {
      agentPathOk: agentPathOk(ticket, t),
      degraded: !!t.degraded,
      degradedAgents: degradedAgents.length,
      breakerSkipped: breakerSkipped,
    };
    if (fanout.efficiency !== undefined && fanout.efficiency !== null) obs.fanoutEfficiency = Number(fanout.efficiency);
    if (fanout.round_wall_ms !== undefined && fanout.round_wall_ms !== null) obs.fanoutWallMs = Number(fanout.round_wall_ms);
    if (fanout.serial_sum_ms !== undefined && fanout.serial_sum_ms !== null) obs.fanoutSerialMs = Number(fanout.serial_sum_ms);
    return obs;
  },

  boardSections: function () { return ['stage2']; },

  // 判定入口：路径/降级/熔断/扇出均为观测项，不改变 qualified（与阶段二 common.py 一致）
  judgeTicket: function (ticket, view, opts) {
    return composeJudgement(ticket, view, {
      httpOk: opts.httpOk,
      sloMs: opts.sloMs,
      firstAnswerSloMs: opts.firstAnswerSloMs,
      extraDims: stage2.extraDims(ticket, view),
      extraObs: stage2.extraObs(ticket, view),
      relaxResult: opts.relaxResult === true,
    });
  },
};
