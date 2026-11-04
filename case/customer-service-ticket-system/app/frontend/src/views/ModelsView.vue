<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useModelsStore } from '@/stores/models'
import type { ModelConfig } from '@/types'

const store = useModelsStore()
const dialog = ref(false)
const editingId = ref<string | null>(null)
const form = reactive({
  name: '',
  provider: 'openai' as ModelConfig['provider'],
  baseUrl: '',
  model: '',
  apiKey: '',
})

function openAdd() {
  editingId.value = null
  form.name = ''
  form.provider = 'openai'
  form.baseUrl = 'https://api.example.com/v1'
  form.model = ''
  form.apiKey = 'YOUR_API_KEY'
  dialog.value = true
}

function openEdit(m: ModelConfig) {
  editingId.value = m.id
  form.name = m.name
  form.provider = m.provider
  form.baseUrl = m.baseUrl
  form.model = m.model
  form.apiKey = m.apiKey
  dialog.value = true
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  try {
    if (editingId.value) {
      await store.saveModel(editingId.value, { ...form })
    } else {
      await store.addModel({ ...form })
    }
    dialog.value = false
    ElMessage.success('已写入服务端 models.json')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  }
}

async function remove(id: string) {
  await store.removeModel(id)
}

onMounted(() => {
  store.load().catch((e: unknown) => {
    ElMessage.error(e instanceof Error ? e.message : '模型列表加载失败')
  })
})
</script>

<template>
  <div class="models">
    <div class="toolbar">
      <div>
        <h2>模型配置</h2>
        <p class="text-sm">
          配置保存在服务端 data/models.json。当前进程实际推理仍由
          <code>.env</code> / <code>agent_config.yaml</code> 决定，选用不会热切换。
        </p>
      </div>
      <el-button type="primary" @click="openAdd">添加模型</el-button>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="当前选用（UI 标记）"
      :description="store.active ? `${store.active.name} · ${store.active.provider} · ${store.active.model}` : '无'"
      style="margin-bottom: 14px"
    />

    <el-table :data="store.models" stripe>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="provider" label="Provider" width="110" />
      <el-table-column prop="model" label="Model" width="140" />
      <el-table-column prop="baseUrl" label="Base URL" min-width="200" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.active" type="success" size="small">当前</el-tag>
          <el-tag v-else type="info" size="small">备用</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="store.setActive((row as ModelConfig).id)">选用</el-button>
          <el-button link @click="openEdit(row as ModelConfig)">编辑</el-button>
          <el-button link type="danger" @click="remove((row as ModelConfig).id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="editingId ? '编辑模型' : '添加模型'" width="520px">
      <el-form label-width="96px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：DeepSeek 生产" />
        </el-form-item>
        <el-form-item label="Provider">
          <el-select v-model="form.provider" style="width: 100%">
            <el-option label="mock" value="mock" />
            <el-option label="openai" value="openai" />
          </el-select>
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.baseUrl" placeholder="https://api.example.com/v1" />
        </el-form-item>
        <el-form-item label="Model">
          <el-input v-model="form.model" placeholder="deepseek-chat" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.apiKey" placeholder="YOUR_API_KEY" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.models {
  height: 100%;
  overflow-y: auto;
  padding: 16px 20px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 14px;
}
h2 {
  margin: 0 0 6px;
  font-size: 16px;
}
code {
  font-family: var(--mono);
  font-size: 12px;
  background: #f1f5f9;
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
