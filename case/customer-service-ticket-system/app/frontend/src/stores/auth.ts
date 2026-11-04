import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchMe, getToken, login as apiLogin, logout as apiLogout, setToken, type AdminUser } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getToken())
  const user = ref<AdminUser | null>(null)

  async function login(username: string, password: string) {
    const data = await apiLogin(username, password)
    token.value = data.token
    user.value = data.user
    setToken(data.token)
  }

  async function ensure(): Promise<boolean> {
    token.value = getToken()
    if (!token.value) {
      user.value = null
      return false
    }
    try {
      const data = await fetchMe()
      user.value = data.user
      return true
    } catch {
      token.value = ''
      user.value = null
      setToken('')
      return false
    }
  }

  async function logout() {
    try {
      if (getToken()) await apiLogout()
    } catch {
      /* 会话已失效时仍清理本地令牌 */
    }
    token.value = ''
    user.value = null
    setToken('')
  }

  function applyUser(next: AdminUser) {
    user.value = next
  }

  return { token, user, login, ensure, logout, applyUser }
})
