<script setup lang="ts">
defineProps<{
  label: string
  value: string
  sub?: string
  cls?: string
}>()
</script>

<template>
  <div class="kpi">
    <div class="lbl">{{ label }}</div>
    <div class="val" :class="cls">{{ value }}</div>
    <div v-if="sub" class="sub">{{ sub }}</div>
  </div>
</template>

<style scoped>
.kpi {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 14px 16px;
  min-width: 140px;
  flex: 1;
}
.lbl {
  font-size: 12px;
  color: var(--ink-2);
}
.val {
  font-size: 22px;
  font-weight: 700;
  margin: 4px 0 2px;
  font-variant-numeric: tabular-nums;
}
.sub {
  font-size: 11px;
  color: var(--ink-2);
}
</style>
