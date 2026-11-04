// S1 长稳（固定并发，含早/中/晚段位与末段劣化判定）（阶段3 Workflow+RAG）——薄入口：场景实现与判定口径在案例共享套件 k6-suite/ 内
// 用法（阶段目录下）：tests/performance/k6/run-scenario.sh s1
//   或：k6 run tests/performance/k6/s1-stability.js（需先 mkdir -p out）
import { defineS1 } from '../../../../../testing/k6-suite/scenarios/s1.js';
import { stage3 } from '../../../../../testing/k6-suite/stages/stage3.js';

const scenario = defineS1(stage3);

export const options = scenario.options;
export const handleSummary = scenario.handleSummary;
export function setup() { return scenario.setup(); }
export default scenario.run;
