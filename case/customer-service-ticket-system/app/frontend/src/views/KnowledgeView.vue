<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchKnowledge, createKnowledge, updateKnowledge, deleteKnowledge, uploadKnowledge } from '@/api/catalog'

interface KbItem {
  id?: number
  category?: string
  title?: string
  content?: string
  keywords?: string[] | string
  rank?: number
}

const query = ref('')
const items = ref<KbItem[]>([])
const source = ref('')
const error = ref('')
const loading = ref(false)

// CRUD 相关
const dialog = ref(false)
const editingId = ref<number | null>(null)
const form = ref({ category: '', title: '', content: '', keywords: '' })

// 上传相关
const uploadDialog = ref(false)
const uploadCategory = ref('uploaded')
const uploadFile = ref<File | null>(null)
const uploading = ref(false)
const uploadResult = ref<{ total_chunks: number; filename: string } | null>(null)

// 预览抽屉
const drawer = ref(false)
const preview = ref<KbItem | null>(null)

function openPreview(k: KbItem) {
  preview.value = k
  drawer.value = true
}

function keywordsOf(k: KbItem): string[] {
  if (Array.isArray(k.keywords)) return k.keywords
  if (!k.keywords) return []
  try {
    const parsed = JSON.parse(k.keywords)
    return Array.isArray(parsed) ? parsed.map(String) : [String(k.keywords)]
  } catch {
    return [String(k.keywords)]
  }
}

async function load(q = '') {
  loading.value = true
  try {
    const data = await fetchKnowledge(q)
    items.value = data.items
    source.value = data.source
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '知识库加载失败'
  } finally {
    loading.value = false
  }
}

function search() {
  void load(query.value.trim())
}

function openAdd() {
  editingId.value = null
  form.value = { category: '', title: '', content: '', keywords: '' }
  dialog.value = true
}

function openEdit(k: KbItem) {
  editingId.value = k.id ?? null
  form.value = {
    category: k.category || '',
    title: k.title || '',
    content: k.content || '',
    keywords: keywordsOf(k).join(', '),
  }
  dialog.value = true
}

async function save() {
  const keywords = form.value.keywords
    ? form.value.keywords.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    : []
  try {
    if (editingId.value) {
      await updateKnowledge(editingId.value, {
        category: form.value.category,
        title: form.value.title,
        content: form.value.content,
        keywords,
      })
    } else {
      await createKnowledge({
        category: form.value.category,
        title: form.value.title,
        content: form.value.content,
        keywords,
      })
    }
    dialog.value = false
    await load(query.value.trim())
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存失败'
  }
}

async function remove(kid: number) {
  if (!confirm('确定删除这篇知识文档？')) return
  try {
    await deleteKnowledge(kid)
    await load(query.value.trim())
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
  }
}

function openUpload() {
  uploadFile.value = null
  uploadCategory.value = 'uploaded'
  uploadResult.value = null
  uploadDialog.value = true
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  uploadFile.value = input.files?.[0] ?? null
}

