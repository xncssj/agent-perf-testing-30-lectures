// 阶段无关配置（k6 依赖）：全部经 __ENV 注入，脚本零密钥。
//
// 路径解析约定（k6 v1.6.1 实测）：k6 的 open() 以**调用它的脚本文件所在目录**为基准，与 cwd 无关。
// 本文件位于 k6-suite/lib/，按该目录上溯 2 级即案例根目录：
//   lib → k6-suite → customer-service-ticket-system/，因此共享数据集为 ../../../testing/test-data/tickets.json。
// -e TICKETS= 覆盖遵循同一规则；按 cwd 理解路径时请传绝对路径。
import { gatewayBusinessUrl } from './gateway-headers.js';

export const STAGE = __ENV.STAGE || '1';
export const RUN_ID = __ENV.RUN_ID || ('k6-' + Date.now());
export const TICKETS_PATH = __ENV.TICKETS || '../../../testing/test-data/tickets.json';
// 产物前缀与目录（k6 的 handleSummary 不会自建目录，运行前需存在 out/）
export const OUT_PREFIX = __ENV.OUT_PREFIX || '';
export const OUT_DIR = __ENV.OUT_DIR || 'out';
// 高基数标签开关：默认关闭；置 1 时把工单号作为 task_id 标签，仅用于小样本归因
export const TAG_TASK_ID = String(__ENV.TAG_TASK_ID || '0') === '1';
// 逐笔判定输出开关（B15）：置 1 时 runTicket 尾部以 JSON 行打印逐笔四级判定与要素明细，
// 配合 k6 --console-output 落盘 raw/judgements.ndjson，供采集包复核逐笔 Eval（仅编排链路开启）
export const EMIT_JUDGEMENT = String(__ENV.EMIT_JUDGEMENT || '0') === '1';
// 结果口径回退开关（2026-10-14 v12real1 战役拍板 B 登记）：真实模型自由措辞下，
// 数据集逐字关键词/事实判定不适用于门禁时置 1——result_ok 回退为业务完成级，
// 关键词命中率降为单列观测项（quality 命中/缺失明细照常产出）。默认 0 保持 formal 系列口径
export const RELAX_RESULT_TO_BUSINESS = String(__ENV.RELAX_RESULT_TO_BUSINESS || '0') === '1';
// 环境指纹（由 k6-suite/run-scenario.sh 注入，缺失时摘要页标注 unknown）
export const GIT_HEAD = __ENV.GIT_HEAD || '';
export const K6_VERSION = __ENV.K6_VERSION || '';
// 口径来源标注：fingerprint（run-scenario.sh 按 /version 注入）/ env（人工覆盖）/ stage-default（阶段默认）
export const CALIBER_SOURCE = __ENV.CALIBER_SOURCE || 'stage-default';

// 网关链路口径（可选）：GATEWAY_URL 非空时业务流量经公网网关内网回环（真实用户链路），
// 直连 BASE_URL 仅保留给控制面（探活/指纹/指标采样）。认证流见 lib/gateway-auth.js。
export const GATEWAY_URL = __ENV.GATEWAY_URL || '';
export const GATEWAY_ORIGIN = __ENV.GATEWAY_ORIGIN || '';
export const GATEWAY_USERNAME = __ENV.GATEWAY_USERNAME || '';
// 口令只被 lib/gateway-auth.js 读取，不进 runtime 对象、manifest、摘要与日志（README 安全注记）
export const GATEWAY_PASSWORD = __ENV.GATEWAY_PASSWORD || '';

function envNumber(name) {
  const raw = __ENV[name];
  if (raw === undefined || raw === null || String(raw) === '') return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

// 运行口径：环境显式值 > 阶段默认（阶段三由 run-scenario.sh 从 /version 指纹注入服务端配置值）
export function resolveRuntime(stage) {
  const sloMs = envNumber('SLO_MS');
  const firstAnswerSloMs = envNumber('FIRST_ANSWER_SLO_MS');
  const costPer1k = envNumber('COST_PER_1K');
  const baseUrl = __ENV.BASE_URL || stage.defaultBaseUrl;
  // 网关链路开启时预拼业务 URL（非法组合在此 fail fast：前缀缺失/endpoint 非 / 开头）
  const gatewayEnabled = GATEWAY_URL !== '';
  if (gatewayEnabled && (!GATEWAY_ORIGIN || !GATEWAY_USERNAME || !GATEWAY_PASSWORD)) {
    throw new Error('GATEWAY_URL 已设置，须同时提供 GATEWAY_ORIGIN/GATEWAY_USERNAME/GATEWAY_PASSWORD');
  }
  return {
    stageId: stage.id,
    stageName: stage.name,
    baseUrl: baseUrl,
    endpoint: stage.endpoint,
    sloMs: (sloMs === null) ? stage.defaultSloMs : sloMs,
    firstAnswerSloMs: (firstAnswerSloMs === null) ? stage.defaultFirstAnswerSloMs : firstAnswerSloMs,
    costPer1k: (costPer1k === null) ? stage.defaultCostPer1k : costPer1k,
    caliberSource: CALIBER_SOURCE,
    relaxResult: RELAX_RESULT_TO_BUSINESS,
    // 网关块：enabled/url/origin/username/businessUrl 可随摘要落盘；口令不在其中
    gateway: {
      enabled: gatewayEnabled,
      url: GATEWAY_URL,
      origin: GATEWAY_ORIGIN,
      username: GATEWAY_USERNAME,
      businessUrl: gatewayEnabled ? gatewayBusinessUrl(GATEWAY_URL, stage.id, stage.endpoint) : '',
    },
    fromEnv: {
      sloMs: sloMs !== null,
      firstAnswerSloMs: firstAnswerSloMs !== null,
      costPer1k: costPer1k !== null,
      relaxResult: RELAX_RESULT_TO_BUSINESS,
    },
  };
}

// 工单池加载：失败时给出可操作的修复提示，避免只看到 GoError 栈
function loadTickets(path) {
  let data;
  try {
    data = JSON.parse(open(path));
  } catch (e) {
    throw new Error(
      '工单集加载失败：' + path
        + '（k6 的 open() 以调用文件所在目录为基准，本文件在 k6-suite/lib/）。'
        + '请用 -e TICKETS=<绝对路径> 指定，或确认共享数据集 test-data/tickets.json 存在。',
    );
  }
  return data;
}

export const TICKETS = loadTickets(TICKETS_PATH); // 默认共享 v2.1.0；TICKETS 可选独立性能档

export function requireTickets() {
  if (!Array.isArray(TICKETS) || !TICKETS.length) {
    throw new Error('工单集为空：' + TICKETS_PATH + '（应为 tickets.json 数组，含 ticket_id/user_query/期望字段）');
  }
  const first = TICKETS[0] || {};
  if (!first.ticket_id || !first.user_query) {
    throw new Error('工单集结构异常：首条缺少 ticket_id/user_query 字段，请核对所选数据档清单');
  }
}

export function ticketCount() {
  return Array.isArray(TICKETS) ? TICKETS.length : 0;
}

// 工单轮换器：__VU 错相位 + 调用自增，实现全局错开轮换
// （Mock 为确定性模型：同一工单重复读数相同，输入必须轮换覆盖分布——方案 §8.4）
let pickIter = 0;
export function pickTicket() {
  const t = TICKETS[(__VU + pickIter) % TICKETS.length];
  pickIter += 1;
  return t;
}
