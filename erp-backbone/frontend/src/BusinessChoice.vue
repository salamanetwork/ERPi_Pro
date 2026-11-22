<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from './api'
import { locale, message } from './i18n'

const p = defineProps<{ url: string; label: string; required?: boolean }>()
const value = defineModel<number | null>()
const items = ref<Record<string, any>[]>([])
const error = ref('')
let ticket = 0

watch(() => p.url, async () => {
  const key = ++ticket
  error.value = ''
  items.value = []
  if (!p.url) return
  try {
    const sep = p.url.includes('?') ? '&' : '?'
    const r = await api<{ results: Record<string, any>[] }>(`${p.url}${sep}page_size=200`)
    if (key === ticket) {
      items.value = (r.results ?? []).map(x => ({
        id: x.id,
        code: x.code ?? '',
        name: x.name || x['name_' + locale.value] || [x.resource__name, x.start].filter(Boolean).join(' · ') || `#${x.id}`,
      }))
    }
  } catch (e) {
    if (key === ticket) error.value = message(e)
  }
}, { immediate: true })
</script>

<template>
  <div>
    <EntityAutocomplete
      :label="label"
      :choices="items"
      :required="required"
      :empty-value="null"
      v-model="value"
    />
    <small v-if="error" role="alert">{{ error }}</small>
  </div>
</template>
