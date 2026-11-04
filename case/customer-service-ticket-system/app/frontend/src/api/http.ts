import type { HistSeries, PromParsed } from '@/types'
import { IS_GATEWAY, getCsrfToken } from './gatewayAuth'

/** API 基址：空串走 Vite proxy；直连时填 http://localhost:8000 */
export function apiBase(): string {
  const env = import.meta.env.VITE_API_BASE as string | undefined
  if (env != null && env !== '') return env.replace(/\/$/, '')
  return ''
}

/** 网关模式的写请求头：会话绑定 CSRF（接口契约 §3）；本地模式返回空集 */
export function writeAuthHeaders(): Record<string, string> {
  if (!IS_GATEWAY) return {}
  return { 'X-CSRF-Token': getCsrfToken() }
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`
}

export function nowTime(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

export function fmtArgs(args: unknown): string {
  try {
    return JSON.stringify(args, null, 1)
  } catch {
    return String(args)
  }
}

function _labelValue(labelText: string, name: string): string | undefined {
  const m = new RegExp(`${name}="([^"]*)"`).exec(labelText)
  return m ? m[1] : undefined
}

export function parseProm(text: string): PromParsed {
  const counters: Record<string, number> = {}
  for (const m of text.matchAll(/^agent_run_terminal_total\{([^}]*)\}\s+([\d.]+)/gm)) {
    const label = m[1].replace(/\s/g, '')
    counters[label] = Number(m[2])
  }
  const byPath: Record<string, HistSeries> = {}
  const getSeries = (path: string): HistSeries => {
    if (!byPath[path]) byPath[path] = { buckets: [], sum: 0, count: 0 }
    return byPath[path]!
  }
  for (const m of text.matchAll(/^agent_e2e_latency_seconds_bucket\{([^}]*)\}\s+([\d.]+)/gm)) {
    const path = _labelValue(m[1]!, 'path')
    const le = _labelValue(m[1]!, 'le')
    if (path && le) {
      getSeries(path).buckets.push({
        le: le === '+Inf' ? 'Inf' : le,
        n: Number(m[2]),
      })
    }
  }
  for (const m of text.matchAll(/^agent_e2e_latency_seconds_(count|sum)\{([^}]*)\}\s+([\d.]+)/gm)) {
    const path = _labelValue(m[2]!, 'path')
    if (path) {
      const series = getSeries(path)
      if (m[1] === 'count') series.count = Number(m[3])
      else series.sum = Number(m[3])
    }
  }
  for (const series of Object.values(byPath)) {
    series.buckets.sort((a, b) => {
      const na = a.le === 'Inf' ? Infinity : Number(a.le)
      const nb = b.le === 'Inf' ? Infinity : Number(b.le)
      return na - nb
    })
  }
  return {
    counters,
    hist: { byPath, paths: Object.keys(byPath) },
  }
}
