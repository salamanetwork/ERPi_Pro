<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from './api'
import { message, t } from './i18n'
import { useDirtyForm } from './useDirectory'
type Settings = {automatic:boolean;next_number:string}
const form = ref<Settings>()
const loadedNumber = ref('')
const failure = ref('')
const busy = ref(false)
const saved = ref(false)
const {reset} = useDirtyForm(() => form.value)
function apply(result:Settings) {
  form.value = result
  loadedNumber.value = result.next_number
  reset()
}
onMounted(async () => {
  try { apply(await api<Settings>('/hr/employee-numbering')) }
  catch (error) { failure.value = message(error) }
})
async function save() {
  if (!form.value || busy.value) return
  busy.value = true
  saved.value = false
  failure.value = ''
  try {
    const {automatic,next_number} = form.value
    const changed = loadedNumber.value !== next_number
    if (changed && (!/^\d+$/.test(next_number) || !Number.isSafeInteger(Number(next_number)))) {
      failure.value = t('Enter a valid employee number.')
      return
    }
    apply(await api<Settings>('/hr/employee-numbering','PATCH',{
      automatic,...(changed ? {next_number:Number(next_number)} : {}),
    }))
    saved.value = true
  } catch (error) { failure.value = message(error) }
  finally { busy.value = false }
}
</script>
<template>
  <RouterLink class="back-link" to="/hr/setup/departments">‹ {{ t('HR Setup') }}</RouterLink>
  <h1>{{ t('Employee numbering') }}</h1>
  <p v-if="failure" class="alert">{{ t(failure) }}</p>
  <p v-if="saved" class="success">{{ t('Changes saved.') }}</p>
  <form v-validated-form v-if="form" class="panel record-form aligned-form" @submit.prevent="save">
    <div class="field-wrap"><label class="check"><input data-field="automatic" v-model="form.automatic" type="checkbox">{{ t('Automatic numbering') }}</label><FieldHint :label="t('Automatic numbering')" /></div>
    <div v-if="form.automatic" class="field-wrap"><label>{{ t('Next employee number') }}<input data-field="next_number" :placeholder="t('Enter {field}').replace('{field}', t('Next employee number'))" v-model="form.next_number" inputmode="numeric" required pattern="[1-9][0-9]*"></label><FieldHint :label="t('Next employee number')" required /></div>
    <div class="actions form-actions"><button class="primary" :disabled="busy">{{ t('Save') }}</button>
      <RouterLink to="/hr/setup/departments" class="button secondary">{{ t('Cancel') }}</RouterLink></div>
  </form>
</template>
