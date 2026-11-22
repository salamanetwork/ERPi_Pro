<script setup lang="ts">
import { computed, ref } from 'vue'
import { allows, ApiError, type Workspace } from './api'
import { message, t } from './i18n'

const props = defineProps<{workspace: Workspace; screen: string; filters?: Record<string, string>; report?: boolean}>()
const companyMasters = ['unit', 'item', 'supplier', 'customer']
const allowed = computed(() => companyMasters.includes(props.screen)
  ? allows(props.workspace, 'platform.export', null) : allows(props.workspace, 'platform.export'))
const error = ref('')
const busy = ref(false)

async function download() {
  busy.value = true
  error.value = ''
  try {
    const base = props.screen === 'employees' ? '/api/hr/employees/export' : props.screen === 'budget-vs-actual' ? '/api/budgeting/report/export' : props.report ? '/api/reports/advanced/' + props.screen + '/export' : '/api/export/' + props.screen
    const path = base + '?' + new URLSearchParams(props.filters)
    const response = await fetch(path)
    if (!response.ok) {
      const body = await response.json()
      throw new ApiError(body.error, response.status)
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = props.screen + '.csv'
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (failure) {
    error.value = message(failure)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <button v-if="allowed" type="button" class="secondary" :disabled="busy" @click="download">{{ t('Export CSV') }}</button>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
</template>
