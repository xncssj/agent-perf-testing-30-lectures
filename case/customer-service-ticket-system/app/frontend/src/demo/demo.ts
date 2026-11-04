export const BOARD_COLS = [
  { k: 'todo', t: '待处理', color: '#64748b' },
  { k: 'ai', t: 'AI 处理中', color: '#2563eb' },
  { k: 'clarify', t: '待澄清', color: '#d97706' },
  { k: 'human', t: '人工处理中', color: '#7c3aed' },
  { k: 'done', t: '已解决', color: '#16a34a' },
  { k: 'closed', t: '已关闭', color: '#94a3b8' },
]

export const UNIFIED_STEPS = [
  { k: '1', t: '意图识别' },
  { k: '2', t: '参数提取' },
  { k: '3', t: '订单/物流查询' },
  { k: '4', t: '知识库检索' },
  { k: '5', t: '答复生成' },
]

export const TOOL_URI: Record<string, (a: Record<string, unknown>) => string> = {
  query_order: (a) => 'GET /api/orders/' + (String(a.order_id ?? '?')),
  query_logistics: (a) =>
    a.tracking_no
      ? 'GET /api/logistics/' + String(a.tracking_no)
      : 'GET /api/logistics/order/' + String(a.order_id ?? '?'),
  search_knowledge: (a) =>
    'GET /api/kb/search?q=' + encodeURIComponent(String(a.query ?? a.query_text ?? '')),
}
