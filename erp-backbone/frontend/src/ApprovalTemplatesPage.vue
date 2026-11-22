<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Entry } from './api'
import { message, t } from './i18n'

type Version = { version: number; steps: Entry[]; published_at: string }
const versions = ref<Version[]>([])
const roles = ref<Entry[]>([])
const selected = ref<(number | null)[]>([null])
const busy = ref(true)
const error = ref('')
const notice = ref('')

function loadVersions(data: { versions: Version[]; roles: Entry[] }) {
  versions.value = data.versions
  roles.value = data.roles
  selected.value = data.versions[0]?.steps.map(step => step.id) ?? [null]
}

onMounted(async () => {
  try { loadVersions(await api('/work-order-approvals')) }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
})

async function publish() {
  if (busy.value || selected.value.includes(null)) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const version = await api<Version>('/work-order-approvals', 'POST', { role_ids: selected.value })
    versions.value.unshift(version)
    notice.value = 'Approval template published.'
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
</script>

<template>
  <RouterLink class="back-link" to="/work-orders">‹ {{ t('Back to work orders') }}</RouterLink>
  <h1>{{ t('Approval templates') }}</h1>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <form v-validated-form class="panel record-form" @submit.prevent="publish">
    <p class="muted">{{ t('Choose reviewer roles in order. New submissions use the published template.') }}</p>
    <p v-if="!busy && !roles.length" class="muted">
      {{ t('Create a role with workorders.view and workorders.approve in Access control first.') }}
      <RouterLink to="/access">{{ t('Access control') }}</RouterLink>
    </p>
    <div v-for="(_, index) in selected" :key="index" class="split">
      <div class="field-wrap"><label class="approval-step">{{ t('Reviewer role') }} {{ index + 1 }}
        <select v-model="selected[index]" required :disabled="busy || !roles.length">
          <option :value="null" disabled>{{ t('Select a role') }}</option>
          <option v-for="role in roles" :key="role.id" :value="role.id"
            :disabled="selected.includes(role.id) && selected[index] !== role.id">{{ role.name }}</option>
        </select>
      </label><FieldHint :label="t('Reviewer role')" required /></div>
      <button v-if="selected.length > 1" type="button" class="secondary" :disabled="busy"
        @click="selected.splice(index, 1)">{{ t('Remove step') }}</button>
    </div>
    <div class="actions">
      <button type="button" class="secondary" :disabled="busy || selected.length >= Math.min(roles.length, 10)"
        @click="selected.push(null)">{{ t('Add step') }}</button>
      <button class="primary" :disabled="busy || selected.includes(null)">{{ t('Publish template') }}</button>
    </div>
  </form>
  <section v-if="versions.length" class="panel record-form approval-section">
    <h2>{{ t('Published versions') }}</h2>
    <div v-for="version in versions" :key="version.version">
      <strong>{{ t('Version') }} {{ version.version }}</strong>
      <span class="muted"> · {{ version.steps.map(step => step.name).join(' → ') }}</span>
    </div>
  </section>
</template>
