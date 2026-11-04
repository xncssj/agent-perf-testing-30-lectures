// E2 多轮上下文场景（逐轮累积上下文，ctx_turn 分档判定）——薄入口：场景实现与判定口径在案例共享套件 k6-suite/ 内
// 用法（阶段目录下）：k6 run tests/performance/k6/e2-multiturn.js -e TURNS=5（需先 mkdir -p out）
import { defineE2 } from '../../../../../testing/k6-suite/scenarios/e2-multiturn.js';
import { stage3 } from '../../../../../testing/k6-suite/stages/stage3.js';

const scenario = defineE2(stage3);

export const options = scenario.options;
export const handleSummary = scenario.handleSummary;
export function setup() { return scenario.setup(); }
export default scenario.run;
