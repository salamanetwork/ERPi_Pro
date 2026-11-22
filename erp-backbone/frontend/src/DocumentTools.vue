<script setup lang="ts">
import { ref, watch } from 'vue'
import { api, requestKey } from './api'
import { locale, message, t } from './i18n'

const props = defineProps<{ id: number; editable: boolean; archived: boolean; revision: number; kind?: string; showActions?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const rows = ref<{id: number; name: string; size: number}[]>([])
const file = ref<File>()
const input = ref<HTMLInputElement>()
const busy = ref(false)
const error = ref('')
let key = ''
const base = () => '/documents/' + (props.kind ?? 'pr') + '/' + props.id

async function load() {
  const id = props.id
  try {
    const result = await api<{items: typeof rows.value}>(base() + '/attachments')
    if (id === props.id) rows.value = result.items
  } catch (failure) { if (id === props.id) error.value = message(failure) }
}

watch(() => props.id, () => {
  file.value = undefined
  if (input.value) input.value.value = ''
  rows.value = []
  error.value = ''
  void load()
}, { immediate: true })

function select(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0]
  key = requestKey()
}

async function run(operation: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await operation() }
  catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}

function upload() {
  if (!file.value) return
  const data = new FormData()
  data.append('file', file.value)
  void run(async () => {
    await api(base() + '/attachments', 'POST', data, key)
    file.value = undefined
    if (input.value) input.value.value = ''
    await load()
  })
}

function archive() {
  void run(async () => {
    await api(base() + '/archive', 'PATCH', { archived: !props.archived, revision: props.revision })
    emit('changed')
  })
}
</script>

<template>
  <section class="panel record-form approval-section">
    <h2>{{ t('Document tools') }}</h2>
    <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
    <p v-if="archived" class="muted">{{ t('This draft is archived.') }}</p>
    <div v-if="showActions !== false" class="actions">
      <a class="button secondary" :href="'/print/' + (kind ?? 'pr') + '/' + id + '?language=' + locale"
        target="_blank" rel="noopener">{{ t('Print or save as PDF') }}</a>
      <button v-if="editable" class="secondary" type="button" :disabled="busy"
        @click="archive">{{ t(archived ? 'Restore draft' : 'Archive draft') }}</button>
    </div>
    <h3>{{ t('Attachments') }}</h3>
    <ul v-if="rows.length">
      <li v-for="row in rows" :key="row.id">
        <a :href="'/api' + base() + '/attachments/' + row.id"><bdi>{{ row.name }}</bdi></a>
      </li>
    </ul>
    <p v-else class="muted">{{ t('No attachments yet.') }}</p>
    <form v-validated-form v-if="editable && !archived" class="actions" @submit.prevent="upload">
      <div class="field-wrap"><label>{{ t('Choose a file (up to 10 MB)') }}<input data-field="file" ref="input" type="file" required @change="select"></label><FieldHint :label="t('Choose a file (up to 10 MB)')" required /></div>
      <button class="primary" :disabled="busy">{{ t('Upload attachment') }}</button>
    </form>
  </section>
</template>
