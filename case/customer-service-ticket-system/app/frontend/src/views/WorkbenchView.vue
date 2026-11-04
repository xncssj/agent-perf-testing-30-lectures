<script setup lang="ts">
import { nextTick, computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import Composer from '@/components/chat/Composer.vue'
import CustomerPanel from '@/components/chat/CustomerPanel.vue'
import MessageList from '@/components/chat/MessageList.vue'
import DemoBadge from '@/components/common/DemoBadge.vue'
import { useAppStore } from '@/stores/app'
import { useChatStore } from '@/stores/chat'
import { CATALOG_SUPPORTED, useSessionsStore } from '@/stores/sessions'
import type { ConversationItem } from '@/types'

const app = useAppStore()
const chat = useChatStore()
const sessions = useSessionsStore()
const msgBox = ref<HTMLElement | null>(null)

// 核心对话保留，目录栏与工单流程侧栏按阶段能力开关显示（接口契约 §4）
const showCatalog = computed(() => CATALOG_SUPPORTED)

watch(
  () => chat.messages.length,
  async () => {
    await nextTick()
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  },
)

watch(
  () => chat.messages.map((m) => m.answer).join(''),
  async () => {
    await nextTick()
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  },
)

const statusTabs = [
  { st: 'all', label: '全部' },
  { st: 'ai', label: 'AI' },
  { st: 'clarify', label: '待澄清' },
  { st: 'resolved', label: '已解决' },
]

const stMap: Record<string, string> = {
  ai: 'AI 处理中',
  clarify: '待澄清',
  transfer: '待转人工',
  resolved: '已解决',
  closed: '已关闭',
  done: '已解决',
}

onMounted(() => {
  if (CATALOG_SUPPORTED) {
    sessions.refresh()
  } else {
    // 案例二、三公网构建：无目录接口，直接进入新会话开始核心对话
    sessions.newChat()
  }
})

function select(c: ConversationItem) {
  sessions.selectConv(c)
}

function onNew() {
  sessions.newChat()
  chat.clearMessages()
}

function onDone() {
  chat.clearMessages()
  sessions.current = null
}

function onTransfer() {
  ElMessage.info(
    '阶段一 Single-Agent 仅做 AI 自动应答；转人工分支在阶段三 Workflow（expected_branch=human_transfer）接入。',
  )
}

function send() {
  const q = chat.draft.trim()
  if (!q) return
  chat.sendQuery(q, app.chatMode)
}

function onKey(e: Event | KeyboardEvent) {
  if (!(e instanceof KeyboardEvent)) return
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function quick(q: string) {
  chat.sendQuery(q, app.chatMode)
}

function priLabel(p?: string) {
  if (p === 'urgent') return '紧急'
  if (p === 'high') return '高优'
  return ''
}
</script>

<template>
  <div class="workbench">
    <aside v-if="showCatalog" class="cx-list">
      <div class="cx-toolbar">
        <el-input
          v-model="sessions.searchKw"
          size="small"
          clearable
          placeholder="搜索用户 / 订单号"
        />
        <el-button type="primary" size="small" @click="onNew">＋ 新会话</el-button>
      </div>
      <div class="cx-status-tabs">
        <button
          v-for="t in statusTabs"
          :key="t.st"
          type="button"
          :class="{ active: sessions.statusFilter === t.st }"
          @click="sessions.statusFilter = t.st"
        >
          {{ t.label }}
        </button>
      </div>
      <div class="cx-scroll">
        <div
          v-for="c in sessions.visible"
          :key="c.id"
          class="cx-item"
          :class="{ active: sessions.current?.id === c.id }"
          @click="select(c)"
        >
          <div class="row1">
            <span class="person">{{ c.person }}</span>
            <span v-if="c.unread" class="tag">{{ c.unread }} 未读</span>
            <span class="time">{{ c.time }}</span>
          </div>
          <div class="preview">{{ c.preview }}</div>
          <div class="badges">
            <span class="tag" :class="'b-' + c.status">{{ stMap[c.status] || c.status }}</span>
            <span v-if="priLabel(c.priority)" class="tag" :class="'pr-' + c.priority">
              {{ priLabel(c.priority) }}
            </span>
            <DemoBadge v-if="c.source === 'tickets'" text="数据集" />
            <DemoBadge v-else-if="c.source === 'run'" text="本次运行" />
          </div>
        </div>
      </div>
    </aside>

    <section class="cx-chat">
      <div class="chat-top">
        <span class="title">{{ sessions.current?.person || '请选择或新建一个会话' }}</span>
        <span class="st">{{ sessions.current ? '会话 ' + sessions.current.id : '' }}</span>
        <div class="ops">
          <el-button size="small" @click="onTransfer">转人工</el-button>
          <el-button size="small" type="success" plain @click="onDone">完成</el-button>
        </div>
      </div>
      <div ref="msgBox" class="msg-scroll">
        <MessageList :messages="chat.messages" />
      </div>
      <Composer
        :draft="chat.draft"
        :running="chat.running"
        :mode="app.chatMode"
        :quick="sessions.quick"
        @update:draft="chat.draft = $event"
        @update:mode="app.chatMode = $event"
        @send="send"
        @quick="quick"
        @keydown="onKey"
      />
    </section>

    <CustomerPanel
      v-if="showCatalog"
      :customer="chat.sideCustomer"
      :steps="chat.steps"
      :metrics="chat.sideMetrics"
    />
  </div>
</template>

<style scoped>
.workbench {
  display: flex;
  height: 100%;
  width: 100%;
}
.cx-list {
  width: 300px;
  min-width: 300px;
  background: var(--panel);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
}
.cx-toolbar {
  display: flex;
  gap: 8px;
  padding: 12px 12px 8px;
  align-items: center;
}
.cx-status-tabs {
  display: flex;
  gap: 2px;
  padding: 0 12px 8px;
  border-bottom: 1px solid var(--line);
}
.cx-status-tabs button {
  flex: 1;
  border: 0;
  background: transparent;
  color: var(--ink-2);
  padding: 6px 0;
  border-radius: 6px;
  font-size: 12.5px;
  cursor: pointer;
}
.cx-status-tabs button.active {
  background: var(--chip);
  color: var(--brand-dk);
  font-weight: 600;
}
.cx-scroll {
  flex: 1;
  overflow-y: auto;
}
.cx-item {
  padding: 10px 14px;
  border-bottom: 1px solid #f1f5f9;
  cursor: pointer;
}
.cx-item:hover {
  background: #f8fafc;
}
.cx-item.active {
  background: var(--chip);
  box-shadow: inset 3px 0 0 var(--brand);
}
.row1 {
  display: flex;
  align-items: center;
  gap: 6px;
}
.person {
  font-weight: 600;
  font-size: 13px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.time {
  font-size: 11px;
  color: var(--ink-2);
}
.preview {
  font-size: 12px;
  color: var(--ink-2);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.badges {
  margin-top: 4px;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  align-items: center;
}
.tag {
  font-size: 10.5px;
  padding: 1px 7px;
  border-radius: 20px;
  background: #f1f5f9;
  color: var(--ink-2);
}
.tag.pr-urgent {
  background: #fef3c7;
  color: var(--warn);
}
.tag.pr-high {
  background: #fee2e2;
  color: var(--bad);
}
.tag.b-ai {
  background: #e0e7ff;
  color: #3730a3;
}
.tag.b-clarify {
  background: #ffedd5;
  color: #c2410c;
}
.tag.b-resolved,
.tag.b-done,
.tag.b-closed {
  background: #d1fae5;
  color: #047857;
}
.cx-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: #fff;
}
.chat-top {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
}
.chat-top .title {
  font-weight: 700;
  font-size: 14px;
}
.chat-top .st {
  font-size: 12px;
  color: var(--ink-2);
  flex: 1;
}
.ops {
  display: flex;
  gap: 6px;
}
.msg-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
</style>
