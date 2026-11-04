<script setup lang="ts">
defineProps<{
  draft: string
  running: boolean
  mode: 'stream' | 'complete'
  quick: string[]
}>()

const emit = defineEmits<{
  'update:draft': [value: string]
  'update:mode': [value: 'stream' | 'complete']
  send: []
  quick: [q: string]
  keydown: [e: Event | KeyboardEvent]
}>()
</script>

<template>
  <div class="composer">
    <div class="quick">
      <el-button
        v-for="q in quick"
        :key="q"
        size="small"
        text
        @click="emit('quick', q)"
      >
        {{ q }}
      </el-button>
    </div>
    <div class="row">
      <el-input
        :model-value="draft"
        type="textarea"
        :rows="2"
        resize="none"
        placeholder="输入提问，例如：帮我查一下订单 2024010100001 的物流状态"
        :disabled="running"
        @update:model-value="emit('update:draft', String($event))"
        @keydown="emit('keydown', $event)"
      />
      <el-button type="primary" :loading="running" @click="emit('send')">发送</el-button>
    </div>
    <div class="mode-row">
      <el-radio-group
        :model-value="mode"
        size="small"
        @update:model-value="emit('update:mode', $event as 'stream' | 'complete')"
      >
        <el-radio-button value="stream">流式 SSE</el-radio-button>
        <el-radio-button value="complete">完整响应</el-radio-button>
      </el-radio-group>
    </div>
  </div>
</template>

<style scoped>
.composer {
  border-top: 1px solid var(--line);
  padding: 10px 14px 12px;
  background: #fff;
}
.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}
.row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.mode-row {
  margin-top: 8px;
}
</style>
