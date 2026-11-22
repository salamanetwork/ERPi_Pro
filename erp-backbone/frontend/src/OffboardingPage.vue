<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { allows, api, createRequest, requestKey, type Workspace } from './api'
import { message, t } from './i18n'
import { useDirectory, useDirtyForm } from './useDirectory'
import DataTable from './DataTable.vue'
import DateInput from './DateInput.vue'
type Request = {id:number;employee_id:number;employee:string;kind:string;last_day:string;reason:string;
  state:string;revision:number;can_approve:boolean;note:string}
type Employee = {id:number;name:string;number:string;site_id:number;ended:string|null;archived:boolean}
const {workspace} = defineProps<{workspace:Workspace}>()
const route = useRoute()
const employee = ref<Employee>()
const list = useDirectory<Request>(() => '/hr/offboarding')
const {page, loading, error, change} = list
const form = ref({kind:'resignation',last_day:'',reason:''})
const {reset} = useDirtyForm(() => form.value)
const failure = ref('')
const busy = ref(false)
const decision = ref<Request>()
const note = ref('')
let loadToken = 0
let submit = createRequest()
let submitSignature = ''
const canRequest = computed(() => employee.value && !employee.value.ended && !employee.value.archived &&
  !page.value?.results.some(row => ['submitted','approved'].includes(row.state)) &&
  allows(workspace,'hr.offboarding.request',employee.value.site_id))
watch(() => route.query.employee, async value => {
  const token = ++loadToken
  employee.value = undefined
  failure.value = ''
  decision.value = undefined
  form.value = {kind:'resignation',last_day:'',reason:''}
  reset()
  submit = createRequest()
  submitSignature = ''
  if (typeof value !== 'string' || !/^\d+$/.test(value)) return
  try {
    const row = await api<Employee>('/hr/employees/'+value)
    if (token === loadToken) employee.value = row
  } catch (error) { if (token === loadToken) failure.value = message(error) }
}, {immediate:true})
async function save() {
  if (!employee.value || busy.value) return
  busy.value = true
  failure.value = ''
  try {
    const payload = {...form.value,employee_id:employee.value.id}
    const signature = JSON.stringify(payload)
    if (signature !== submitSignature) { submit = createRequest(); submitSignature = signature }
    await submit('/hr/offboarding',payload)
    form.value = {kind:'resignation',last_day:'',reason:''}
    reset()
    await list.load()
  } catch (error) { failure.value = message(error) }
  finally { busy.value = false }
}
async function decide(action:string) {
  if (!decision.value || busy.value) return
  busy.value = true
  failure.value = ''
  try {
    await api('/hr/offboarding/'+decision.value.id+'/decision','POST',{action,note:note.value,revision:decision.value.revision},requestKey())
    decision.value = undefined
    await list.load()
  } catch (error) { failure.value = message(error) }
  finally { busy.value = false }
}
const labels: Record<string,string> = { resignation:'Resignation',termination:'Termination',submitted:'Submitted',approved:'Approved',rejected:'Rejected' }
</script>
<template>
  <div class="page-heading"><h1>{{ t('Offboarding') }}</h1>
    <RouterLink v-if="employee" class="button secondary" :to="'/hr/employees/' + employee.id">{{ employee.number }} · {{ employee.name }}</RouterLink>
    <RouterLink v-else class="button secondary" to="/hr/employees">{{ t('Employees') }}</RouterLink>
  </div>
  <p v-if="failure" class="alert" role="alert">{{ t(failure) }}</p>
  <form v-validated-form v-if="canRequest" class="panel record-form aligned-form" @submit.prevent="save">
    <h2>{{ t('New offboarding request') }}</h2>
    <div class="field-wrap"><label>{{ t('Request type') }}<select data-field="kind" v-model="form.kind">
      <option value="resignation">{{ t('Resignation') }}</option><option value="termination">{{ t('Termination') }}</option>
    </select></label><FieldHint :label="t('Request type')" required /></div>
    <DateInput data-field="last_day" :label="t('Last employment day')" v-model="form.last_day" type="date" required />
    <div class="field-wrap form-wide"><label>{{ t('Reason') }}<textarea data-field="reason" :placeholder="t('Explain the reason')" v-model="form.reason" maxlength="500" required /></label><FieldHint :label="t('Reason')" required /></div>
    <div class="actions form-actions"><button class="primary" :disabled="busy">{{ t('Submit') }}</button></div>
  </form>
  <DataTable :page="page" :headings="['Employee','Request type','Last employment day','Reason','Status','Actions']"
    :loading="loading" :error="error" @page="change('page',$event)" v-slot="{row}">
    <td><RouterLink :to="'/hr/employees/'+row.employee_id">{{ row.employee }}</RouterLink></td>
    <td>{{ t(labels[row.kind] ?? row.kind) }}</td><td>{{ row.last_day }}</td><td>{{ row.reason }}<p v-if="row.note" class="muted">{{ row.note }}</p></td>
    <td>{{ t(labels[row.state] ?? row.state) }}</td><td><button v-if="row.can_approve" class="secondary"
      @click="decision = row; note = ''">{{ t('Review') }}</button></td>
  </DataTable>
  <form v-validated-form v-if="decision" class="panel record-form" @submit.prevent="decide(($event as SubmitEvent).submitter?.getAttribute('value') === 'reject' ? 'reject' : 'approve')">
    <h2>{{ t('Review') }} · {{ decision.employee }}</h2>
    <div class="field-wrap"><label>{{ t('Review note') }}<textarea data-field="note" data-required-action="reject" :placeholder="t('Add a short note')" v-model="note" maxlength="500" /></label><FieldHint :label="t('Review note')" /></div>
    <div class="actions"><button class="primary" :disabled="busy">{{ t('Approve') }}</button>
      <button class="secondary" value="reject" :disabled="busy">{{ t('Reject') }}</button>
      <button type="button" class="secondary" @click="decision = undefined">{{ t('Cancel') }}</button></div>
  </form>
</template>
