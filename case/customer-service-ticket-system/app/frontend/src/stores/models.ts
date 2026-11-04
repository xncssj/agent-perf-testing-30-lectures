import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { activateModel, createModel, deleteModel, fetchModels, updateModel } from '@/api/catalog'
import type { ModelConfig } from '@/types'

export const useModelsStore = defineStore('models', () => {
  const models = ref<ModelConfig[]>([])
  const runtimeNote = ref('')
  const active = computed(() => models.value.find((m) => m.active) || null)

  async function load() {
    const data = await fetchModels()
    models.value = data.items
    runtimeNote.value = data.runtime.note
  }

  async function addModel(partial: Omit<ModelConfig, 'id' | 'active'>) {
    await createModel(partial)
    await load()
  }

  async function saveModel(id: string, patch: Partial<ModelConfig>) {
    await updateModel(id, patch)
    await load()
  }

  async function removeModel(id: string) {
    await deleteModel(id)
    await load()
  }

  async function setActive(id: string) {
    await activateModel(id)
    await load()
  }

  return { models, active, runtimeNote, load, addModel, saveModel, removeModel, setActive }
})
