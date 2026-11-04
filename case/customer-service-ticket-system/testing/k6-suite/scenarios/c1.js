// C1 容量阶梯场景工厂（k6 依赖）：每档一个场景，startTime 顺序累加，level tag 分档判定。
// 负载模型二选一（-e LOAD_MODEL=）：vu（默认，封闭模型 constant-vus，level=并发数）|
// arrival（开放模型 constant-arrival-rate，level=每秒到达笔数 TPS，-e ARRIVAL_VUS= 预分配 VU 上限）
// - 档位时间结构（契约 §2/§4）：每档默认 3 分钟 = 稳定 60s（phase=stabilization，不入容量分母）
//   + 计量 120s（phase=measured）；档边界经 Gauge 传给 handleSummary（k6 的 handleSummary 在
//   独立 JS 上下文执行，VU 侧模块状态不可见；Gauge 摘要保留末值）
// - 阈值与分档统计只挂 {level:X,phase:measured} 子指标；等值边界统一继承 lib/contract.js
// - 每档独立生成 LevelSummary（handleSummary 落 *-levels.json）；跨档汇总不得回答容量问题
// 用法：-e LEVELS=1,5,10 -e STAB_SEC=60 -e MEASURED_SEC=120
import exec from 'k6/execution';
import { Gauge } from 'k6/metrics';
import { requireTickets, pickTicket, resolveRuntime } from '../lib/config.js';
import { probeFingerprint } from '../lib/fingerprint.js';
import { createRunner } from '../lib/api.js';
import { gatewayPrecheck } from '../lib/gateway-auth.js';
import { summaryFor } from '../lib/summary.js';
import {
  k6ThresholdExprs, E2E_CLIENT_WALL_METRIC, E2E_SERVER_METRIC,
  C1_STABILIZATION_SECONDS, C1_MEASURED_SECONDS,
} from '../lib/contract.js';

export function defineC1(stage) {
  const runtime = resolveRuntime(stage);
  const runner = createRunner(stage, runtime);
  const LEVELS = (__ENV.LEVELS || '1,5,10,20,30,50').split(',').map(Number);
  const STAB_SEC = Number(__ENV.STAB_SEC || C1_STABILIZATION_SECONDS);
  const MEASURED_SEC = Number(__ENV.MEASURED_SEC || C1_MEASURED_SECONDS);
  const exprs = k6ThresholdExprs(runtime.sloMs);

  requireTickets();

  // 逐档边界 Gauge（init 期按档注册）：start = 各档首个请求实际时刻；end = 逐请求刷新（末值生效）。
  // 档起点同时在 VU 侧记录（levelStartMs）用于请求相位判定；稳定/计量窗口由 start + STAB_SEC
  // 推算（windowsFromMetrics），与相位判定保持同一口径。
  const levelGauges = {};
  const levelStartMs = {};
  for (let i = 0; i < LEVELS.length; i++) {
    const level = LEVELS[i];
    levelGauges[level] = {
      start: new Gauge('c1_l' + level + '_start_unix'),
      end: new Gauge('c1_l' + level + '_end_unix'),
    };
  }

  const scenarios = {};
  const thresholds = { 'http_req_failed': [exprs.errorRate] };
  const LEVEL_SEC = STAB_SEC + MEASURED_SEC;
  let offset = 0;
  // 负载模型：vu（默认，封闭模型）| arrival（开放模型，LEVELS 重读为每秒到达笔数 TPS）。
  // arrival 模式在途笔数 ≈ 到达率 × E2E 时延，preAllocatedVUs 须覆盖峰值在途；
  // 不足时 k6 丢弃到达并计 dropped_iterations，摘要的 business_total 会低于 arrival×时长，可据此发现容量不足。
  const LOAD_MODEL = __ENV.LOAD_MODEL || 'vu';
  const ARRIVAL_VUS = Number(__ENV.ARRIVAL_VUS || 200);
  for (let i = 0; i < LEVELS.length; i++) {
    const level = LEVELS[i];
    scenarios['level_' + level] = LOAD_MODEL === 'arrival'
      ? {
          executor: 'constant-arrival-rate',
          rate: level, // arrival 模式下 level = 每秒到达笔数（TPS），非并发数
          timeUnit: '1s',
          duration: LEVEL_SEC + 's',
          startTime: offset + 's',
          preAllocatedVUs: ARRIVAL_VUS,
          maxVUs: ARRIVAL_VUS,
          // 不指定 exec：多档场景统一使用入口脚本的 default 导出（薄入口只导出 default）
        }
      : {
          executor: 'constant-vus',
          vus: level,
          duration: LEVEL_SEC + 's',
          startTime: offset + 's',
          // 不指定 exec：多档场景统一使用入口脚本的 default 导出（薄入口只导出 default）
        };
    const tag = 'level:' + level + ',phase:measured';
    thresholds['http_req_failed{' + tag + '}'] = [exprs.errorRate];
    thresholds['business_qualified_rate{' + tag + '}'] = [exprs.qualifiedRate];
    thresholds[E2E_CLIENT_WALL_METRIC + '{' + tag + '}'] = [exprs.e2eP90];
    // 空阈值物化逐档服务端子指标（k6 只为 thresholds 声明的子指标产出 summary 值）：
    // 不加退出码约束，levels.json 的 e2e_server_p90_ms 不再恒 null（B14 同源缺口）
    thresholds[E2E_SERVER_METRIC + '{' + tag + '}'] = [];
    offset += LEVEL_SEC;
  }

  function setup() {
    const fingerprint = probeFingerprint(runtime.baseUrl); // 控制面直连不变
    const gateway = gatewayPrecheck(); // 网关模式：开跑前真实登录，失败即中止
    return Object.assign(fingerprint, { gateway: gateway });
  }

  function hit() {
    const level = Number(exec.scenario.name.replace(/^level_/, ''));
    const now = Date.now();
    if (levelStartMs[level] === undefined) {
      levelStartMs[level] = now; // 档起点（实际时刻）；稳定窗口 [start, start+STAB)，计量 [start+STAB, end]
      levelGauges[level].start.add(now / 1000);
    }
    const elapsedSec = (now - levelStartMs[level]) / 1000;
    const phase = elapsedSec < STAB_SEC ? 'stabilization' : 'measured';
    runner.runTicket(pickTicket(), { level: String(level), phase: phase });
    levelGauges[level].end.add(Date.now() / 1000);
  }

  return {
    options: { scenarios: scenarios, thresholds: thresholds },
    setup: setup,
    run: hit,
    handleSummary: summaryFor('c1', stage, runtime),
    runtime: runtime,
  };
}
