// 长稳段位与劣化计算（纯 ESM，无 k6 依赖，node 可直接测试）
// 口径来源：方案 §5「长稳末段劣化 ≤20%，对比预热后首段，排除升温效应」、
//           §8.3「首末段对比由时序分桶后在报告层解读」。
// 段位切分按 k6 官方 exec.scenario.progress（0—1 的场景进度）三等分。

export const SEGMENTS = ['early', 'mid', 'late'];

// 场景进度 → 段位（边界按 1/3、2/3 归入后一段）
export function segmentOf(progress) {
  const p = Number(progress);
  if (!Number.isFinite(p)) return 'early';
  if (p < 1 / 3) return 'early';
  if (p < 2 / 3) return 'mid';
  return 'late';
}

// 末段相对首段的劣化百分比：(late/early - 1) × 100；样本缺失或首段为 0 时返回 null
export function degradationPct(earlyAvg, lateAvg) {
  const e = Number(earlyAvg);
  const l = Number(lateAvg);
  if (!Number.isFinite(e) || !Number.isFinite(l) || e <= 0) return null;
  return (l / e - 1) * 100;
}

// 劣化判定：默认限值 20%（方案 §5）；返回 {pct, ok, reason}
export function degradationVerdict(earlyAvg, lateAvg, limitPct) {
  const limit = Number.isFinite(Number(limitPct)) ? Number(limitPct) : 20;
  const pct = degradationPct(earlyAvg, lateAvg);
  if (pct === null) return { pct: null, ok: null, reason: '样本不足或首段均值为 0，无法判定' };
  return { pct: pct, ok: pct <= limit, reason: '限值 ' + limit + '%' };
}
