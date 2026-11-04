// CALIB 校准场景（1 VU × COUNT 笔低并发，输出 P50/P90/P95/P99 与 token 摘要，不参与门禁）（阶段2 Multi-Agent）——薄入口：场景实现与判定口径在案例共享套件 k6-suite/ 内
// 用法（阶段目录下）：tests/performance/k6/run-scenario.sh calib
//   或：k6 run tests/performance/k6/calib.js -e COUNT=20（需先 mkdir -p out）
//   云端编排：lab.sh run --stage 2 --scenario calib --count 20 → run-isolated.sh → 本入口
import { defineCalib } from '../../../../../testing/k6-suite/scenarios/calib.js';
import { stage2 } from '../../../../../testing/k6-suite/stages/stage2.js';

const scenario = defineCalib(stage2);

export const options = scenario.options;
export const handleSummary = scenario.handleSummary;
export function setup() { return scenario.setup(); }
export default scenario.run;
