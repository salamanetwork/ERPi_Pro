<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Entry } from './api'
import { message, t } from './i18n'

type Version = { currency: string; version: number; threshold: string; steps: Entry[] }
const roles = ref<Entry[]>([])
const versions = ref<Version[]>([])
const currencies = ref<string[]>([])
const currency = ref('')
const threshold = ref('')
const manager = ref<number>()
const finance = ref<number>()
const busy = ref(true)
const error = ref('')
const notice = ref('')

function selectCurrency() {
  const published = versions.value.find(item => item.currency === currency.value)
  threshold.value = published?.threshold ?? ''
  manager.value = published?.steps[0]?.id
  finance.value = published?.steps[1]?.id
}

onMounted(async () => {
  try {
    const data = await api<{ roles: Entry[]; versions: Version[]; currencies: string[] }>('/purchase-templates')
    roles.value = data.roles
    versions.value = data.versions
    currencies.value = data.currencies
    currency.value = data.currencies[0] ?? ''
    selectCurrency()
  } catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
})

async function publish() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const item = await api<Version>('/purchase-templates', 'POST', {
      currency: currency.value, threshold: threshold.value, manager_role_id: manager.value, finance_role_id: finance.value,
    })
    versions.value.unshift(item)
    notice.value = 'Approval template published.'
  } catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}
</script>

<template>
  <RouterLink class="back-link" to="/purchase-requests">‹ {{ t('Back to purchase requisitions') }}</RouterLink>
  <h1>{{ t('Purchase approval templates') }}</h1>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <form v-validated-form v-if="currencies.length" class="panel record-form" @submit.prevent="publish">
    <p class="muted">{{ t('Finance reviews requests whose total exceeds the threshold.') }}</p>
    <p v-if="!busy && roles.length < 2" class="muted">{{ t('Create two roles with purchasing.view and purchasing.approve in Access control first.') }}</p>
    <div class="field-wrap"><label>{{ t('Currency') }}<select data-field="currency" v-model="currency" required @change="selectCurrency">
      <option v-for="code in currencies" :key="code" :value="code">{{ code }}</option>
    </select></label><FieldHint :label="t('Currency')" required /></div>
    <div class="field-wrap"><label>{{ t('Finance threshold') }}<input data-field="threshold" :placeholder="t('e.g., 125.50')" v-model="threshold" inputmode="decimal" required pattern="[0-9]{1,15}(\.[0-9]{1,2})?"></label><FieldHint :label="t('Finance threshold')" required /></div>
    <div class="field-wrap"><label>{{ t('Department manager role') }}<select data-field="manager" v-model="manager" required>
      <option :value="undefined" disabled>{{ t('Select a role') }}</option>
      <option v-for="role in roles" :key="role.id" :value="role.id">{{ role.name }}</option>
    </select></label><FieldHint :label="t('Department manager role')" required /></div>
    <div class="field-wrap"><label>{{ t('Finance reviewer role') }}<select data-field="finance" v-model="finance" required>
      <option :value="undefined" disabled>{{ t('Select a role') }}</option>
      <option v-for="role in roles" :key="role.id" :value="role.id" :disabled="role.id === manager">{{ role.name }}</option>
    </select></label><FieldHint :label="t('Finance reviewer role')" required /></div>
    <button class="primary" :disabled="busy || !manager || !finance || manager === finance">{{ t('Publish template') }}</button>
  </form>
  <section v-if="versions.length" class="panel record-form approval-section">
    <h2>{{ t('Published versions') }}</h2>
    <div v-for="item in versions" :key="item.currency + item.version">
      <strong>{{ item.currency }} · {{ t('Version') }} {{ item.version }}</strong>
      <span class="muted"> · {{ item.threshold }} · {{ item.steps.map(step => step.name).join(' → ') }}</span>
    </div>
  </section>
</template>
