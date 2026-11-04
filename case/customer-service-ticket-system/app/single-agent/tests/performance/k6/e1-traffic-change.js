// E1 流量变化场景（突发/骤降，PATTERN 选择）——薄入口：场景实现与判定口径在案例共享套件 k6-suite/ 内
// 用法（阶段目录下）：k6 run tests/performance/k6/e1-traffic-change.js -e PATTERN=spike（需先 mkdir -p out）
import { defineE1 } from '../../../../../testing/k6-suite/scenarios/e1-traffic-change.js';
import { stage1 } from '../../../../../testing/k6-suite/stages/stage1.js';

const scenario = defineE1(stage1);

export const options = scenario.options;
export const handleSummary = scenario.handleSummary;
export function setup() { return scenario.setup(); }
export default scenario.run;
