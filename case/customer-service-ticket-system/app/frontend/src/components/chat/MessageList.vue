<script setup lang="ts">
import ProcessTimeline from '@/components/chat/ProcessTimeline.vue'
import type { ChatMessage } from '@/types'

defineProps<{
  messages: ChatMessage[]
}>()
</script>

<template>
  <div class="cx-messages">
    <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
      <template v-if="m.role === 'user'">
        <div class="bubble">{{ m.text }}</div>
        <div class="meta">{{ m.time }} · 用户</div>
      </template>
      <template v-else>
        <div class="ab-box">
          <div class="ab-kind">
            <span class="ttl">AI 客服助手</span>
            <span class="chip2" :class="m.statusCls">{{ m.statusLabel }}</span>
            <span v-if="m.intent" class="chip2 intent">意图 · {{ m.intent }}</span>
          </div>
          <div class="ab-body">
            <ProcessTimeline
              :items="m.timeline || []"
              :open="!!m.timelineOpen"
              @update:open="m.timelineOpen = $event"
            />
            <div v-if="m.answer" class="ab-answer" :class="{ cursor: m.streaming }">
              {{ m.answer }}
            </div>
            <div v-if="m.tail?.length" class="ab-tail">
              <span v-for="(t, i) in m.tail" :key="i" class="text-card">
                {{ t.label }} <b>{{ t.value }}</b>
              </span>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.cx-messages {
  flex: 1;
  padding: 16px 18px;
  background: var(--bg);
}
.msg {
  margin-bottom: 14px;
}
.msg.user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.bubble {
  max-width: 78%;
  background: var(--brand);
  color: #fff;
  padding: 10px 14px;
  border-radius: 14px 14px 4px 14px;
  font-size: 13.5px;
  line-height: 1.5;
  white-space: pre-wrap;
}
.meta {
  font-size: 11px;
  color: var(--ink-2);
  margin-top: 4px;
}
.ab-box {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  overflow: hidden;
  max-width: 92%;
}
.ab-kind {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f8fafc;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.ttl {
  font-weight: 600;
  font-size: 13px;
}
.chip2 {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 20px;
  background: #e2e8f0;
}
.chip2.intent {
  background: #eef2ff;
  color: #4338ca;
}
.ab-body {
  padding: 10px 12px 12px;
}
.ab-answer {
  font-size: 13.5px;
  line-height: 1.55;
  white-space: pre-wrap;
}
.ab-answer.cursor::after {
  content: '▍';
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
.ab-tail {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.text-card {
  font-size: 11.5px;
  background: #f1f5f9;
  padding: 4px 8px;
  border-radius: 8px;
}
</style>
