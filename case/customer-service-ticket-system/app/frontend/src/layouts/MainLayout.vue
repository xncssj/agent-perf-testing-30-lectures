<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { IS_GATEWAY, STAGE, ensureGatewaySession, gatewayLogout, getGatewayUsername } from '@/api/gatewayAuth'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const app = useAppStore()
const auth = useAuthStore()

const allNavItems = [
  { name: 'workbench', label: '工作台', path: '/', stages: ['single', 'multi', 'workflow'] },
  { name: 'board', label: '负载预览', path: '/board', stages: ['single'] },
  { name: 'analytics', label: '数据分析', path: '/analytics', stages: ['single'] },
  { name: 'customers', label: '客户库', path: '/customers', stages: ['single'] },
  { name: 'knowledge', label: '知识库', path: '/knowledge', stages: ['single'] },
  { name: 'models', label: '模型', path: '/models', stages: ['single'] },
  { name: 'about', label: '关于', path: '/about', stages: ['single', 'multi', 'workflow'] },
]

// 能力开关（接口契约 §4）：案例一公网保留已支持的业务页面；案例二、三只保留核心对话
const navItems = computed(() =>
  allNavItems.filter((item) => !IS_GATEWAY || item.stages.includes(STAGE || 'single')),
)

const stageLabel = computed(
  () =>
    ({ single: '阶段一 · Single-Agent', multi: '阶段二 · Multi-Agent', workflow: '阶段三 · Workflow+RAG' })[
      STAGE || ''
    ] || '',
)

const displayUser = ref('')

let timer: number | undefined

onMounted(async () => {
  app.checkHealth()
  timer = window.setInterval(() => app.checkHealth(), 15000)
  if (IS_GATEWAY) {
    await ensureGatewaySession()
    displayUser.value = getGatewayUsername()
  } else {
    await auth.ensure()
    displayUser.value = auth.user?.display_name || auth.user?.username || '管理员'
  }
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

function go(path: string) {
  router.push(path)
}

async function onLogout() {
  if (IS_GATEWAY) {
    // 退出一次使三个阶段一并失效（V09），由网关统一登录页接管
    await gatewayLogout()
    location.assign('/login')
    return
  }
  await auth.logout()
  router.replace('/login')
}
</script>

<template>
  <div class="shell">
    <header class="top">
      <div class="brand">
        客服工单系统
        <span v-if="stageLabel" class="sub">{{ stageLabel }}</span>
      </div>
      <nav>
        <button
          v-for="item in navItems"
          :key="item.name"
          type="button"
          :class="{ active: route.name === item.name }"
          @click="go(item.path)"
        >
          {{ item.label }}
        </button>
      </nav>
      <div class="conn">
        <!-- 网关模式无阶段内资料页：用户名仅展示，不可点（接口契约 §4） -->
        <button
          type="button"
          class="who"
          :disabled="IS_GATEWAY"
          @click="IS_GATEWAY ? undefined : go('/profile')"
        >
          {{ displayUser || '管理员' }}
        </button>
        <button type="button" class="who" @click="onLogout">退出</button>
        <span class="dot" :class="app.connected ? 'up' : 'down'" />
        <span>{{ app.connText }}</span>
        <span class="url">{{ app.connUrl }}</span>
      </div>
    </header>
    <main class="body">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.shell {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.top {
  height: var(--header-h);
  background: #0f172a;
  color: #e2e8f0;
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 8px;
  flex-shrink: 0;
}
.brand {
  font-weight: 700;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.brand .sub {
  font-size: 11px;
  color: #94a3b8;
  font-weight: 400;
  margin-left: 8px;
}
nav {
  display: flex;
  gap: 4px;
  margin: 0 auto;
}
nav button {
  background: transparent;
  border: 0;
  color: #cbd5e1;
  padding: 7px 14px;
  border-radius: 8px;
  font-size: 13.5px;
  cursor: pointer;
}
nav button.active {
  background: #1e293b;
  color: #fff;
  font-weight: 600;
}
nav button:hover {
  background: #1e293b;
}
.conn {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  white-space: nowrap;
}
.conn .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #64748b;
}
.conn .dot.up {
  background: #22c55e;
  box-shadow: 0 0 0 3px #22c55e33;
}
.conn .dot.down {
  background: #f43f5e;
}
.conn .url {
  color: #94a3b8;
}
.who {
  background: transparent;
  border: 0;
  color: #e2e8f0;
  cursor: pointer;
  font-size: 12px;
}
.body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
