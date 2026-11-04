import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchHealth } from '@/api/chat'
import { apiBase } from '@/api/http'

export const useAppStore = defineStore('app', () => {
  const connected = ref(false)
  const connText = ref('检测后端…')
  const connUrl = ref(apiBase() || location.origin)
  const chatMode = ref<'stream' | 'complete'>('stream')

  async function checkHealth() {
    connUrl.value = apiBase() || location.origin
    try {
      const j = await fetchHealth()
      if (j && j.status === 'ok') {
        connected.value = true
        connText.value = '后端已连接 · ' + (j.mode || 'running')
        return true
      }
      connected.value = false
      connText.value = '后端未连接'
      return false
    } catch {
      connected.value = false
      connText.value = '后端未连接'
      return false
    }
  }

  return { connected, connText, connUrl, chatMode, checkHealth }
})
