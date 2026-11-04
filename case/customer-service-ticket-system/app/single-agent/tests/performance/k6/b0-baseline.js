// B0 基准场景（工单池全量轮换 × 1VU，含预热与冷启动标注）（阶段1 Single Agent）——薄入口：场景实现与判定口径在案例共享套件 k6-suite/ 内
// 用法（阶段目录下）：tests/performance/k6/run-scenario.sh b0
//   或：k6 run tests/performance/k6/b0-baseline.js（需先 mkdir -p out）
import { defineB0 } from '../../../../../testing/k6-suite/scenarios/b0.js';
import { stage1 } from '../../../../../testing/k6-suite/stages/stage1.js';

const scenario = defineB0(stage1);

export const options = scenario.options;
export const handleSummary = scenario.handleSummary;
export function setup() { return scenario.setup(); }
export default scenario.run;
