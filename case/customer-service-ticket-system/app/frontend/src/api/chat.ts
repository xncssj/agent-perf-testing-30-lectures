import type { ChatRequest, RunResult, StreamEvent } from '@/types'
import { apiBase, writeAuthHeaders } from './http'

const jsonHeaders = { 'Content-Type': 'application/json' }

export async function fetchHealth(): Promise<{ status?: string; mode?: string }> {
  const r = await fetch(`${apiBase()}/health`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

export async function fetchVersion(): Promise<Record<string, unknown>> {
  const r = await fetch(`${apiBase()}/version`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

export async function fetchMetricsText(): Promise<string> {
  const r = await fetch(`${apiBase()}/metrics`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.text()
}

export async function completeChat(payload: ChatRequest): Promise<RunResult> {
  const r = await fetch(`${apiBase()}/api/v1/chat/complete`, {
    method: 'POST',
    headers: { ...jsonHeaders, ...writeAuthHeaders() },
    body: JSON.stringify(payload),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

/** SSE 流式对话：逐条回调已解析的 data JSON */
export async function streamChat(
  payload: ChatRequest,
  onEvent: (raw: StreamEvent | RunResult) => void,
): Promise<void> {
  const res = await fetch(`${apiBase()}/api/v1/chat/stream`, {
    method: 'POST',
    headers: { ...jsonHeaders, ...writeAuthHeaders() },
    body: JSON.stringify(payload),
  })
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''

  function processBuffer(): void {
    let idx = buf.indexOf('\n\n')
    while (idx >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const line = raw.split('\n').find((l) => l.startsWith('data:'))
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(5).trim()) as StreamEvent | RunResult)
        } catch {
          /* 忽略无法解析的碎片 */
        }
      }
      idx = buf.indexOf('\n\n')
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    processBuffer()
  }

  // 处理 buffer 中残留的最后一段数据（无 \n\n 分隔符的情况）
  if (buf.trim()) {
    const line = buf.split('\n').find((l) => l.startsWith('data:'))
    if (line) {
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as StreamEvent | RunResult)
      } catch {
        /* 忽略无法解析的碎片 */
      }
    }
  }
}

export function docsUrl(): string {
  const base = apiBase()
  return base ? `${base}/docs` : '/docs'
}
