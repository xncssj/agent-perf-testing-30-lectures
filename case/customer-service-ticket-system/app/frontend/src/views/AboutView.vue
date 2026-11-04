<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchAnalytics } from '@/api/catalog'
import { docsUrl, fetchVersion } from '@/api/chat'

const health = ref('—')
const version = ref('—')
const fingerprint = ref('—')
const docs = ref(docsUrl())
const slo = ref<string[][]>([])
const sloSource = ref('')
const tools = ref<{ tool: string; uri: string; service: string }[]>([])
const error = ref('')

onMounted(async () => {
  try {
    const j = await fetchVersion()
    health.value = 'ok · ' + String(j.name || '')
    version.value = String(j.version || '—')
    fingerprint.value = String(j.fingerprint || '—').slice(0, 40)
    docs.value = docsUrl()
  } catch {
    health.value = '未连接'
  }
  try {
    const data = await fetchAnalytics()
    slo.value = data.slo
    sloSource.value = data.slo_source
    tools.value = data.tools
  } catch (e) {
    error.value = e instanceof Error ? e.message : '口径加载失败'
  }
})
</script>

<template>
  <div class="about">
    <div class="pb">
      <div class="pb-head">后端指纹</div>
      <div class="pb-body">
        <dl class="kv">
          <dt>服务</dt><dd>{{ health }}</dd>
          <dt>版本</dt><dd>{{ version }}</dd>
          <dt>指纹</dt><dd class="mono">{{ fingerprint }}</dd>
          <dt>文档</dt><dd><a :href="docs" target="_blank" rel="noreferrer">/docs</a></dd>
        </dl>
      </div>
    </div>

    <p v-if="error" class="text-sm">{{ error }}</p>
    <div class="pb">
      <div class="pb-head">工具 → Java 微服务映射</div>
      <div class="pb-body">
        <dl class="kv tools">
          <template v-for="(r, i) in tools" :key="i">
            <dt class="mono">{{ r.tool }}</dt>
            <dd>
              <code class="mono">{{ r.uri }}</code><br />
              <span class="text-sm">{{ r.service }}</span>
            </dd>
          </template>
        </dl>
      </div>
    </div>

    <div class="pb">
      <div class="pb-head">SLO 分档表 <span class="src">{{ sloSource }}</span></div>
      <div class="pb-body">
        <table v-if="slo.length" class="metrics-table">
          <thead>
            <tr>
              <th v-for="h in slo[0]" :key="h">{{ h }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in slo.slice(1)" :key="i">
              <td v-for="(c, j) in row" :key="j">{{ c }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.about {
  height: 100%;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pb {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  overflow: hidden;
}
.pb-head {
  padding: 10px 14px;
  font-weight: 600;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 8px;
}
.src {
  margin-left: auto;
  font-size: 11px;
  color: var(--ink-2);
  font-weight: 400;
}
.pb-body {
  padding: 12px 14px;
}
.kv {
  display: grid;
  grid-template-columns: 100px 1fr;
  gap: 8px 12px;
  margin: 0;
  font-size: 13px;
}
.kv dt {
  color: var(--ink-2);
}
.kv dd {
  margin: 0;
}
.kv.tools {
  grid-template-columns: 160px 1fr;
}
code {
  font-size: 12px;
  background: #f1f5f9;
  padding: 1px 6px;
  border-radius: 4px;
}
.metrics-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.metrics-table th,
.metrics-table td {
  border-bottom: 1px solid var(--line);
  padding: 6px 8px;
  text-align: left;
}
a {
  color: var(--brand);
}
</style>
