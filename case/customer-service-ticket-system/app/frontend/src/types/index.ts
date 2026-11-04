/** 会话列表项（演示 + 真实运行同步） */
export interface ConversationItem {
  id: string
  person: string
  order?: string
  time: string
  unread?: number
  priority?: 'urgent' | 'high' | 'normal'
  status: string
  preview: string
  source?: string
}

/** 看板工单卡 */
export interface BoardTicket {
  id: string
  q: string
  pri: string
  src: string
  st: string
  sla: string
  user_id?: string
  order_id?: string
  difficulty?: string
}

/** 客户库取样 */
export interface CustomerTicket {
  ui: string
  tid: string
  d: string
  q: string
}

/** 前端本地模型配置（不驱动后端推理，仅 UI 管理） */
export interface ModelConfig {
  id: string
  name: string
  provider: 'mock' | 'openai'
  baseUrl: string
  model: string
  apiKey: string
  active: boolean
}

/**
 * Chat 请求体（对齐三个阶段的后端 ChatRequest）：
 * 案例一/二发送 user_query（案例二仍为 user_query）；案例三发送
 * query + execution_id（接口契约 §4），两组字段按阶段二选一。
 */
export interface ChatRequest {
  conversation_id?: string
  user_query?: string
  query?: string
  execution_id?: string
  task_id?: string
  batch_id?: string
  stream?: boolean
}

/** SSE / 运行事件 */
export type StreamEvent =
  | { type: 'thinking'; content?: string }
  | { type: 'tool_call'; tool?: string; args?: Record<string, unknown> }
  | { type: 'tool_result'; tool?: string; data?: unknown; duration_ms?: number; found?: boolean; error?: string }
  | { type: 'answer'; content?: string; text?: string }
  | { type: string; [k: string]: unknown }

export interface RunMetrics {
  e2e_ms?: number
  first_answer_ms?: number
  ttft_ms?: number
  total_tokens?: number
  token_count?: number
  usage_source?: string
  evidence_kind?: string
  [k: string]: unknown
}

export interface ToolCallTrace {
  tool?: string
  args?: Record<string, unknown>
  data?: unknown
  duration_ms?: number
  found?: boolean
  error?: string
}

export interface RunResult {
  status?: string
  stop_reason?: string
  intent?: string
  response?: string
  answer?: string
  metrics?: RunMetrics
  trace?: {
    intent?: string
    tool_calls?: ToolCallTrace[]
  }
}

export interface TimelineItem {
  kind: 'thinking' | 'tool_call' | 'tool_result'
  title: string
  uri?: string
  sub?: string
  data?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'agent'
  text?: string
  time: string
  /** Agent 块状态 */
  running?: boolean
  statusLabel?: string
  statusCls?: string
  intent?: string
  timeline: TimelineItem[]
  timelineOpen?: boolean
  answer?: string
  streaming?: boolean
  tail?: { label: string; value: string }[]
}

export interface SideMetrics {
  status: string
  stop: string
  e2e: string
  first: string
  token: string
  usageSrc: string
  evidence: string
}

/** 订单快照（对齐后端 query_order 返回） */
export interface OrderSnapshot {
  order_id?: string
  user_id?: string
  status?: string
  carrier?: string
  tracking_no?: string
  trackingNo?: string
  items?: { product_id?: string; product_name?: string; quantity?: number; price?: number }[]
  total_amount?: number
  shipping_address?: {
    province?: string
    city?: string
    district?: string
    address?: string
  }
}

/** 物流快照（对齐后端 query_logistics 返回） */
export interface LogisticsSnapshot {
  tracking_no?: string
  trackingNo?: string
  order_id?: string
  carrier?: string
  status?: string
  current_location?: string
  currentLocation?: string
  location?: string
  origin?: string
  destination?: string
}

/** 客户面板投影模型：一份工具结果的结构化视图，带来源锚点 */
export interface CustomerPanelModel {
  user: string
  order: string
  orderStatus: string
  logistics: string
  carrier: string
  amount: string
  address: string
  /** 来源锚点：run_id / task_id、工具名、时刻（证据链用） */
  runId: string
  tool: string
  ts: string
  found: boolean
}

export interface HistSeries {
  buckets: { le: string; n: number }[]
  sum: number
  count: number
}

export interface PromParsed {
  counters: Record<string, number>
  hist: { byPath: Record<string, HistSeries>; paths: string[] }
}
