<script setup lang="ts">
defineProps<{
  text: string
}>()
</script>

<template>
  <span class="badge-demo">{{ text }}</span>
</template>

<style scoped>
.badge-demo {
  font-size: 10.5px;
  padding: 1px 7px;
  border-radius: 20px;
  background: #eef2ff;
  color: #4338ca;
}
</style>
