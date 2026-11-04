<script setup lang="ts">
import { computed } from 'vue'
import type { BoardTicket } from '@/types'

const props = defineProps<{
  ticket: BoardTicket | null
  colTitle: string
}>()

const emit = defineEmits<{
  close: []
}>()

const open = computed({
  get: () => !!props.ticket,
  set: (v: boolean) => {
    if (!v) emit('close')
  },
})

const PRI_TEXT: Record<string, string> = {
  urgent: '紧急',
  high: '高优',
  normal: '普通',
}

const BRANCH_TEXT: Record<string, string> = {
  auto_reply: '自动应答',
  clarification: '待澄清',
  human_transfer: '人工转接',
}

function priText(p?: string) {
  return p ? (PRI_TEXT[p] || p) : '—'
}

function branchText(s?: string) {
  return s ? (BRANCH_TEXT[s] || s) : '—'
}
</script>

<template>
  <el-drawer v-model="open" :with-header="false" size="360px">
    <template v-if="ticket">
      <h3>{{ ticket.id }}</h3>
      <dl class="kv">
        <dt>提问</dt>
        <dd>{{ ticket.q }}</dd>
        <dt>用户</dt>
        <dd>{{ ticket.user_id || '—' }}</dd>
        <dt>订单号</dt>
        <dd class="mono">{{ ticket.order_id || '—' }}</dd>
        <dt>优先级</dt>
        <dd>{{ priText(ticket.pri) }}</dd>
        <dt>难度</dt>
        <dd>{{ ticket.difficulty || '—' }}</dd>
        <dt>来源</dt>
        <dd>{{ ticket.src }}</dd>
        <dt>期望分支</dt>
        <dd>{{ branchText(ticket.sla) }}</dd>
        <dt>状态列</dt>
        <dd>{{ colTitle }}</dd>
      </dl>
      <el-button style="margin-top: 12px" @click="emit('close')">关闭</el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.kv {
  display: grid;
  grid-template-columns: 74px 1fr;
  gap: 8px 10px;
  font-size: 13px;
}
.kv dt {
  color: var(--ink-2);
}
.kv dd {
  margin: 0;
}
</style>
