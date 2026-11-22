<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Workspace } from './api'
import { message, t } from './i18n'
import CsvExport from './CsvExport.vue'

type Filters = Record<string, string>
type View = {id: number; name: string; filters: Filters; is_default: boolean}
const props = defineProps<{workspace: Workspace; screen: string; filters: Filters; skipDefault?: boolean; hideExport?: boolean}>()
const emit = defineEmits<{apply: [Filters]}>()
const rows = ref<View[]>([])
const selected = ref(0)
const current = computed(() => rows.value.find(row => row.id === selected.value))
const name = ref('')
const isDefault = ref(false)
const busy = ref(false)
const error = ref('')
const feedback = ref('')
const endpoint = '/saved-views/' + props.screen

function select(row?: View, apply = true) {
  selected.value = row?.id ?? 0
  name.value = row?.name ?? ''
  isDefault.value = row?.is_default ?? false
  if (row && apply) emit('apply', row.filters)
}

async function run(task: () => Promise<void>) {
  busy.value = true
  error.value = feedback.value = ''
  try { await task() } catch (failure) { error.value = message(failure) } finally { busy.value = false }
}

async function load() {
  rows.value = (await api<{items: View[]}>(endpoint)).items
}

onMounted(() => run(async () => {
  const initial = JSON.stringify(props.filters)
  await load()
  if (!props.skipDefault && initial === JSON.stringify(props.filters)) select(rows.value.find(row => row.is_default))
}))

function save(update: boolean) {
  const id = update ? current.value?.id : undefined
  return run(async () => {
    const row = await api<View>(id ? endpoint + '/' + id : endpoint, id ? 'PATCH' : 'POST', {
      name: name.value, filters: props.filters, is_default: isDefault.value,
    })
    await load()
    select(row, false)
    feedback.value = 'View saved.'
  })
}

function remove() {
  const id = current.value?.id
  if (!id) return
  return run(async () => {
    await api(endpoint + '/' + id, 'DELETE')
    rows.value = rows.value.filter(row => row.id !== id)
    select()
    feedback.value = 'View deleted.'
  })
}
</script>

<template>
  <div class="actions">
    <div class="field-wrap"><label>{{ t('Saved views') }}
      <select data-field="selected" v-model.number="selected" :disabled="busy" @change="select(current)">
        <option :value="0">{{ t('Current filters') }}</option>
        <option v-for="row in rows" :key="row.id" :value="row.id">{{ row.name }}</option>
      </select>
    </label><FieldHint :label="t('Saved views')" /></div>
    <CsvExport v-if="!hideExport" :workspace="workspace" :screen="screen" :filters="filters" />
  </div>
  <form v-validated-form class="actions" @submit.prevent="save(false)">
    <div class="field-wrap"><label>{{ t('View name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="name" maxlength="80" required></label><FieldHint :label="t('View name')" required /></div>
    <div class="field-wrap"><label class="delegation-check"><input data-field="isDefault" v-model="isDefault" type="checkbox">{{ t('Default view') }}</label><FieldHint :label="t('Default view')" /></div>
    <button class="secondary" :disabled="busy">{{ t('Save view') }}</button>
    <button v-if="current" type="button" class="secondary" :disabled="busy" @click="save(true)">{{ t('Update view') }}</button>
    <button v-if="current" type="button" class="secondary" :disabled="busy" @click="remove">{{ t('Delete view') }}</button>
  </form>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="feedback" class="success" role="status">{{ t(feedback) }}</p>
</template>
