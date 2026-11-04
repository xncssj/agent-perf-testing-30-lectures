<script setup lang="ts">
import { computed } from 'vue'
import { UNIFIED_STEPS } from '@/demo/demo'
import type { CustomerPanelModel } from '@/types'

const props = defineProps<{
  customer: CustomerPanelModel
  steps: Record<string, string>
  metrics: {
    status: string
    stop: string
    e2e: string
    first: string
    token: string
    usageSrc: string
    evidence: string
  }
}>()

const orderRows = computed(() => {
  const c = props.customer
  const rows: { k: string; v: string }[] = [
    { k: '用户', v: c.user },
    { k: '最近订单', v: c.order },
    { k: '状态', v: c.orderStatus },
  ]
  if (c.order !== '—' && c.order) {
    rows.push({ k: '金额', v: c.amount })
    rows.push({ k: '收货地址', v: c.address })
  }
  rows.push({ k: '承运商', v: c.carrier })
  rows.push({ k: '物流', v: c.logistics })
  return rows
})
</script>

<template>
  <aside class="cx-side">
    <div class="panel-title">客户与订单</div>
    <dl class="kv">
      <template v-for="r in orderRows" :key="r.k">
        <dt>{{ r.k }}</dt>
        <dd>{{ r.v }}</dd>
      </template>
    </dl>
    <div class="trace-note">
      来源：<code>{{ customer.runId || '—' }}</code>
      <span v-if="customer.tool">· {{ customer.tool }}</span>
      <span v-if="customer.ts">· {{ customer.ts }}</span>
    </div>
    <div class="panel-title">处理流程</div>
    <div class="stepflow">
      <div v-for="s in UNIFIED_STEPS" :key="s.k" class="step" :class="steps[s.k]">
        <span class="n">{{ s.k }}</span>{{ s.t }}
      </div>
    </div>
    <div class="panel-title">最近运行指标</div>
    <dl class="kv">
      <dt>终态</dt>
      <dd>{{ metrics.status }}</dd>
      <dt>停止原因</dt>
      <dd>{{ metrics.stop }}</dd>
      <dt>E2E</dt>
      <dd>{{ metrics.e2e }}</dd>
      <dt>首片段</dt>
      <dd>{{ metrics.first }}</dd>
      <dt>Token</dt>
      <dd>{{ metrics.token }}</dd>
      <dt>用量来源</dt>
      <dd>{{ metrics.usageSrc }}</dd>
      <dt>证据类型</dt>
      <dd>{{ metrics.evidence }}</dd>
    </dl>
  </aside>
</template>

<style scoped>
.cx-side {
  width: 280px;
  min-width: 280px;
  background: var(--panel);
  border-left: 1px solid var(--line);
  padding: 12px 14px;
  overflow-y: auto;
}
.panel-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-2);
  margin: 12px 0 8px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.panel-title:first-child {
  margin-top: 0;
}
.kv {
  display: grid;
  grid-template-columns: 74px 1fr;
  gap: 6px 8px;
  font-size: 12.5px;
}
.kv dt {
  color: var(--ink-2);
}
.kv dd {
  margin: 0;
  word-break: break-all;
}
.trace-note {
  margin-top: 8px;
  font-size: 11px;
  color: var(--ink-2);
  word-break: break-all;
}
.trace-note code {
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 4px;
  background: #f1f5f9;
  border: 1px solid var(--line);
}
.stepflow {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.step {
  font-size: 12px;
  padding: 6px 8px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid var(--line);
  color: var(--ink-2);
}
.step .n {
  display: inline-block;
  width: 18px;
  font-weight: 700;
  margin-right: 4px;
}
.step.done {
  background: #ecfdf5;
  border-color: #a7f3d0;
  color: #047857;
}
.step.active {
  background: #eef2ff;
  border-color: #c7d2fe;
  color: #3730a3;
}
</style>