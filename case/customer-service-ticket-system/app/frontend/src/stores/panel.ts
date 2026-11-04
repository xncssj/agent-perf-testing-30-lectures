/** 客户/订单面板投影层：把一份工具结果结构化为面板模型（纯函数，无响应式状态）。
 *
 * 架构目的：后端返回订单/物流全量 schema，投影层在这里一次性做字段映射与中文化，
 * chat store 只负责把结果喂给 applySnapshot，面板只负责渲染 CustomerPanelModel；
 * 每个字段自带来源锚点（run_id + 工具 + 时刻），呼应第 17 讲证据链与口径。
 */
import type { CustomerPanelModel, LogisticsSnapshot, OrderSnapshot } from '@/types'

const STATUS_TEXT: Record<string, string> = {
  shipped: '已发货',
  delivered: '已签收',
  pending: '待发货',
  processing: '处理中',
  refunding: '退款中',
  closed: '已关闭',
  not_found: '未找到订单',
}

export function orderStatusText(s?: string): string {
  return s ? (STATUS_TEXT[s] || s) : '—'
}

const STOP_REASON_TEXT: Record<string, string> = {
  answered: '已作答',
  max_iterations: '达到迭代上限',
  tool_limit: '达到工具调用上限',
  refused: '模型拒绝作答',
  client_disconnect: '客户端断连',
  timeout: '超时',
  guardrail: '护栏拦截',
  error: '运行异常',
  unknown: '未知',
}

const USAGE_SOURCE_TEXT: Record<string, string> = {
  estimated: '估算',
  actual: '实际',
  mixed: '混合',
  missing: '缺失',
}

const EVIDENCE_KIND_TEXT: Record<string, string> = {
  mock: '模拟覆盖',
  replay: '回放校准',
  real: '真实流式',
}

export function stopReasonText(s?: string): string {
  return s ? (STOP_REASON_TEXT[s] || s) : '—'
}

export function usageSourceText(s?: string): string {
  return s ? (USAGE_SOURCE_TEXT[s] || s) : '—'
}

export function evidenceKindText(s?: string): string {
  return s ? (EVIDENCE_KIND_TEXT[s] || s) : '—'
}

function amountOf(o: OrderSnapshot): string {
  if (o.total_amount == null) return ''
  const n = Number(o.total_amount)
  return Number.isFinite(n) ? `¥${n.toLocaleString('zh-CN')}` : ''
}

function addressOf(o: OrderSnapshot): string {
  const a = o.shipping_address
  if (!a) return ''
  return [a.province, a.city, a.district, a.address].filter(Boolean).join(' ')
}

export interface SnapshotCtx {
  runId: string
  ts: string
}

/** 面板空壳（未运行任何工具时） */
export function emptyPanel(): CustomerPanelModel {
  return {
    user: '—',
    order: '—',
    orderStatus: '—',
    logistics: '—',
    carrier: '—',
    amount: '—',
    address: '—',
    runId: '',
    tool: '',
    ts: '',
    found: false,
  }
}

/** 把 query_order / query_logistics / miss 投影到面板字段（prev 为当前面板值） */
export function applySnapshot(
  prev: CustomerPanelModel,
  kind: 'order' | 'logistics' | 'miss',
  data: unknown,
  ctx: SnapshotCtx,
): CustomerPanelModel {
  const next = { ...prev, runId: ctx.runId, tool: kind, ts: ctx.ts }

  if (kind === 'order') {
    const raw = (data || {}) as Record<string, unknown>
    const o = ((raw.order || raw) as OrderSnapshot) || {}
    next.user = o.user_id || prev.user
    next.order = String(o.order_id || prev.order)
    next.orderStatus = orderStatusText(o.status)
    const t = String(o.tracking_no || o.trackingNo || '')
    next.carrier = o.carrier || '—'
    if (t) next.logistics = '已发货 · ' + t
    next.amount = amountOf(o) || '—'
    next.address = addressOf(o) || '—'
    next.found = true
  } else if (kind === 'logistics') {
    const raw = (data || {}) as Record<string, unknown>
    const l = ((raw.logistics || raw) as LogisticsSnapshot) || {}
    next.carrier = l.carrier || next.carrier
    const loc = String(l.current_location || l.currentLocation || l.location || '')
    const st = String(l.status || '')
    if (loc || st) next.logistics = [loc, st].filter(Boolean).join(' · ') || '—'
    next.found = true
  } else {
    next.orderStatus = '未找到订单'
    next.logistics = '—'
    next.found = false
  }
  return next
}