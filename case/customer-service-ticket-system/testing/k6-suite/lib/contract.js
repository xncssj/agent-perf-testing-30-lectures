// 契约常量与三态门禁（纯 ESM，无 k6 依赖，node 可直接测试）。
// 唯一事实源：课程案例的压测契约（契约版本见下）。
// 方案、场景阈值、看板与报告一律从本文件继承取值，不允许另设默认阈值或二态判定。
// 兼容约定：为避免 k6（goja）运行时差异，本文件不使用 ?? / ?. 语法。

// 契约版本：写入每次运行的 manifest、摘要与报告，用于区分新契约产物与历史产物
export const CONTRACT_VERSION = 'load-test-remediation-v1';

// 门禁阈值（接口契约 §3.3）：等于边界值时满足该项条件（等值判定，不用严格不等号）
export const QUALIFIED_RATE_MIN = 0.90;
export const ERROR_RATE_MAX = 0.005;
// e2e_slo_ms 不设全局默认值：每次运行前按场景解析（阶段默认 / /version 指纹 / 环境显式注入）。

// 业务 E2E 双口径字段名（接口契约 §3.1）：客户端墙钟是唯一门禁来源；服务端值只作诊断
export const E2E_CLIENT_WALL_METRIC = 'e2e_client_wall_ms';
export const E2E_SERVER_METRIC = 'e2e_server_ms';

// 三态门禁判定（接口契约 §5）：NOT_VERIFIED 不是 PASS 的别名，也不得当作合格档位
export const GATE_PASS = 'PASS';
export const GATE_FAIL = 'FAIL';
export const GATE_NOT_VERIFIED = 'NOT_VERIFIED';

// C1 档位时间结构（接口契约 §2 必需规则）：每档默认 3 分钟 = 稳定 60s + 计量 120s。
// 稳定期样本不进入容量分母，计量窗口是 Goodput、Token、成本的唯一共同范围。
export const C1_STABILIZATION_SECONDS = 60;
export const C1_MEASURED_SECONDS = 120;

// 采样与状态汇总上限（接口契约 §7）：监控角色事件写入间隔不得超过对应值（秒）。
// 监控角色本体由运行隔离层提供；聚合器发现缺口影响必需证据覆盖时按 NOT_VERIFIED 判定。
export const SAMPLING_LIMIT_SECONDS = {
  environment: 15,
  business: 10,
  coordinator: 30,
};

// 建议的最小原因码（接口契约 §5）；INSUFFICIENT_SAMPLES 为其显式扩展（无样本不可计算阈值）
export const GATE_REASON = {
  MISSING_MANIFEST: 'MISSING_MANIFEST',
  MISSING_SAFETY_EVIDENCE: 'MISSING_SAFETY_EVIDENCE',
  WINDOW_MISMATCH: 'WINDOW_MISMATCH',
  ARTIFACT_HASH_MISMATCH: 'ARTIFACT_HASH_MISMATCH',
  QUALIFIED_RATE_BELOW_MIN: 'QUALIFIED_RATE_BELOW_MIN',
  ERROR_RATE_ABOVE_MAX: 'ERROR_RATE_ABOVE_MAX',
  E2E_SLO_EXCEEDED: 'E2E_SLO_EXCEEDED',
  FUSED: 'FUSED',
  ABORTED: 'ABORTED',
  INSUFFICIENT_SAMPLES: 'INSUFFICIENT_SAMPLES',
};

// k6 阈值表达式（等值边界）：rate>=min / rate<=max / p(90)<=slo。
// 场景脚本只用这里产出的表达式，杜绝局部写严格不等号造成的边界口径漂移。
export function k6ThresholdExprs(sloMs) {
  return {
    qualifiedRate: 'rate>=' + QUALIFIED_RATE_MIN,
    errorRate: 'rate<=' + ERROR_RATE_MAX,
    e2eP90: 'p(90)<=' + Number(sloMs),
  };
}

// measured 窗口实际秒数：只认 manifest 中 measured 起止时间的差值；
// 缺失、不可解析或起止倒挂时返回 null，调用方不得用计划时长或整场时长顶替。
export function measuredSeconds(windows) {
  const w = windows || {};
  const m = w.measured || {};
  if (!m.start || !m.end) return null;
  const start = Date.parse(m.start);
  const end = Date.parse(m.end);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return (end - start) / 1000;
}

// 三态门禁（接口契约 §5，判定顺序固定）：
//   1) 校验 manifest、时间窗口、熔断/中止状态与必需安全证据；
//   2) 任一缺失或不一致 → NOT_VERIFIED（附原因码）；
//   3) 证据齐全时计算三项阈值；任一不满足 → FAIL（附原因码）；
//   4) 全部满足 → PASS。
// input 字段：
//   hasManifest     boolean  当前 run_id 的 manifest 是否在场
//   windowAligned   boolean  证据与样本是否对齐 measured 窗口（缺省视为未知，不单独判 NOT_VERIFIED）
//   fused / aborted boolean  运行是否被熔断或中止（熔断/中止窗口不可判 PASS）
//   evidence        Array    [{name, present}] 必需安全证据清单及在场性
//   qualifiedRate / errorRate / e2eP90Ms / sloMs  三项门禁输入
// 空值安全的数值化：null/undefined/空串视为缺失（NaN），避免 Number(null)=0 被当成零错误率通过门禁
function toNum(v) {
  if (v === null || v === undefined || v === '') return NaN;
  return Number(v);
}

export function evaluateGate(input) {
  const i = input || {};
  const reasons = [];
  if (i.hasManifest !== true) reasons.push(GATE_REASON.MISSING_MANIFEST);
  if (i.windowAligned === false) reasons.push(GATE_REASON.WINDOW_MISMATCH);
  const evidence = Array.isArray(i.evidence) ? i.evidence : [];
  for (let k = 0; k < evidence.length; k++) {
    if (!evidence[k] || evidence[k].present !== true) {
      reasons.push(GATE_REASON.MISSING_SAFETY_EVIDENCE);
      break;
    }
  }
  if (i.fused === true) reasons.push(GATE_REASON.FUSED);
  if (i.aborted === true) reasons.push(GATE_REASON.ABORTED);
  if (reasons.length) return { verdict: GATE_NOT_VERIFIED, reason_codes: reasons };

  const qualifiedRate = toNum(i.qualifiedRate);
  const errorRate = toNum(i.errorRate);
  const e2eP90Ms = toNum(i.e2eP90Ms);
  const sloMs = toNum(i.sloMs);
  if (!Number.isFinite(qualifiedRate) || !Number.isFinite(errorRate)
    || !Number.isFinite(e2eP90Ms) || !Number.isFinite(sloMs)) {
    return { verdict: GATE_NOT_VERIFIED, reason_codes: [GATE_REASON.INSUFFICIENT_SAMPLES] };
  }

  // 等值边界：恰好等于阈值时满足该项条件（契约 §3.3）
  const checks = [];
  if (qualifiedRate < QUALIFIED_RATE_MIN) checks.push(GATE_REASON.QUALIFIED_RATE_BELOW_MIN);
  if (errorRate > ERROR_RATE_MAX) checks.push(GATE_REASON.ERROR_RATE_ABOVE_MAX);
  if (e2eP90Ms > sloMs) checks.push(GATE_REASON.E2E_SLO_EXCEEDED);
  if (checks.length) return { verdict: GATE_FAIL, reason_codes: checks };
  return { verdict: GATE_PASS, reason_codes: [] };
}
