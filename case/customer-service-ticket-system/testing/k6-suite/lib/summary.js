// 运行摘要输出（k6 依赖）：handleSummary 薄适配层——计算看板并落两类产物
// - out/<前缀>-summary.html：人读判定表 + 业务看板 + 阶段观测 + 环境指纹
// - out/<前缀>-summary.json：机器可读（聚合看板 + 阈值判定 + 原始 k6 指标）
// 前缀默认取场景名，由 k6-suite/run-scenario.sh 注入带时间戳的 OUT_PREFIX，避免覆盖历史产物。
// 注意：k6 的 handleSummary 不会自建目录，运行前请确保 out/ 存在（封装器已 mkdir）。
import {
  CALIBER_SOURCE, RUN_ID, OUT_PREFIX, OUT_DIR,
  TICKETS_PATH, GIT_HEAD, K6_VERSION, ticketCount,
} from './config.js';
import { computeBoard, computeLevelSummaries, renderHtml, renderJson, windowsFromMetrics } from './board.js';
import { CONTRACT_VERSION } from './contract.js';

// 运行窗口由场景侧边界 Gauge 写入聚合摘要（k6 的 handleSummary 在独立 JS 上下文执行，
// VU 侧模块状态不可见），此处统一解析为 ISO 窗口并供看板与逐档摘要使用。
export function summaryFor(scenario, stage, runtime) {
  return function handleSummary(data) {
    const prefix = OUT_PREFIX || (stage.id + '-' + scenario);
    const htmlPath = OUT_DIR + '/' + prefix + '-summary.html';
    const jsonPath = OUT_DIR + '/' + prefix + '-summary.json';
    try {
      const fp = data.setup_data || null;
      const wins = windowsFromMetrics(data.metrics, {
        levels: (scenario === 'c1')
          ? ((__ENV.LEVELS || '1,5,10,20,30,50').split(',').map(Number)) : [],
        stabilizationSeconds: Number(__ENV.STAB_SEC || 60),
      });
      const windows = { warmup: wins.warmup, measured: wins.measured };
      const ctx = {
        scenario: scenario,
        stageId: stage.id,
        stageName: stage.name,
        contractVersion: CONTRACT_VERSION,
        runId: RUN_ID,
        k6Version: K6_VERSION,
        gitHead: GIT_HEAD,
        ticketsPath: TICKETS_PATH,
        ticketCount: ticketCount(),
        sloMs: runtime.sloMs,
        firstAnswerSloMs: runtime.firstAnswerSloMs,
        costPer1k: runtime.costPer1k,
        caliberSource: CALIBER_SOURCE,
        caliberFromEnv: runtime.fromEnv,
        windows: windows,
        probeError: (fp && fp.error) ? fp.error : '',
      };
      const board = computeBoard(data, {
        scenario: scenario,
        stageId: stage.id,
        sloMs: runtime.sloMs,
        firstAnswerSloMs: runtime.firstAnswerSloMs,
        costPer1k: runtime.costPer1k,
        caliberSource: CALIBER_SOURCE,
        degradationLimitPct: 20, // 方案 §5：长稳末段劣化 ≤20%
        windows: windows,
        // 隔离编排器（run-isolated.sh）注入 MANIFEST=1：运行清单已由启动器落盘，
        // k6 侧门禁据此进入阈值判定分支；权威判定仍由 verify-run.py 复核证据链
        manifest: (typeof __ENV !== 'undefined' && __ENV.MANIFEST === '1') ? { run_id: RUN_ID } : null,
      });
      const out = {};
      out[htmlPath] = renderHtml(board, ctx);
      out[jsonPath] = renderJson(board, ctx, data.metrics || {});
      if (scenario === 'c1') {
        // 逐档容量摘要（契约 §4）：每档独立产物，跨档汇总不得回答容量问题
        const levels = computeLevelSummaries(data, {
          contractVersion: CONTRACT_VERSION,
          runId: RUN_ID,
          sloMs: runtime.sloMs,
          levelWindows: wins.levels,
          samplesRef: 'raw samples: ' + OUT_DIR + '/' + prefix + '.ndjson',
        });
        out[OUT_DIR + '/' + prefix + '-levels.json'] = JSON.stringify({
          contract_version: CONTRACT_VERSION,
          run_id: RUN_ID,
          scenario: scenario,
          stage: stage.id,
          levels: levels,
        }, null, 2);
      }
      return out;
    } catch (e) {
      // 摘要生成自身异常也必须落产物：带错误栈的最小 HTML，现场可排障
      const out = {};
      const stack = String((e && e.stack) || e);
      out[htmlPath] = '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>'
        + scenario + ' 摘要生成失败</title></head><body><h1>' + scenario + ' 摘要生成失败</h1><pre>'
        + stack + '</pre><p>原始数据仍可用 --out json 的 ndjson 追溯</p></body></html>';
      out[jsonPath] = JSON.stringify({
        contract_version: CONTRACT_VERSION,
        scenario: scenario, error: stack, metrics: data.metrics || {},
      }, null, 2);
      return out;
    }
  };
}
