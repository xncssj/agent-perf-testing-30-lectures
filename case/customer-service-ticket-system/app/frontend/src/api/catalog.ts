/** 目录接口：会话、工单、客户、知识库、分析、模型。 */
import type { BoardTicket, ConversationItem, ModelConfig } from '@/types'
import { apiBase, writeAuthHeaders } from './http'

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(`${apiBase()}${path}`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

async function sendJson<T>(path: string, method: string, body?: unknown): Promise<T> {
  const r = await fetch(`${apiBase()}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...writeAuthHeaders() },
    body: body == null ? undefined : JSON.stringify(body),
  })
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const j = await r.json()
      if (j.detail) detail = String(j.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return r.json()
}

async function sendForm<T>(path: string, method: string, form: FormData): Promise<T> {
  const r = await fetch(`${apiBase()}${path}`, {
    method,
    headers: writeAuthHeaders(),
    body: form,
  })
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const j = await r.json()
      if (j.detail) detail = String(j.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return r.json()
}

export interface AnalyticsPayload {
  kpis: { lbl: string; val: string; sub: string; cls: string }[]
  e2e: { k: string; v: number | null }[]
  slo: string[][]
  slo_source: string
  sample_count: number
  tools: { tool: string; uri: string; service: string }[]
}

export function fetchConversations() {
  return getJson<{ items: ConversationItem[]; quick: string[]; source: string }>('/api/v1/conversations')
}

export function fetchTickets() {
  return getJson<{ items: BoardTicket[]; source: string; tickets_loaded?: boolean; tickets_dir?: string }>('/api/v1/tickets')
}

export interface CustomersPayload {
  items: { ui: string; tid: string; order_id: string; d: string; q: string; branch: string }[]
  total: number
  page: number
  page_size: number
  source: string
}

export function fetchCustomers(params: { page?: number; pageSize?: number; q?: string } = {}) {
  const qs = new URLSearchParams({
    page: String(params.page ?? 1),
    page_size: String(params.pageSize ?? 20),
    q: params.q ?? '',
  }).toString()
  return getJson<CustomersPayload>(`/api/v1/customers?${qs}`)
}

export function fetchKnowledge(q = '') {
  const qs = q ? `?q=${encodeURIComponent(q)}&top_k=5` : ''
  return getJson<{
    items: { id?: number; category?: string; title?: string; content?: string; keywords?: string[]; rank?: number }[]
    query: string
    source: string
  }>(`/api/v1/knowledge${qs}`)
}

export function createKnowledge(body: { category: string; title: string; content: string; keywords?: string[] }) {
  return sendJson<{ item: { id: number; category: string; title: string; content: string; keywords: string[] } }>(
    '/api/v1/knowledge', 'POST', body,
  )
}

export function updateKnowledge(id: number, body: { category?: string; title?: string; content?: string; keywords?: string[] }) {
  return sendJson<{ item: { id: number; category: string; title: string; content: string; keywords: string[] } }>(
    `/api/v1/knowledge/${id}`, 'PUT', body,
  )
}

export function deleteKnowledge(id: number) {
  return sendJson<{ success: boolean }>(`/api/v1/knowledge/${id}`, 'DELETE')
}

export function uploadKnowledge(file: File, category: string = 'uploaded', chunkSize: number = 500) {
  const formData = new FormData()
  formData.append('file', file)
  return sendForm<{
    success: boolean
    filename: string
    category: string
    total_chunks: number
    items: { id: number; category: string; title: string; content: string; keywords: string[] }[]
  }>(`/api/v1/knowledge/upload?category=${encodeURIComponent(category)}&chunk_size=${chunkSize}`, 'POST', formData)
}

export function fetchAnalytics() {
  return getJson<AnalyticsPayload>('/api/v1/analytics')
}

export function fetchModels() {
  return getJson<{
    items: ModelConfig[]
    runtime: { provider: string; model: string; base_url: string; note: string }
  }>('/api/v1/models')
}

export function createModel(body: Partial<ModelConfig>) {
  return sendJson<ModelConfig>('/api/v1/models', 'POST', {
    name: body.name,
    provider: body.provider,
    base_url: body.baseUrl,
    model: body.model,
    api_key: body.apiKey,
  })
}

export function updateModel(id: string, body: Partial<ModelConfig>) {
  return sendJson<ModelConfig>(`/api/v1/models/${id}`, 'PUT', {
    name: body.name,
    provider: body.provider,
    base_url: body.baseUrl,
    model: body.model,
    api_key: body.apiKey,
  })
}

export function deleteModel(id: string) {
  return sendJson<{ success: boolean }>(`/api/v1/models/${id}`, 'DELETE')
}

export function activateModel(id: string) {
  return sendJson<ModelConfig>(`/api/v1/models/${id}/activate`, 'POST')
}
