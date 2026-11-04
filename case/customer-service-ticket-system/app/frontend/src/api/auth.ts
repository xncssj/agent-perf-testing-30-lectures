import { apiBase } from './http'

const TOKEN_KEY = 'cs-admin-token'

export interface AdminUser {
  id: number
  username: string
  display_name: string
  email: string
  phone: string
  title: string
  updated_at: string
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token: string) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

async function authFetch<T>(path: string, method: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const r = await fetch(`${apiBase()}${path}`, {
    method,
    headers,
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

export function login(username: string, password: string) {
  return authFetch<{ token: string; user: AdminUser }>('/api/v1/auth/login', 'POST', {
    username,
    password,
  })
}

export function fetchMe() {
  return authFetch<{ user: AdminUser }>('/api/v1/auth/me', 'GET')
}

export function logout() {
  return authFetch<{ success: boolean }>('/api/v1/auth/logout', 'POST')
}

export function updateProfile(body: Partial<AdminUser>) {
  return authFetch<{ user: AdminUser }>('/api/v1/auth/profile', 'PUT', body)
}

export function changePassword(oldPassword: string, newPassword: string) {
  return authFetch<{ success: boolean }>('/api/v1/auth/password', 'PUT', {
    old_password: oldPassword,
    new_password: newPassword,
  })
}
