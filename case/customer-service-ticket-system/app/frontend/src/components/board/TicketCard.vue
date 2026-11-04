<script setup lang="ts">
import type { BoardTicket } from '@/types'

defineProps<{
  ticket: BoardTicket
}>()

const emit = defineEmits<{
  select: [ticket: BoardTicket]
}>()

function priLabel(pri: string) {
  if (pri === 'urgent') return '紧急'
  if (pri === 'high') return '高优'
  return '普通'
}
</script>

<template>
  <div class="tk-card" @click="emit('select', ticket)">
    <div class="id">{{ ticket.id }}</div>
    <div class="q">{{ ticket.q }}</div>
    <div class="row">
      <span class="tag" :class="'pr-' + ticket.pri">{{ priLabel(ticket.pri) }}</span>
      <span class="tag">{{ ticket.src }}</span>
    </div>
  </div>
</template>

<style scoped>
.tk-card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px;
  margin-bottom: 8px;
  cursor: pointer;
}
.tk-card:hover {
  border-color: var(--brand);
}
.tk-card .id {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--ink-2);
}
.tk-card .q {
  font-size: 13px;
  margin: 4px 0 8px;
}
.tk-card .row {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
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
</style>
