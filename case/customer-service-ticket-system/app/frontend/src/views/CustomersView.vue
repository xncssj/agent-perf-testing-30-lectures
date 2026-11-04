<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchCustomers } from '@/api/catalog'
import { useAppStore } from '@/stores/app'
import { useChatStore } from '@/stores/chat'
import { useSessionsStore } from '@/stores/sessions'
import type { ConversationItem } from '@/types'

interface CustomerRow {
  ui: string
  tid: string
  order_id: string
  d: string
  q: string
  branch: string
}

const router = useRouter()
const app = useAppStore()
const chat = useChatStore()
const sessions = useSessionsStore()
const rows = ref<CustomerRow[]>([])
const source = ref('')
const error = ref('')

// 服务端分页与搜索
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const loading = ref(false)

async function load(p = page.value) {
  loading.value = true
  try {
    const data = await fetchCustomers({
      page: p,
      pageSize: pageSize.value,
      q: keyword.value.trim(),
    })
    rows.value = data.items
    total.value = data.total
    source.value = data.source
    page.value = data.page
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '客户库加载失败'
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  void load(1)
}

function onPageChange(p: number) {
  void load(p)
}

onMounted(() => {
  void load()
})

function startChat(row: CustomerRow) {
  const conv: ConversationItem = {
    id: row.tid,
    person: row.ui,
    order: row.order_id,
    time: '',
    preview: row.q,
    status: 'ai',
    source: 'tickets',
  }
  sessions.selectConv(conv)
  chat.clearMessages()
  router.push('/')
  setTimeout(() => chat.sendQuery(row.q, app.chatMode), 50)
}
</script>

<template>
  <div class="customers">
    <h2>客户库</h2>
    <p class="text-sm">来源 {{ source || 'tickets.json' }}。点击「对话」带着工单 ID 进入工作台并触发真实 Agent。</p>
    <p v-if="error" class="text-sm">{{ error }}</p>
    <div class="toolbar">
      <el-input
        v-model="keyword"
        class="search-input"
        clearable
        placeholder="搜索用户 / 工单 ID / 订单号 / 提问 / 难度 / 分支"
        @keydown.enter="search"
        @clear="search"
      />
      <el-button type="primary" :loading="loading" @click="search">搜索</el-button>
    </div>
    <el-table v-loading="loading" :data="rows" stripe style="width: 100%; margin-top: 12px">
      <el-table-column prop="ui" label="用户" width="120" />
      <el-table-column prop="order_id" label="订单号" width="160">
        <template #default="{ row }">
          <span class="mono">{{ row.order_id || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="tid" label="工单 ID" width="100" />
      <el-table-column prop="q" label="提问" />
      <el-table-column prop="d" label="难度" width="90" />
      <el-table-column prop="branch" label="分支" width="130" />
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button type="primary" link @click="startChat(row as CustomerRow)">对话 →</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-button v-if="keyword.trim()" link @click="keyword = ''; search()">无匹配结果，清空搜索</el-button>
      </template>
    </el-table>
    <div class="pager">
      <el-pagination
        :current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="onPageChange"
      />
    </div>
  </div>
</template>

<style scoped>
.customers {
  height: 100%;
  overflow-y: auto;
  padding: 16px 20px;
}
h2 {
  margin: 0 0 6px;
  font-size: 16px;
}
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 12px;
}
.search-input {
  width: 340px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
