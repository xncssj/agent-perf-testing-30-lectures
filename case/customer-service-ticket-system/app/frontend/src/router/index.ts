import { createRouter, createWebHistory } from 'vue-router'
import { IS_GATEWAY, STAGE, ensureGatewaySession, redirectToGatewayLogin } from '@/api/gatewayAuth'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      children: [
        { path: '', name: 'workbench', component: () => import('@/views/WorkbenchView.vue') },
        { path: 'board', name: 'board', component: () => import('@/views/BoardView.vue') },
        { path: 'analytics', name: 'analytics', component: () => import('@/views/AnalyticsView.vue') },
        { path: 'customers', name: 'customers', component: () => import('@/views/CustomersView.vue') },
        { path: 'knowledge', name: 'knowledge', component: () => import('@/views/KnowledgeView.vue') },
        { path: 'models', name: 'models', component: () => import('@/views/ModelsView.vue') },
        { path: 'profile', name: 'profile', component: () => import('@/views/ProfileView.vue') },
        { path: 'about', name: 'about', component: () => import('@/views/AboutView.vue') },
      ],
    },
  ],
})

// 公网网关构建：账号资料改走网关（接口契约 §4），阶段内旧 /login、/profile
// 不再作为独立页面，守卫中改跳统一登录或阶段首页
const stageHome = IS_GATEWAY && STAGE ? `/${STAGE}/` : null

router.beforeEach(async (to) => {
  // 网关模式下服务端会话由统一 Nginx 鉴权兜底，前端守卫只负责体验（spec §3）
  if (IS_GATEWAY) {
    if (to.name === 'login') return { path: stageHome || '/' }
    if (to.name === 'profile') return { path: stageHome || '/' }
    const ok = await ensureGatewaySession()
    if (!ok) {
      // 整页跳转网关统一登录页（不是 SPA 内路由）
      redirectToGatewayLogin(to.fullPath)
      return false
    }
    return true
  }
  if (to.meta.public) return true
  const auth = useAuthStore()
  const ok = await auth.ensure()
  if (!ok) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})

export default router
