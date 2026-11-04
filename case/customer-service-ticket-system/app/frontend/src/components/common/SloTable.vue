<script setup lang="ts">
defineProps<{
  rows: string[][]
  emptyText?: string
}>()
</script>

<template>
  <table v-if="rows.length" class="metrics-table">
    <thead>
      <tr>
        <th v-for="h in rows[0]" :key="h">{{ h }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, i) in rows.slice(1)" :key="i">
        <td v-for="(c, j) in row" :key="j">{{ c }}</td>
      </tr>
    </tbody>
  </table>
  <p v-else class="text-sm">{{ emptyText || '暂无数据' }}</p>
</template>

<style scoped>
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
.metrics-table th {
  color: var(--ink-2);
  font-weight: 600;
}
</style>
