<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, ref } from 'vue'
import { ApiError, requestKey, type Workspace } from './api'
import { message, t } from './i18n'

const { workspace } = defineProps<{workspace: Workspace}>()
const entities = [
  ['unit', 'Units', 'inventory'], ['item', 'Items', 'inventory'], ['warehouse', 'Warehouses', 'inventory'],
  ['asset', 'Assets', 'assets'], ['location', 'Locations', 'inventory'], ['supplier', 'Suppliers', 'purchasing'], ['customer', 'Customers', 'sales'],
] as const
const available = computed(() => entities.filter(([, , module]) => workspace.modules.some(row => row.id === module && row.enabled)))
const issueReasons: Record<string, string> = {
  file_required: 'Choose a CSV file.',
  file_size_limit: 'The file exceeds the size limit in bytes.',
  utf8_required: 'Save the CSV file as UTF-8.',
  duplicate_header: 'This column appears more than once in the header.',
  unknown_header: 'This column is not supported by the selected template.',
  missing_header: 'This required column is missing from the header.',
  required_value: 'Enter a value in this column.',
  invalid_text: 'Remove the null character from this field.',
  invalid_boolean: 'Enter true or false, or leave blank for true.',
  invalid_terms: 'Enter whole payment days from 0 to 365, or leave blank for 30.',
  unsupported_currency: 'Select a supported currency.',
  invalid_code: 'Use letters A–Z, digits, hyphens or underscores. Start with a letter or digit.',
  too_long: 'This value exceeds the character limit.',
  unknown_reference: 'This reference does not exist. Check its spelling and scope.',
  duplicate_in_file: 'This identifier is repeated in the file for the same scope.',
  code_exists: 'This identifier already exists in the same scope.',
  column_count: 'The number of fields does not match the header.',
  malformed_csv: 'Check CSV quoting and delimiters.',
  row_limit: 'The file exceeds the data row limit.',
  no_rows: 'Add at least one data row after the header.',
}
const page=ref(1),size=ref(10),q=ref('')
const kind = ref('')
const file = ref<File>()
const key = ref('')
const input = ref<HTMLInputElement>()
const busy = ref(false)
const error = ref('')
const result = ref<{row_count: number; count?:number; total_errors?:number; imported?: number; errors: Issue[]}>()
type Issue = {row: number; column: string; reason: string; detail?: string; limit?: number; expected?: number; actual?: number}
const valid = computed(() => result.value && result.value.row_count > 0 && !(result.value.total_errors??result.value.errors.length) && result.value.imported === undefined)

function selectFile() {
  page.value=1;q.value=''
  file.value = input.value?.files?.[0]
  key.value = requestKey()
  result.value = undefined
  error.value = ''
}
function reset() {
  if (input.value) input.value.value = ''
  selectFile()
}
async function upload(operation: 'preview' | 'commit') {
  if (!file.value || !kind.value || busy.value) return
  busy.value = true
  error.value = ''
  
  try {
    const csrf = document.cookie.split('; ').find(value => value.startsWith('csrftoken='))
    const headers: Record<string, string> = {}
    if (csrf) headers['X-CSRFToken'] = decodeURIComponent(csrf.slice('csrftoken='.length))
    if (operation === 'commit') headers['Idempotency-Key'] = key.value
    const data = new FormData()
    data.set('file', file.value)
    const response = await fetch('/api/import/' + kind.value + '/' + operation+(operation==='preview'?'?'+new URLSearchParams({page:String(page.value),page_size:String(size.value),q:q.value}):''), {method: 'POST', headers, body: data})
    const payload = await response.json()
    if (Array.isArray(payload.errors)) result.value = payload
    else error.value = message(new ApiError(payload.error ?? 'request_failed', response.status))
  } catch {
    error.value = 'The request could not be completed. Try again.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <h1>{{ t('Data import') }}</h1>
  <form v-validated-form class="panel record-form" @submit.prevent="upload('preview')">
    <div class="field-wrap"><label>{{ t('Data type') }}
      <select data-field="kind" v-model="kind" :disabled="busy" required @change="reset">
        <option value="" disabled>{{ t('Select data type') }}</option>
        <option v-for="[key, name] in available" :key="key" :value="key">{{ t(name) }}</option>
      </select>
    </label><FieldHint :label="t('Data type')" required /></div>
    <a v-if="kind" :href="'/api/import/' + kind + '/template'">{{ t('Download CSV template') }}</a>
    <div class="field-wrap"><label>{{ t('CSV file') }}<input data-field="file" ref="input" type="file" accept=".csv,text/csv" :disabled="busy || !kind" required @change="selectFile"></label><FieldHint :label="t('CSV file')" required /></div>
    <div class="actions">
      <button class="secondary" :disabled="busy">{{ t('Validate & preview') }}</button>
      <button type="button" class="primary" :disabled="busy || !valid" @click="upload('commit')">{{ t('Import') }}</button>
    </div>
  </form>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <section v-if="result" class="panel approval-section table-panel">
    <p role="status">{{ t(result.imported !== undefined ? 'Imported rows' : 'Rows checked') }} · {{ result.imported ?? result.row_count }}</p>
    <TableToolbar v-if="result.total_errors" :page="page" :size="size" :query="q" :count="result.count||0" @page="page=$event;upload('preview')" @size="size=$event;page=1;upload('preview')" @search="q=$event;page=1;upload('preview')"/><table v-if="result.errors.length">
      <thead><tr><th>{{ t('CSV row') }}</th><th>{{ t('Column') }}</th><th>{{ t('Reason') }}</th></tr></thead>
      <tbody><tr v-for="(issue, index) in result.errors" :key="index">
        <td>{{ issue.row }}</td><td><bdi>{{ issue.column }}</bdi></td>
        <td>{{ t(issueReasons[issue.reason] ?? issue.reason) }}<span v-if="issue.limit !== undefined"> · {{ issue.limit }}</span>
          <span v-if="issue.expected !== undefined"> · {{ t('Expected') }} {{ issue.expected }} · {{ t('Found') }} {{ issue.actual }}</span>
          <span v-if="issue.detail"> · <bdi>{{ issue.detail }}</bdi></span></td>
      </tr></tbody>
    </table>
  </section>
</template>
