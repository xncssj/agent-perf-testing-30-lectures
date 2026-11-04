/**
 * 公网网关会话（VITE_AUTH_MODE=gateway 时启用）。
 *
 * 契约（接口契约 §2/§3）：会话在 `__Host-cs-session` Cookie 中，脚本不可读；
 * CSRF 令牌经 GET /auth/session 获取后只保存在运行内存，不写入 localStorage 等
 * 持久存储；写请求统一附带 X-CSRF-Token。本地开发模式（auth_mode=local）不使用
 * 本模块，继续走案例一 localStorage 令牌（接口契约 §6 兼容路径）。
 */

export const AUTH_MODE = import.meta.env.VITE_AUTH_MODE || 'local'
export const STAGE = import.meta.env.VITE_STAGE || ''
export const IS_GATEWAY = AUTH_MODE === 'gateway'

/** 网关根路径（部署在网关域名根，无 BASE_URL 前缀） */
const GATEWAY_ROOT = '/'

let csrfToken = ''
let username = ''

export function getCsrfToken(): string {
  return csrfToken
}

export function getGatewayUsername(): string {
  return username
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string }
    return body.detail || `HTTP ${response.status}`
  } catch {
    return `HTTP ${response.status}`
  }
}

/** 探测网关会话；有效则缓存用户名与 CSRF，失效返回 false */
export async function ensureGatewaySession(): Promise<boolean> {
  try {
    const response = await fetch(`${GATEWAY_ROOT}auth/session`)
    if (!response.ok) {
      csrfToken = ''
      username = ''
      return false
    }
    const body = (await response.json()) as { username: string; csrf_token: string }
    username = body.username
    csrfToken = body.csrf_token
    return true
  } catch {
    return false
  }
}

/** 网关登录：成功（204）后立即拉取 CSRF；失败返回错误文案 */
export async function gatewayLogin(
  user: string,
  password: string,
): Promise<{ ok: boolean; error?: string }> {
  const response = await fetch(`${GATEWAY_ROOT}auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password }),
  })
  if (response.status !== 204) {
    return { ok: false, error: await parseError(response) }
  }
  const ok = await ensureGatewaySession()
  return ok ? { ok: true } : { ok: false, error: '登录成功但会话初始化失败' }
}

/** 网关退出：一次退出使三个阶段一并失效（V09） */
export async function gatewayLogout(): Promise<void> {
  try {
    await fetch(`${GATEWAY_ROOT}auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    })
  } catch {
    /* 会话已失效时服务端自然 401，仍按已退出处理 */
  }
  csrfToken = ''
  username = ''
}

/** 跳网关统一登录页；next 仅接受本阶段前缀下的站内相对路径（防任意跳转） */
export function redirectToGatewayLogin(nextPath: string): void {
  const prefix = STAGE ? `/${STAGE}/` : ''
  let next = nextPath
  if (prefix) {
    if (!next.startsWith(prefix)) next = prefix
  } else if (!next.startsWith('/')) {
    next = '/'
  }
  location.assign(`${GATEWAY_ROOT}login?next=${encodeURIComponent(next)}`)
}
