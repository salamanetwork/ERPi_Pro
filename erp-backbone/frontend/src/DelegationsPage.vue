<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPage} from 'vue'
const pg=ref(1),ps=ref(25),total=ref(0),term=ref('')
const paging=()=>new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value})

import { computed, onMounted, ref } from 'vue'
import { api, type Entry, type Workspace } from './api'
import { message, t } from './i18n'

const { workspace } = defineProps<{ workspace: Workspace }>()
type Assignment = Entry & { site_id: number | null; permissions: string[] }
type Grant = {
  id: number; requester: string; delegate: string; role: string; site: string | null;
  permissions: string[]; starts_at: string; ends_at: string; reason: string; status: string;
  previous: number | null; can_extend: boolean;
}
type Directory = {count:number;
  items: Grant[]; assignments: Assignment[]; sites: Entry[]; all_sites: boolean;
  users: { id: number; username: string }[]; username: string;
}
const directory = ref<Directory>()
const assignmentId = ref<number>()
const delegateId = ref<number>()
const siteId = ref<number | null>(null)
const permissions = ref<string[]>([])
const starts = ref('')
const ends = ref('')
const reason = ref('')
const previous = ref<Grant>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
const assignment = computed(() => directory.value?.assignments.find(item => item.id === assignmentId.value))
const labels: Record<string, string> = {
  pending: 'Pending approval', active: 'Active', scheduled: 'Scheduled', expired: 'Expired',
  inactive: 'Inactive', rejected: 'Rejected', revoked: 'Revoked',
}

function chooseAssignment() {
  siteId.value = assignment.value?.site_id ?? (directory.value?.all_sites ? null : directory.value?.sites[0]?.id ?? null)
  permissions.value = []
}

watchPage([pg,ps,term],()=>load().catch(e=>error.value=message(e)))
async function load() {
  directory.value = await api<Directory>('/delegations?'+paging());total.value=directory.value.count
}

async function run(operation: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try { await operation() }
  catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}

onMounted(() => { void run(load) })

function submit() {
  void run(async () => {
    const details = previous.value ? { previous: previous.value.id } : {
      assignment_id: assignmentId.value, delegate_id: delegateId.value, site_id: siteId.value,
      permissions: permissions.value, starts_at: new Date(starts.value).toISOString(),
    }
    await api('/delegations', 'POST', { ...details, reason: reason.value, ends_at: new Date(ends.value).toISOString() })
    previous.value = undefined
    reason.value = ''
    ends.value = ''
    await load()
    notice.value = 'Delegation request sent.'
  })
}

function decide(id: number, action: 'approve' | 'reject' | 'revoke') {
  void run(async () => {
    await api('/delegations/' + id + '/decision', 'POST', { action })
    await load()
    notice.value = 'Changes saved.'
  })
}

function extend(grant: Grant) {
  previous.value = grant
  ends.value = ''
  reason.value = ''
  document.getElementById('workspace-content')?.scrollTo({ top: 0, behavior: 'smooth' })
}

const date = (value: string) => new Date(value).toLocaleString()
import DateInput from './DateInput.vue'
</script>

