// C1 容量阶梯（每档 constant-vus，level 分档判定）（阶段1 Single Agent）——薄入口：场景实现与判定口径在案例共享套件 k6-suite/ 内
// 用法（阶段目录下）：tests/performance/k6/run-scenario.sh c1
//   或：k6 run tests/performance/k6/c1-capacity.js（需先 mkdir -p out）
import { defineC1 } from '../../../../../testing/k6-suite/scenarios/c1.js';
import { stage1 } from '../../../../../testing/k6-suite/stages/stage1.js';

const scenario = defineC1(stage1);

export const options = scenario.options;
export const handleSummary = scenario.handleSummary;
export function setup() { return scenario.setup(); }
export default scenario.run;
