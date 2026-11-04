<script setup lang="ts">
import type { TimelineItem } from '@/types'

defineProps<{
  items: TimelineItem[]
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()
</script>

<template>
  <div>
    <button type="button" class="ab-proc-toggle" @click="emit('update:open', !open)">
      {{ open ? '▾' : '▸' }} 处理过程（事件日志）
    </button>
    <div v-show="open" class="tl">
      <div
        v-for="(it, i) in items"
        :key="i"
        class="tl-item"
        :class="it.kind === 'thinking' ? 'think' : it.kind === 'tool_call' ? 'tool' : 'res'"
      >
        <div class="tl-head">{{ it.title }}</div>
        <div v-if="it.uri" class="tl-uri">{{ it.uri }}</div>
        <div v-if="it.sub" class="tl-sub">{{ it.sub }}</div>
        <div v-if="it.data" class="tl-data">{{ it.data }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ab-proc-toggle {
  border: 0;
  background: transparent;
  color: var(--ink-2);
  font-size: 12px;
  padding: 0;
  cursor: pointer;
  margin-bottom: 6px;
}
.tl {
  margin: 4px 0 10px;
  padding-left: 8px;
  border-left: 2px solid var(--line);
}
.tl-item {
  margin-bottom: 8px;
  font-size: 12px;
}
.tl-head {
  font-weight: 600;
}
.tl-uri,
.tl-sub {
  color: var(--ink-2);
  font-family: var(--mono);
  font-size: 11px;
}
.tl-data {
  white-space: pre-wrap;
  font-family: var(--mono);
  font-size: 11px;
  background: #f8fafc;
  padding: 6px 8px;
  border-radius: 6px;
  margin-top: 4px;
}
.tl-item.think .tl-head {
  color: #64748b;
}
.tl-item.tool .tl-head {
  color: #1d4ed8;
}
.tl-item.res .tl-head {
  color: #047857;
}
</style>
