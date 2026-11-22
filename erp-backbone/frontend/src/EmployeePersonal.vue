<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from './api'
import { message, t } from './i18n'

type Personal = {
  revision: number; can_edit: boolean; can_view_full_id: boolean
  birth_date: string | null; nationality: string; address: string
  national_id_masked: string; national_id?: string
}
const props = defineProps<{ employee: number; revision: number; allowed: boolean }>()
const emit = defineEmits<{ changed: [revision: number] }>()
const personal = ref<Personal>()
const newId = ref('')
const clearId = ref(false)
const showId = ref(false)
const error = ref('')
const busy = ref(false)
const saved = ref(false)
let sequence = 0

watch(() => [props.employee, props.allowed, props.revision] as const, async ([employee, allowed, revision], previous) => {
  if (previous?.[0] === employee && allowed && personal.value?.revision === revision) return
  const request = ++sequence
  personal.value = undefined
  newId.value = error.value = ''
  clearId.value = showId.value = saved.value = false
  if (!allowed) return
  try {
    const result = await api<Personal>('/hr/employees/' + employee + '/personal')
    if (request === sequence) personal.value = result
  } catch (failure) {
    if (request === sequence) error.value = message(failure)
  }
}, { immediate: true })

async function save() {
  const data = personal.value
  if (!data || busy.value) return
  const request = sequence
  busy.value = true
  error.value = ''
  saved.value = false
  const { revision, birth_date, address, nationality } = data
  const identity = clearId.value ? {national_id: ''} : newId.value.trim() ? {national_id: newId.value.trim()} : {}
  try {
    const result = await api<Personal>('/hr/employees/' + props.employee + '/personal', 'PATCH',
      {revision, birth_date: birth_date || null, address, nationality, ...identity})
    if (request !== sequence) return
    personal.value = result
    newId.value = ''
    clearId.value = showId.value = false
    saved.value = true
    emit('changed', result.revision)
  } catch (failure) {
    if (request === sequence) error.value = message(failure)
  } finally { busy.value = false }
}
async function toggleId() {
  if (showId.value) {
    showId.value = false
    if (personal.value) delete personal.value.national_id
    return
  }
  const request = sequence
  busy.value = true
  error.value = ''
  try {
    const revealed = await api<Personal>('/hr/employees/' + props.employee + '/personal?reveal=1')
    if (request === sequence && personal.value && revealed.national_id) {
      personal.value.national_id = revealed.national_id
      showId.value = true
    }
  } catch (failure) {
    if (request === sequence) error.value = message(failure)
  } finally { busy.value = false }
}
import DateInput from './DateInput.vue'
</script>

<template>
  <section v-if="personal || error" class="panel hr-card">
    <h3>{{ t('Personal details') }}</h3>
    <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
    <p v-if="saved" class="success" role="status">{{ t('Changes saved.') }}</p>
    <form v-validated-form v-if="personal" class="record-form" @submit.prevent="save">
      <DateInput data-field="birth_date" :label="t('Date of birth')" v-model="personal.birth_date" type="date" :readonly="!personal.can_edit" />
      <div class="field-wrap"><label>{{ t('Nationality') }}<input data-field="nationality" :placeholder="t('Enter {field}').replace('{field}', t('Nationality'))" v-model="personal.nationality" maxlength="80" :readonly="!personal.can_edit"></label><FieldHint :label="t('Nationality')" :readonly="!personal.can_edit" /></div>
      <div class="field-wrap"><label>{{ t('Address') }}<textarea data-field="address" :placeholder="t('Enter details')" v-model="personal.address" maxlength="500" :readonly="!personal.can_edit"></textarea></label><FieldHint :label="t('Address')" :readonly="!personal.can_edit" /></div>
      <div class="field-wrap"><label>{{ t('National ID') }}<input :value="showId ? personal.national_id : personal.national_id_masked" readonly dir="auto"></label><FieldHint :label="t('National ID')" readonly /></div>
      <button v-if="personal.can_view_full_id && personal.national_id_masked" type="button" class="secondary" :disabled="busy" @click="toggleId">
        {{ t(showId ? 'Hide national ID' : 'Show national ID') }}
      </button>
      <template v-if="personal.can_edit">
        <div class="field-wrap"><label>{{ t('New national ID') }}<input data-field="newId" :placeholder="t('Enter {field}').replace('{field}', t('New national ID'))" v-model="newId" maxlength="64" autocomplete="off" :disabled="clearId"></label><FieldHint :label="t('New national ID')" /></div>
        <div class="field-wrap" v-if="personal.national_id_masked"><label class="check"><input data-field="clearId" v-model="clearId" type="checkbox">{{ t('Remove national ID') }}</label><FieldHint :label="t('Remove national ID')" /></div>
        <button class="primary" :disabled="busy">{{ t('Save personal details') }}</button>
      </template>
    </form>
  </section>
</template>
