import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { IS_GATEWAY, STAGE } from '@/api/gatewayAuth'
import { fetchConversations, fetchTickets } from '@/api/catalog'
import type { BoardTicket, ConversationItem } from '@/types'

// 目录接口能力开关（接口契约 §4）：案例二、三公网构建不请求未实现的
// conversations/tickets 等目录接口，只保留核心对话
export const CATALOG_SUPPORTED = !IS_GATEWAY || STAGE === 'single'

export const useSessionsStore = defineStore('sessions', () => {
  const list = ref<ConversationItem[]>([])
  const quick = ref<string[]>([])
  const boardTickets = ref<BoardTicket[]>([])
  // 工单数据加载态：null=尚未加载，false=后端未读到 tickets.json（看板空）
  const ticketsLoaded = ref<boolean | null>(null)
  const ticketsDir = ref('')
  const source = ref('')
  const statusFilter = ref('all')
  const searchKw = ref('')
  const current = ref<ConversationItem | null>(null)
  const error = ref('')

  const visible = computed(() => {
    const st = statusFilter.value
    const kw = searchKw.value.trim().toLowerCase()
    return list.value.filter((c) => {
      if (st && st !== 'all' && c.status !== st) return false
      if (kw) {
        const hay = (c.person + c.preview + (c.order || '')).toLowerCase()
        if (!hay.includes(kw)) return false
      }
      return true
    })
  })

  async function loadConversations() {
    const data = await fetchConversations()
    list.value = data.items
    quick.value = data.quick
    source.value = data.source
    error.value = ''
  }

  async function loadTickets() {
    const data = await fetchTickets()
    boardTickets.value = data.items
    ticketsLoaded.value = data.tickets_loaded !== false
    ticketsDir.value = data.tickets_dir || ''
  }

  async function refresh() {
    if (!CATALOG_SUPPORTED) return
    try {
      await Promise.all([loadConversations(), loadTickets()])
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  function selectConv(c: ConversationItem) {
    current.value = c
  }

  function newChat() {
    current.value = {
      id: 'new-' + Date.now(),
      person: '新会话',
      preview: '新会话已就绪',
      status: 'ai',
      time: '',
      source: 'local',
    }
  }

  return {
    list,
    quick,
    boardTickets,
    ticketsLoaded,
    ticketsDir,
    source,
    statusFilter,
    searchKw,
    current,
    error,
    visible,
    refresh,
    selectConv,
    newChat,
  }
})
