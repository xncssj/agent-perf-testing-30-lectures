<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import TicketCard from '@/components/board/TicketCard.vue'
import TicketDetailDrawer from '@/components/board/TicketDetailDrawer.vue'
import { BOARD_COLS } from '@/demo/demo'
import { useSessionsStore } from '@/stores/sessions'
import type { BoardTicket } from '@/types'

const sessions = useSessionsStore()
const detail = ref<BoardTicket | null>(null)

// 搜索与筛选
const keyword = ref('')
const difficulty = ref('')
const priority = ref('')

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const df = difficulty.value
  const pr = priority.value
  return sessions.boardTickets.filter((b) => {
    if (df && b.difficulty !== df) return false
    if (pr && b.pri !== pr) return false
    if (!kw) return true
    const hay = [b.id, b.q, b.user_id, b.order_id, b.sla, b.difficulty].join(' ').toLowerCase()
    return hay.includes(kw)
  })
})

const columns = computed(() =>
  BOARD_COLS.map((c) => ({
    ...c,
    cards: filtered.value.filter((b) => b.st === c.k),
  })),
)

const hitCount = computed(() => filtered.value.length)

function applySearch() {
  keyword.value = keyword.value.trim()
}

function resetFilter() {
  keyword.value = ''
  difficulty.value = ''
  priority.value = ''
}

function sync() {
  void sessions.refresh()
}

onMounted(() => {
  void sessions.refresh()
})

function colTitle(st: string) {
  return BOARD_COLS.find((c) => c.k === st)?.t || st
}
</script>

<template>
  <div class="board-view">
    <div class="toolbar">
      <h2>工单负载预览 <span class="text-sm">{{ sessions.source || 'tickets.json' }}</span></h2>
      <el-button size="small" @click="sync">同步已办会话</el-button>
    </div>
    <p class="text-sm meta">
      仅为压测负载分布预览：列状态来自数据集 expected_branch 映射（业务状态），不等同于第 17 讲的业务级指标与 Goodput。
    </p>
    <el-alert
      v-if="sessions.ticketsLoaded === false"
      type="warning"
      :closable="false"
      class="missing-alert"
    >
      工单数据缺失：后端未读到 tickets.json，看板为空。数据档切换后档目录须补齐该文件，
      或检查 AGENT_TEST_DATA_DIR 指向。查找位置：{{ sessions.ticketsDir }}
    </el-alert>
    <div class="filter-bar">
      <el-input
        v-model="keyword"
        class="kw-input"
        clearable
        size="small"
        placeholder="搜索工单 ID / 提问 / 用户 / 订单号 / 分支"
      />
      <el-select v-model="difficulty" size="small" class="sel" placeholder="难度" clearable>
        <el-option label="简单" value="easy" />
        <el-option label="中等" value="medium" />
        <el-option label="困难" value="hard" />
      </el-select>
      <el-select v-model="priority" size="small" class="sel" placeholder="优先级" clearable>
        <el-option label="普通" value="normal" />
        <el-option label="高优" value="high" />
        <el-option label="紧急" value="urgent" />
      </el-select>
      <el-button type="primary" size="small" class="search-btn" @click="applySearch">搜索</el-button>
      <span class="hit" v-if="hitCount < sessions.boardTickets.length">命中 {{ hitCount }} / {{ sessions.boardTickets.length }}</span>
      <el-button size="small" :disabled="keyword === '' && !difficulty && !priority" @click="resetFilter">
        重置
      </el-button>
    </div>
    <p v-if="sessions.error" class="text-sm">{{ sessions.error }}</p>
    <div class="board-col-wrap">
      <div v-for="col in columns" :key="col.k" class="board-col">
        <div class="col-head" :style="{ color: col.color }">
          <span>●</span> {{ col.t }} <span class="n">{{ col.cards.length }}</span>
        </div>
        <div class="col-body">
          <TicketCard
            v-for="tk in col.cards"
            :key="tk.id"
            :ticket="tk"
            @select="detail = $event"
          />
          <p v-if="!col.cards.length" class="text-sm empty">空</p>
        </div>
      </div>
    </div>

    <TicketDetailDrawer
      :ticket="detail"
      :col-title="detail ? colTitle(detail.st) : ''"
      @close="detail = null"
    />
  </div>
</template>

<style scoped>
.board-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 12px 16px;
  overflow: hidden;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.toolbar h2 {
  font-size: 16px;
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.meta {
  margin: -2px 0 10px;
  color: var(--ink-2);
}
.missing-alert {
  margin-bottom: 10px;
}
.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.kw-input {
  width: 300px;
}
.sel {
  width: 110px;
}
.search-btn {
  margin-left: 4px;
}
.hit {
  font-size: 12px;
  color: var(--ink-2);
}
.board-col-wrap {
  flex: 1;
  display: flex;
  gap: 12px;
  overflow-x: auto;
  min-height: 0;
}
.board-col {
  width: 220px;
  min-width: 220px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  max-height: 100%;
}
.col-head {
  padding: 10px 12px;
  font-weight: 600;
  font-size: 13px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 6px;
}
.col-head .n {
  margin-left: auto;
  color: var(--ink-2);
  font-weight: 500;
}
.col-body {
  padding: 8px;
  overflow-y: auto;
  flex: 1;
}
.empty {
  padding: 8px;
}
</style>