async function doUpload() {
  if (!uploadFile.value) return
  uploading.value = true
  error.value = ''
  try {
    const res = await uploadKnowledge(uploadFile.value, uploadCategory.value)
    uploadResult.value = { total_chunks: res.total_chunks, filename: res.filename }
    await load(query.value.trim())
  } catch (e) {
    error.value = e instanceof Error ? e.message : '上传失败'
  } finally {
    uploading.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="kb">
    <div class="toolbar">
      <el-input
        v-model="query"
        clearable
        placeholder="输入问题检索知识库，如：如何申请退货"
        @keydown.enter="search"
      />
      <el-button type="primary" :loading="loading" @click="search">检索</el-button>
      <el-button v-if="query" @click="query = ''; load()">清空</el-button>
      <el-button type="success" @click="openAdd">新增知识</el-button>
      <el-button @click="openUpload">上传文档</el-button>
    </div>
    <p class="text-sm meta">来源 {{ source || 'sqlite.knowledge' }}</p>
    <p v-if="error" class="text-sm error">{{ error }}</p>
    <div class="kb-cols">
      <div v-for="(k, i) in items" :key="(k.title || '') + i" class="kb-item">
        <h5>
          {{ k.title }}
          <span v-if="k.category" class="cat">{{ k.category }}</span>
          <span v-if="k.rank != null" class="cat">#{{ k.rank }}</span>
        </h5>
        <p class="kb-preview" title="点击查看全文" @click="openPreview(k)">{{ k.content }}</p>
        <div v-if="keywordsOf(k).length" class="kw">
          关键词：{{ keywordsOf(k).map((w) => ' #' + w).join('') }}
        </div>
        <div v-if="k.id" class="actions">
          <el-button link type="primary" @click="openEdit(k)">编辑</el-button>
          <el-button link type="danger" @click="remove(k.id)">删除</el-button>
        </div>
      </div>
      <p v-if="!loading && !items.length" class="text-sm">没有命中文档。</p>
    </div>

    <!-- 新增/编辑对话框 -->
    <el-dialog v-model="dialog" :title="editingId ? '编辑知识' : '新增知识'" width="560px">
      <el-form label-width="80px">
        <el-form-item label="分类">
          <el-input v-model="form.category" placeholder="如：faq、products、policies" />
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="form.title" placeholder="知识标题" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="form.content" type="textarea" :rows="5" placeholder="知识内容" />
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="form.keywords" placeholder="用逗号分隔，如：退货,退款,售后" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 上传文档对话框 -->
    <el-dialog v-model="uploadDialog" title="上传文档" width="480px">
      <el-form label-width="80px">
        <el-form-item label="分类">
          <el-input v-model="uploadCategory" placeholder="如：faq、products、policies" />
        </el-form-item>
        <el-form-item label="选择文件">
          <input type="file" accept=".pdf,.md,.txt" @change="onFileChange" />
          <p class="text-sm" style="color: #909399; margin-top: 4px;">支持 PDF、Markdown、TXT 格式，最大 10MB</p>
        </el-form-item>
      </el-form>
      <div v-if="uploadResult" class="upload-result">
        <el-alert type="success" :closable="false">
          上传成功！文件「{{ uploadResult.filename }}」已拆分为 {{ uploadResult.total_chunks }} 个知识片段。
        </el-alert>
      </div>
      <template #footer>
        <el-button @click="uploadDialog = false">关闭</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadFile" @click="doUpload">
          上传并分块
        </el-button>
      </template>
    </el-dialog>

    <!-- 知识预览抽屉 -->
    <el-drawer v-model="drawer" :title="preview?.title || '知识详情'" size="420px">
      <div v-if="preview" class="preview-body">
        <div class="preview-meta">
          <span v-if="preview.category" class="cat">{{ preview.category }}</span>
          <span v-if="preview.rank != null" class="cat">#{{ preview.rank }}</span>
        </div>
        <p class="preview-content">{{ preview.content }}</p>
        <div v-if="keywordsOf(preview).length" class="kw">
          关键词：{{ keywordsOf(preview).map((w) => ' #' + w).join('') }}
        </div>
        <el-button v-if="preview.id" link type="primary" @click="openEdit(preview)">
          编辑此篇
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.kb {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 16px;
  overflow: hidden;
}
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.meta {
  margin: 0 0 10px;
}
.error {
  color: var(--el-color-danger);
  margin-bottom: 8px;
}
.kb-cols {
  flex: 1;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  align-content: start;
}
.kb-item {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 14px;
}
.kb-item h5 {
  margin: 0 0 8px;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.cat {
  font-size: 11px;
  color: var(--brand-dk);
  background: var(--chip);
  padding: 1px 8px;
  border-radius: 20px;
  font-weight: 500;
}
.kb-item p {
  margin: 0;
  font-size: 13px;
  color: var(--ink);
}
.kb-preview {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
  cursor: pointer;
}
.kb-preview:hover {
  color: var(--brand-dk);
  text-decoration: underline;
}
.preview-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.preview-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.preview-content {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  color: var(--ink);
}
.kw {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ink-2);
}
.actions {
  margin-top: 10px;
  display: flex;
  gap: 4px;
}
.upload-result {
  margin-top: 12px;
}
</style>
