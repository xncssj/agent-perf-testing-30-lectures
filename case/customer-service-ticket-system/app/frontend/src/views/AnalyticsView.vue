<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import KpiCard from '@/components/common/KpiCard.vue'
import SloTable from '@/components/common/SloTable.vue'
import { fetchAnalytics, type AnalyticsPayload } from '@/api/catalog'
import { fetchMetricsText } from '@/api/chat'
import { nowTime, parseProm } from '@/api/http'
import type { PromParsed } from '@/types'

const summary = ref<AnalyticsPayload | null>(null)
const summaryError = ref('')
const real = ref<PromParsed | null>(null)
const realError = ref('')
const srcLabel = ref('来自 /metrics')
const selPath = ref('')
let timer: number | undefined

const curHist = computed(() => (selPath.value ? real.value?.hist.byPath[selPath.value] : undefined))

function defaultHistPath(paths: string[]) {
  const prefer = ['/api/v1/chat/stream', '/api/v1/chat/complete', '/api/v1/chat']
  return prefer.find((p) => paths.includes(p)) || paths[0] || ''
}

const e2eMax = computed(() => {
  const vals = (summary.value?.e2e || []).map((x) => x.v || 0)
  return Math.max(0.001, ...vals)
})

async function loadSummary() {
  try {
    summary.value = await fetchAnalytics()
    summaryError.value = ''
  } catch (e) {
    summary.value = null
    summaryError.value = e instanceof Error ? e.message : '分析接口不可用'
  }
}

async function loadMetrics() {
  try {
    const text = await fetchMetricsText()
    const parsed = parseProm(text)
    real.value = parsed
    const next = defaultHistPath(parsed.hist.paths)
    if (!parsed.hist.paths.includes(selPath.value)) selPath.value = next
    realError.value = ''
    srcLabel.value = '来自 /metrics · ' + nowTime()
  } catch {
    real.value = null
    realError.value = '后端未连接，无法获取实时指标。'
  }
}

onMounted(() => {
  void loadSummary()
  void loadMetrics()
  timer = window.setInterval(() => {
    void loadSummary()
    void loadMetrics()
  }, 8000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

function counterRows(counters: Record<string, number>) {
  return Object.keys(counters)
    .sort()
    .map((k) => {
      const parts = k.split(',').map((x) => x.split('=')[1]?.replace(/"/g, '') || '')
      return { st: parts[0], sr: parts[1], n: counters[k] }
    })
}
</script>

<template>
  <div class="analytics">
    <p v-if="summaryError" class="text-sm">{{ summaryError }}</p>
    <div class="kpi-wrap">
      <KpiCard
        v-for="k in summary?.kpis || []"
        :key="k.lbl"
        :label="k.lbl"
        :value="k.val"
        :sub="k.sub"
        :cls="k.cls"
      />
    </div>
    <div class="grid">
      <div class="pb">
        <div class="pb-head">
          SLO 目标对照
          <span class="src">{{ summary?.slo_source || '课程口径' }}</span>
        </div>
        <div class="pb-body">
          <SloTable :rows="summary?.slo || []" empty-text="暂无 SLO 口径。" />
        </div>
      </div>
      <div class="pb">
        <div class="pb-head">
          E2E 分布
          <span class="src">样本 {{ summary?.sample_count ?? 0 }}</span>
        </div>
        <div class="pb-body">
          <div v-for="x in summary?.e2e || []" :key="x.k" class="bar-row">
            <span>{{ x.k }}</span>
            <div class="bar-track">
              <div
                class="bar-fill"
                :style="{ width: x.v == null ? '0%' : (x.v / e2eMax) * 100 + '%' }"
              />
            </div>
            <span>{{ x.v == null ? '—' : x.v + 's' }}</span>
          </div>
          <p class="text-sm" style="margin-top: 8px">
            分位数来自本次进程已记录的运行样本；没有样本时显示「—」。Prometheus 直方图见下方。
          </p>
        </div>
      </div>
      <div class="pb wide">
        <div class="pb-head">实时终态计数 <span class="src">{{ srcLabel }}</span></div>
        <div class="pb-body">
          <p v-if="realError" class="text-sm">{{ realError }}</p>
          <template v-else-if="real">
            <table class="metrics-table">
              <thead>
                <tr>
                  <th>终态</th>
                  <th>stop_reason</th>
                  <th>次数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!Object.keys(real.counters).length">
                  <td colspan="3" class="text-sm">暂无运行样本</td>
                </tr>
                <tr v-for="r in counterRows(real.counters)" :key="r.st + r.sr">
                  <td><el-tag size="small">{{ r.st }}</el-tag></td>
                  <td class="mono">{{ r.sr }}</td>
                  <td>{{ r.n }}</td>
                </tr>
              </tbody>
            </table>
            <div style="display: flex; align-items: center; gap: 8px; margin: 12px 0 4px">
              <span style="font-weight: 600; font-size: 12px">agent_e2e_latency_seconds</span>
              <el-select
                v-if="real?.hist.paths.length"
                v-model="selPath"
                size="small"
                style="width: 220px"
              >
                <el-option v-for="p in real.hist.paths" :key="p" :label="p" :value="p" />
              </el-select>
            </div>
            <template v-if="curHist">
              <div class="text-sm" style="margin-bottom: 6px">
                计数 {{ curHist.count }} · 累计 {{ curHist.sum.toFixed(2) }}s · 均值
                {{ curHist.count ? (curHist.sum / curHist.count).toFixed(3) : '—' }}s
              </div>
              <div v-for="b in curHist.buckets" :key="b.le" class="bar-row">
                <span>≤ {{ b.le }}s</span>
                <div class="bar-track">
                  <div
                    class="bar-fill"
                    :style="{
                      width: Math.min(100, (b.n / (curHist.count || 1)) * 100) + '%',
                    }"
                  />
                </div>
                <span>{{ b.n }}</span>
              </div>
            </template>
            <p v-else class="text-sm">暂无该路径直方图样本。</p>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.analytics {
  height: 100%;
  overflow-y: auto;
  padding: 16px;
}
.kpi-wrap {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.pb {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  overflow: hidden;
}
.pb.wide {
  grid-column: 1 / -1;
}
.pb-head {
  padding: 10px 14px;
  font-weight: 600;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 8px;
}
.pb-head .src {
  margin-left: auto;
  font-size: 11px;
  color: var(--ink-2);
  font-weight: 400;
}
.pb-body {
  padding: 12px 14px;
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
.bar-row {
  display: grid;
  grid-template-columns: 64px 1fr 48px;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  margin-bottom: 6px;
}
.bar-track {
  height: 8px;
  background: #f1f5f9;
  border-radius: 4px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: var(--brand);
  border-radius: 4px;
}
</style>