<template>
  <div class="page-heading">
    <h1>{{ t('Delegations') }}</h1>
    <button type="button" class="secondary" :disabled="busy" @click="run(load)">{{ t('Refresh status') }}</button>
  </div>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <form v-validated-form v-if="directory?.assignments.length || previous" class="panel record-form" @submit.prevent="submit">
    <h2>{{ previous ? t('Request extension') + ' #' + previous.id : t('Request delegation') }}</h2>
    <template v-if="!previous">
      <div class="field-wrap"><label>{{ t('Source role assignment') }}
        <select data-field="assignmentId" v-model="assignmentId" required @change="chooseAssignment">
          <option :value="undefined" disabled>{{ t('Select an assignment') }}</option>
          <option v-for="item in directory?.assignments" :key="item.id" :value="item.id">
            {{ item.name }} · {{ item.site_id === null ? t('All sites') : directory?.sites.find(site => site.id === item.site_id)?.name }}
          </option>
        </select>
      </label><FieldHint :label="t('Source role assignment')" required /></div>
      <EntityAutocomplete
        data-field="delegateId"
        :label="t('Delegate')"
        lookup="users"
        :choices="directory?.users ?? []"
        :placeholder="t('Select a user')"
        :empty-value="undefined"
        required
        v-model="delegateId"
      />
      <div class="field-wrap" v-if="assignment"><label>{{ t('Site') }}
        <select data-field="siteId" v-model="siteId">
          <option v-if="assignment.site_id === null && directory?.all_sites" :value="null">{{ t('All sites') }}</option>
          <option v-for="site in directory?.sites.filter(item => assignment?.site_id === null || item.id === assignment?.site_id)"
            :key="site.id" :value="site.id">{{ site.name }}</option>
        </select>
      </label><FieldHint :label="t('Site')" /></div>
      <fieldset v-if="assignment">
        <legend>{{ t('Permissions') }}</legend>
        <div v-for="permission in assignment.permissions" :key="permission" class="field-wrap"><label class="delegation-check">
          <input data-field="permissions" v-model="permissions" type="checkbox" :value="permission"><bdi>{{ permission }}</bdi>
        </label><FieldHint :label="t('Permissions')" /></div>
      </fieldset>
      <DateInput data-field="starts" :label="t('Starts at (local time)')" v-model="starts" type="datetime-local" required />
    </template>
    <p v-else class="muted">{{ previous.delegate }} · {{ previous.role }} · {{ previous.site ?? t('All sites') }}</p>
    <DateInput data-field="ends" :label="t('Ends at (local time)')" v-model="ends" type="datetime-local" required />
    <div class="field-wrap"><label>{{ t('Reason') }}<textarea data-field="reason" :placeholder="t('Explain the reason')" v-model="reason" required maxlength="500" rows="2"></textarea></label><FieldHint :label="t('Reason')" required /></div>
    <div class="actions">
      <button class="primary" :disabled="busy || (!previous && !permissions.length)">{{ t('Send request') }}</button>
      <button v-if="previous" type="button" class="secondary" @click="previous = undefined">{{ t('Cancel') }}</button>
    </div>
  </form>
  <section v-if="directory" class="panel table-panel approval-section">
    <TableToolbar :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table v-if="directory.items.length">
      <thead><tr><th>{{ t('Delegation') }}</th><th>{{ t('Scope and permissions') }}</th>
        <th>{{ t('Period') }}</th><th>{{ t('Status') }}</th><th>{{ t('Actions') }}</th></tr></thead>
      <tbody><tr v-for="grant in directory.items" :key="grant.id">
        <td><strong>#{{ grant.id }}</strong> · <bdi>{{ grant.requester }}</bdi> → <bdi>{{ grant.delegate }}</bdi>
          <div v-if="grant.previous" class="muted">{{ t('Extension of') }} #{{ grant.previous }}</div>
          <div class="approval-reason">{{ grant.reason }}</div>
        </td>
        <td><bdi>{{ grant.role }}</bdi> · {{ grant.site ?? t('All sites') }}
          <div class="muted">{{ grant.permissions.join(', ') }}</div>
        </td>
        <td><div>{{ date(grant.starts_at) }}</div><div>{{ date(grant.ends_at) }}</div></td>
        <td><span class="badge" :class="{ on: grant.status === 'active' }">{{ t(labels[grant.status] ?? grant.status) }}</span></td>
        <td><div class="actions">
          <button v-if="workspace.admin && grant.status === 'pending' &&
            workspace.username !== grant.requester && workspace.username !== grant.delegate"
            class="primary" :disabled="busy" @click="decide(grant.id, 'approve')">{{ t('Approve') }}</button>
          <button v-if="workspace.admin && grant.status === 'pending'" class="secondary" :disabled="busy"
            @click="decide(grant.id, 'reject')">{{ t('Reject') }}</button>
          <button v-if="workspace.admin && ['active', 'scheduled', 'inactive'].includes(grant.status)"
            class="secondary" :disabled="busy" @click="decide(grant.id, 'revoke')">{{ t('Revoke') }}</button>
          <button v-if="workspace.username === grant.requester && grant.can_extend" class="secondary" :disabled="busy"
            @click="extend(grant)">{{ t('Request extension') }}</button>
        </div></td>
      </tr></tbody>
    </table>
    <p v-else class="empty muted">{{ t('No delegations yet.') }}</p>
  </section>
</template>
