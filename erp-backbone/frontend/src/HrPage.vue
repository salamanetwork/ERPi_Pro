<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
const pg=ref(1),ps=ref(25),term=ref(''),total=ref(0)
function pagedQuery(){return '?'+new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value})}

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, allows, api, requestKey, type Entry, type Workspace } from './api'
import { locale, message, t } from './i18n'
import EmployeePersonal from './EmployeePersonal.vue'
import LinkedUserSelect from './LinkedUserSelect.vue'
import EmployeeList from './EmployeeList.vue'
import ReferenceSelect from './ReferenceSelect.vue'
import { useDirtyForm } from './useDirectory'

type Employee = { id:number; number:string; name:string; site_id:number; department:string; title:string; joined:string; ended:string|null; user_id:number|null; revision:number; own?:boolean; department_ref_id:number|null; title_ref_id:number|null; grade_ref_id:number|null; grade:string }
type Leave = { id:number; employee:string; site_id:number; start:string; end:string; paid:boolean; state:string; reason:string; decision:string; can_approve:boolean }
type Totals = { gross:string; deduction:string; net:string; paid:string; outstanding:string }
type Run = Totals & { id:number; site_id:number; month:string; state:string; revision:number; calculated:boolean; can_prepare:boolean; can_approve:boolean; can_post:boolean; can_reverse:boolean; can_pay:boolean; can_read_inputs:boolean; can_review_inputs:boolean; entry_id:number|null; reversal_id:number|null; review_note:string; payments:{amount:string;reference:string;posting_date:string}[]; issues?:{ employee:string; code:string }[]; preview?:Pick<Totals,'gross'|'deduction'|'net'> }
type Term = { id:number; effective:string; basic:string; allowance:string; center_id:number }
type Adjustment = { id:number; employee:string; kind:string; amount:string; reason:string; reference:string; approved:boolean }
type Slip = { id:number; number:string; name:string; month:string; state:string; gross:string; deduction:string; net:string }
type List<T> = { items:T[];count?:number }

const props = defineProps<{workspace: Workspace; area?: 'payroll'}>()
const route = useRoute()
const router = useRouter()
const can = (permission:string, site?:number) => allows(props.workspace,permission,site)
const has = (permissions:string[]) => permissions.some(permission => can(permission))
const isPayroll = computed(() => props.area === 'payroll')
const payrollEnabled = computed(() => props.workspace.modules.some(module => module.id === 'payroll' && module.enabled))
const heading = computed(() => isPayroll.value ? 'Payroll' : 'Human Resources')
const tabs = computed(() => [
  {id:'employees', label:'Employees', show:has(['hr.view','hr.manage','hr.self','hr.identity.view_full','hr.offboarding.request','hr.offboarding.approve']) || payrollEnabled.value && can('payroll.prepare')},
  {id:'leaves', label:'Leave requests', show:has(['hr.view','hr.self','hr.leave.request','hr.leave.approve','hr.manage'])},
  {id:'runs', label:'Payroll runs', show:has(['payroll.prepare','payroll.approve','payroll.post','payroll.pay','payroll.summary'])},
  {id:'payslips', label:'Payslips', show:has(['payroll.payslip','payroll.payslip.self'])},
].filter(tab => tab.show && (['runs','payslips'].includes(tab.id) === isPayroll.value)))
const section = computed(() => String(route.params.section || (isPayroll.value && selectedId.value ? 'runs' : tabs.value[0]?.id) || ''))
const selectedId = computed(() => Number(route.params.id) || 0)
const newEmployee = computed(() => section.value === 'employees' && route.params.id === 'new')
const optionsUnavailable = ref(false)
const options = ref<{automatic:boolean; references:Record<string,{id:number;code:string;name:string}[]>}>()
const backToEmployees = computed(() => ({path:'/hr/employees',query:route.query}))

const workers = ref<Employee[]>([])
const leaveRows = ref<Leave[]>([])
const runs = ref<Run[]>([])
const slips = ref<Slip[]>([])
const run = computed(() => runs.value.find(row => row.id === selectedId.value))
const preview = computed(() => breakdown.value?.preview ?? run.value?.preview)
const worker = computed(() => workers.value.find(row => row.id === selectedId.value))
const leave = computed(() => leaveRows.value.find(row => row.id === selectedId.value))
const terms = ref<Term[]>([])
const centers = ref<Entry[]>([])
const available = ref<number>()
const adjustments = ref<Adjustment[]>([])
const candidates = ref<Pick<Employee,'id'|'number'|'name'>[]>([])
const blankEmployee = () => ({revision:1,name:'',number:'',site_id:0,department:'',title:'',grade:'',department_ref_id:null as number|null,title_ref_id:null as number|null,grade_ref_id:null as number|null,joined:'',user_id:''})
const employeeForm = ref(blankEmployee())
const { reset: resetEmployeeForm } = useDirtyForm(() => {
  const {revision, ...fields} = employeeForm.value
  return section.value === 'employees' && (newEmployee.value || selectedId.value) ? fields : null
})
const canReadPersonal = computed(() => {
  const row = worker.value
  return !!row && (can('hr.manage', row.site_id) || can('hr.identity.view_full', row.site_id) ||
    (row.own === true && can('hr.self', row.site_id)))
})
const termForm = ref({effective:'',basic:'',allowance:'0.00',center_id:0})
const balanceForm = ref({day:'',days:0,reason:''})
const leaveForm = ref({employee_id:0,start:'',end:'',paid:true,reason:''})
const runForm = ref({site_id:0,month:''})
const inputForm = ref({employee_id:0,kind:'bonus',amount:'',reason:'',reference:''})
const actionForm = ref({posting_date:'',amount:'',reference:'',reason:''})
const breakdown = ref<Run>()
const busy = ref(false)
const loading = ref(false)
const failure = ref('')
const saved = ref('')
const money = ['gross','deduction','net','paid','outstanding'] as const
const amounts = {gross:'Gross salary',deduction:'Deductions',net:'Net salary',paid:'Paid amount',outstanding:'Outstanding'}
const errorLabels: Record<string,string> = {
  payroll_recalculate:'Recalculate the draft before continuing.',
  payroll_input_pending:'An input needs approval or its employee is outside the payroll month.',
  payroll_missing_terms:'Add effective compensation and an active site cost center.',
  payroll_manual_review_required:'Approve an adjustment or review for unpaid leave or a partial month.',
  payroll_negative_net:'Deductions exceed gross pay.',
  payroll_no_employees:'No eligible employees for this site and month.',
  payroll_zero_total:'Add positive gross pay before continuing.',
  payroll_duplicate_employee:'This employee already has payroll for the same month.',
  payroll_run_exists:'A payroll run already exists for this site and month.',
  hr_separate_reviewer:'Use an account other than the preparer.',
  hr_invalid_state:'Refresh the record and check its status.',
  hr_insufficient_leave:'There is insufficient paid leave balance.',
  hr_leave_overlap:'An existing request overlaps this date range.',
  hr_employee_link_fixed:'The linked account is locked after a payslip is created.',
  payroll_payment_reference:'This payment reference has already been recorded.',
  payroll_payment_exceeds:'The payment must be positive and within the outstanding balance.',
  payroll_reverse_paid:'A payroll run with recorded payments cannot be reversed.',
  payroll_period_unavailable:'Choose a posting date in the active AED ledger after cutover.',
}
function showError(error:unknown) {
  if (error instanceof ApiError && error.fieldsShown) return ''
  const code = error instanceof Error ? error.message : ''
  return errorLabels[code] ? t(errorLabels[code]) : message(error)
}
function siteName(id:number) { return props.workspace.sites.find(site => site.id === id)?.name ?? String(id) }
function filterSites(permission:string) { return props.workspace.sites.filter(site => site.active && can(permission,site.id)) }

let loadVersion = 0
async function refresh() {
  const version = ++loadVersion
  loading.value = true
  const id = selectedId.value
  try {
    if (section.value === 'employees') {
      if (!id && !newEmployee.value) return
      const row = id ? await api<Employee>('/hr/employees/' + id) : undefined
      if (version !== loadVersion) return
      workers.value = row ? [row] : []
      employeeForm.value = row ? {
        revision:row.revision,number:row.number,name:row.name,site_id:row.site_id,
        department:row.department,title:row.title,grade:row.grade,joined:row.joined,
        department_ref_id:row.department_ref_id,title_ref_id:row.title_ref_id,grade_ref_id:row.grade_ref_id,
        user_id:row.user_id ? String(row.user_id) : '',
      } : blankEmployee()
      resetEmployeeForm()
      if (row ? can('hr.manage', row.site_id) : can('hr.manage')) {
        try {
          const choices = await api<NonNullable<typeof options.value>>('/hr/employee-options')
          if (!choices?.references || typeof choices.automatic !== 'boolean') throw new Error('missing_employee_options')
          if (version !== loadVersion) return
          options.value = choices
          if (optionsUnavailable.value) failure.value = ''
          optionsUnavailable.value = false
        } catch {
          if (version === loadVersion) {
            optionsUnavailable.value = true
            failure.value = 'Could not load the employee form options. Retry to reload them.'
          }
          return
        }
      }
      if (!row) return
      if (payrollEnabled.value && can('payroll.prepare',row.site_id)) {
        const result = await api<List<Term> & {centers:Entry[]}>(`/hr/employees/${id}/compensation`)
        if (version !== loadVersion) return
        terms.value = result.items
        centers.value = result.centers
      }
      if (has(['hr.view','hr.self','hr.manage'])) {
        const result = await api<{available:number}>(`/hr/employees/${id}/leave-balance`).catch(() => undefined)
        if (version === loadVersion) available.value = result?.available
      }
    } else if (section.value === 'leaves') {
      const [results, employees] = await Promise.all([api<List<Leave>>('/hr/leaves'+(id?'':pagedQuery())),api<List<Employee>>('/hr/employees')])
      if (version !== loadVersion) return
      leaveRows.value = results.items;total.value=results.count??results.items.length
      workers.value = employees.items
    } else if (section.value === 'runs') {
      const result = await api<List<Run>>('/hr/payroll'+(id?'':pagedQuery()))
      if (version !== loadVersion) return
      runs.value = result.items;total.value=result.count??result.items.length
      const current = result.items.find(row => row.id === id)
      if (current && (current.can_read_inputs || current.can_review_inputs) && ['draft','submitted'].includes(current.state)) {
        const result = await api<List<Adjustment> & {employees:typeof candidates.value}>(`/hr/payroll/${id}/inputs`)
        if (version !== loadVersion) return
        adjustments.value = result.items
        candidates.value = result.employees
      }
    } else if (section.value === 'payslips') {
      const result = await api<List<Slip>>('/hr/payslips'+(id?'':pagedQuery()))
      if (version === loadVersion) slips.value = result.items;total.value=result.count??result.items.length
    }
  } catch (error) {
    if (version === loadVersion) failure.value = showError(error)
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

watch([pg,ps,term],refresh)
watch(() => route.path, () => {
  failure.value = ''
  saved.value = ''
  options.value = undefined
  optionsUnavailable.value = false
  workers.value = []
  employeeForm.value = blankEmployee()
  resetEmployeeForm()
  breakdown.value = undefined
  available.value = undefined
  adjustments.value = []
  centers.value = []
  terms.value = []
  actionForm.value = {posting_date:'',amount:'',reference:'',reason:''}
  void refresh()
}, {immediate:true})

let pendingKey = ''
let pendingPayload = ''
async function save<T>(path:string, payload:object, method='POST', done?:(value:T) => Promise<void> | void) {
  if (busy.value) return
  busy.value = true
  failure.value = ''
  saved.value = ''
  const signature = JSON.stringify([path,payload])
  if (pendingPayload !== signature) {
    pendingPayload = signature
    pendingKey = requestKey()
  }
  try {
    const result = await api<T>(path,method,payload,pendingKey)
    pendingPayload = ''
    if (done) await done(result)
    await refresh()
    saved.value = /\/hr\/employees(\/\d+)?$/.test(path) ? 'Employee saved successfully.' : 'Changes saved.'
  } catch (error) {
    failure.value = showError(error)
  } finally {
    busy.value = false
  }
}

function saveWorker() {
  const {user_id,department,title,grade,...profile} = employeeForm.value
  const payload = {...profile,...(can('hr.identity.link',profile.site_id) ? {user_id:user_id ? Number(user_id) : null} : {})}
  if (worker.value && payload.department_ref_id === null) delete (payload as Partial<typeof payload>).department_ref_id
  if (worker.value && payload.title_ref_id === null) delete (payload as Partial<typeof payload>).title_ref_id
  if (worker.value) return save<Employee>(`/hr/employees/${worker.value.id}`,payload,'PATCH',resetEmployeeForm)
  return save<Employee>('/hr/employees',payload,'POST',async () => {
    resetEmployeeForm()
    await router.push(backToEmployees.value)
  })
}

function createRun() {
  return save<Run>('/hr/payroll',{...runForm.value,month:runForm.value.month + '-01'},'POST',
    async row => {await router.push(`/payroll/runs/${row.id}`)})
}
function inputAction(action:string,id?:number) {
  return save(`/hr/payroll/${selectedId.value}/inputs`,action === 'add' ? {action,...inputForm.value} : {action,id},'POST',
    () => {breakdown.value = undefined})
}
function advance(action:string) {
  if (!run.value) return
  return save<Run>(`/hr/payroll/${run.value.id}/action`,{action,revision:run.value.revision,...actionForm.value},'POST',
    row => {
      breakdown.value = action === 'calculate' ? row : undefined
      if (action === 'pay') {
        actionForm.value.amount = ''
        actionForm.value.reference = ''
      }
    })
}
import DateInput from './DateInput.vue'
</script>

<template>
  <header class="page-heading"><h1>{{ t(heading) }}</h1></header>
  <nav class="section-tabs" :aria-label="t(heading)">
    <RouterLink class="tab-link" v-for="tab in tabs" :key="tab.id" :aria-current="section === tab.id ? 'page' : undefined" :to="(isPayroll ? '/payroll/' : '/hr/') + tab.id">{{ t(tab.label) }}</RouterLink>
  </nav>
  <p v-if="failure" class="notice error" role="alert">{{ t(failure) }}</p>
  <button v-if="optionsUnavailable" type="button" class="secondary" :disabled="loading" @click="refresh">{{ t('Retry') }}</button>
  <p v-if="saved" class="notice success" role="status">{{ t(saved) }}</p>

  <template v-if="section === 'employees'">
    <EmployeeList v-if="!selectedId && !newEmployee" :workspace="workspace" />
    <RouterLink v-if="selectedId || newEmployee" class="back-link" :to="backToEmployees">‹ {{ t('Employees') }}</RouterLink>
    <RouterLink v-if="worker && (can('hr.offboarding.request',worker.site_id) || can('hr.offboarding.approve',worker.site_id))"
      class="button secondary" :to="{path:'/hr/offboarding',query:{employee:String(worker.id)}}">{{ t('Offboarding') }}</RouterLink>
    <p v-if="worker?.ended">{{ t('Last employment day') }} · {{ worker.ended }}</p>
    <p v-if="worker && !can('hr.manage',worker.site_id)">{{ worker.number }} · {{ worker.name }} · {{ siteName(worker.site_id) }} · {{ worker.department }} · {{ worker.title }} · {{ worker.joined }} <template v-if="worker.ended">– {{ worker.ended }}</template></p>
    <form v-validated-form class="panel hr-card aligned-form" v-if="options && (worker ? can('hr.manage',worker.site_id) : newEmployee && can('hr.manage'))" @submit.prevent="saveWorker">
      <h3>{{ t(worker ? 'Edit employee' : 'New employee') }}</h3>
      <div v-if="worker || !options.automatic" class="field-wrap"><label>{{ t('Employee number') }}<input data-field="number" :placeholder="t('Enter {field}').replace('{field}', t('Employee number'))" v-model="employeeForm.number" required maxlength="40" :readonly="!!worker"></label><FieldHint :label="t('Employee number')" required :readonly="!!worker" /></div>
      <p v-else class="muted form-wide">{{ t('Employee number is assigned when you save.') }}</p>
      <div class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="employeeForm.name" required maxlength="150" dir="auto"></label><FieldHint :label="t('Name')" required /></div>
      <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site_id" v-model="employeeForm.site_id" @change="employeeForm.user_id = ''" required :disabled="!!worker"><option disabled :value="0">{{ t('Select site') }}</option><option v-for="site in filterSites('hr.manage')" :key="site.id" :value="site.id">{{ site.name }}</option></select></label><FieldHint :label="t('Site')" required /></div>
      <ReferenceSelect data-field="department_ref_id" :label="t('Department')" v-model="employeeForm.department_ref_id" :choices="options.references.departments ?? []" :current-label="employeeForm.department" :required="!worker || Boolean(worker.department_ref_id)" />
      <ReferenceSelect data-field="title_ref_id" :label="t('Job title')" v-model="employeeForm.title_ref_id" :choices="options.references['job-titles'] ?? []" :current-label="employeeForm.title" :required="!worker || Boolean(worker.title_ref_id)" />
      <ReferenceSelect data-field="grade_ref_id" :label="t('Grade')" v-model="employeeForm.grade_ref_id" :choices="options.references.grades ?? []" :current-label="employeeForm.grade" />
      <DateInput data-field="joined" :label="t('Hire date')" v-model="employeeForm.joined" type="date" required />
      <LinkedUserSelect data-field="user_id" class="form-wide" v-if="employeeForm.site_id && can('hr.identity.link', employeeForm.site_id)"
        v-model="employeeForm.user_id" :site="employeeForm.site_id" :employee="worker?.id" :disabled="busy" />
      <div class="actions form-actions"><button class="primary" :disabled="busy">{{ t('Save employee') }}</button>
        <RouterLink class="button secondary" :to="backToEmployees">{{ t('Cancel') }}</RouterLink></div>
    </form>
    <EmployeePersonal v-if="worker && canReadPersonal" :key="worker.id" :employee="worker.id" :revision="worker.revision" :allowed="canReadPersonal"
      @changed="worker.revision = $event; employeeForm.revision = $event" />
    <form v-validated-form v-if="worker && payrollEnabled && can('payroll.prepare',worker.site_id)" class="panel hr-card" @submit.prevent="save('/hr/employees/' + selectedId + '/compensation',termForm)">
      <h3>{{ t('Compensation history') }}</h3>
      <ul v-if="terms.length"><li v-for="term in terms" :key="term.id">{{ term.effective }} · {{ term.basic }} + {{ term.allowance }} AED · {{ centers.find(c => c.id === term.center_id)?.name ?? term.center_id }}</li></ul>
      <DateInput data-field="effective" :label="t('Effective date')" v-model="termForm.effective" type="date" required />
      <EntityAutocomplete
        data-field="center_id"
        :label="t('Cost center')"
        lookup="cost_centers"
        :params="{ site_id: worker?.site_id }"
        :choices="centers"
        :placeholder="t('Select cost center')"
        :empty-value="0"
        required
        v-model="termForm.center_id"
      />
      <div class="field-wrap"><label>{{ t('Basic salary') }}<input data-field="basic" :placeholder="t('e.g., 125.50')" v-model="termForm.basic" inputmode="decimal" required></label><FieldHint :label="t('Basic salary')" required /></div>
      <div class="field-wrap"><label>{{ t('Allowances') }}<input data-field="allowance" :placeholder="t('e.g., 125.50')" v-model="termForm.allowance" inputmode="decimal" required></label><FieldHint :label="t('Allowances')" required /></div>
      <button class="primary" :disabled="busy || !centers.length">{{ t('Save compensation') }}</button>
    </form>
    <p v-if="available !== undefined">{{ t('Available paid leave days') }} · {{ available }}</p>
    <form v-validated-form v-if="worker && can('hr.manage',worker.site_id)" class="panel hr-card" @submit.prevent="save('/hr/employees/' + selectedId + '/leave-balance',balanceForm)">
      <h3>{{ t('Adjust leave balance') }}</h3>
      <DateInput data-field="day" :label="t('Effective date')" v-model="balanceForm.day" type="date" required />
      <div class="field-wrap"><label>{{ t('Days') }}<input data-field="days" :placeholder="t('Enter {field}').replace('{field}', t('Days'))" v-model.number="balanceForm.days" type="number" min="-366" max="366" required></label><FieldHint :label="t('Days')" required /></div>
      <div class="field-wrap"><label>{{ t('Reason') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="balanceForm.reason" required maxlength="500"></label><FieldHint :label="t('Reason')" required /></div>
      <button class="primary" :disabled="busy">{{ t('Save adjustment') }}</button>
    </form>
  </template>

  <template v-if="section === 'leaves'">
    <h2>{{ t('Leave requests') }}</h2>
    <div class="table-wrap" v-if="!selectedId && leaveRows.length"><TableToolbar v-if="!selectedId" :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table>
      <thead><tr><th>{{ t('Employee') }}</th><th>{{ t('Site') }}</th><th>{{ t('Date range') }}</th><th>{{ t('Status') }}</th></tr></thead>
      <tbody><tr v-for="row in leaveRows" :key="row.id"><td><RouterLink :to="'/hr/leaves/' + row.id">{{ row.employee }}</RouterLink></td><td>{{ siteName(row.site_id) }}</td><td>{{ row.start }} – {{ row.end }}</td><td>{{ t(row.state) }}</td></tr></tbody>
    </table></div>
    <section class="panel hr-card" v-if="leave">
      <h3>{{ leave.employee }}</h3>
      <p>{{ leave.start }} – {{ leave.end }} · {{ t(leave.paid ? 'Paid leave' : 'Unpaid leave') }} · {{ t(leave.state) }}</p>
      <p>{{ leave.reason }}</p><p v-if="leave.decision">{{ leave.decision }}</p>
      <form v-validated-form v-if="leave.state === 'pending' && leave.can_approve" @submit.prevent="save('/hr/leaves/' + selectedId,{action:'approve',reason:actionForm.reason})">
        <div class="field-wrap"><label>{{ t('Review note') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="actionForm.reason" maxlength="500"></label><FieldHint :label="t('Review note')" /></div>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Approve') }}</button><button type="button" :disabled="busy || !actionForm.reason.trim()" @click="save('/hr/leaves/' + selectedId,{action:'reject',reason:actionForm.reason})">{{ t('Reject') }}</button></div>
      </form>
    </section>
    <form v-validated-form v-if="!selectedId && has(['hr.leave.request','hr.self'])" class="panel hr-card" @submit.prevent="save('/hr/leaves',leaveForm)">
      <h3>{{ t('Request leave') }}</h3>
      <EntityAutocomplete
        data-field="employee_id"
        :label="t('Employee')"
        lookup="employees"
        :choices="workers"
        :placeholder="t('Select employee')"
        :empty-value="0"
        required
        v-model="leaveForm.employee_id"
      />
      <DateInput data-field="start" :label="t('Start date')" v-model="leaveForm.start" type="date" required />
      <DateInput data-field="end" :label="t('End date')" v-model="leaveForm.end" type="date" required />
      <div class="field-wrap"><label>{{ t('Leave type') }}<select data-field="paid" v-model="leaveForm.paid"><option :value="true">{{ t('Paid leave') }}</option><option :value="false">{{ t('Unpaid leave') }}</option></select></label><FieldHint :label="t('Leave type')" /></div>
      <div class="field-wrap"><label>{{ t('Reason') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="leaveForm.reason" required maxlength="500"></label><FieldHint :label="t('Reason')" required /></div>
      <button class="primary" :disabled="busy">{{ t('Send request') }}</button>
    </form>
  </template>

  <template v-if="section === 'runs'">
    <h2>{{ t('Payroll runs') }}</h2>
    <p class="muted">{{ t('Parallel monthly payroll · AED') }}</p>
    <div class="table-wrap" v-if="!selectedId && runs.length"><TableToolbar v-if="!selectedId" :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table>
      <thead><tr><th>{{ t('Payroll run') }}</th><th>{{ t('Site') }}</th><th>{{ t('Month') }}</th><th>{{ t('Status') }}</th><th>{{ t('Net salary') }}</th></tr></thead>
      <tbody><tr v-for="row in runs" :key="row.id"><td><RouterLink :to="'/payroll/runs/' + row.id">PAY-{{ row.id }}</RouterLink></td><td>{{ siteName(row.site_id) }}</td><td>{{ row.month.slice(0,7) }}</td><td>{{ t(row.state) }}</td><td>{{ row.net }} AED</td></tr></tbody>
    </table></div>
    <form v-validated-form v-if="!selectedId && can('payroll.prepare')" class="panel hr-card" @submit.prevent="createRun">
      <h3>{{ t('New payroll run') }}</h3>
      <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site_id" v-model="runForm.site_id" required><option disabled :value="0">{{ t('Select site') }}</option><option v-for="site in filterSites('payroll.prepare')" :key="site.id" :value="site.id">{{ site.name }}</option></select></label><FieldHint :label="t('Site')" required /></div>
      <div class="field-wrap"><label>{{ t('Month') }}<input data-field="month" v-model="runForm.month" type="month" required></label><FieldHint :label="t('Month')" required /></div>
      <button class="primary" :disabled="busy">{{ t('Create draft') }}</button>
    </form>
    <template v-if="run">
      <section class="panel hr-card">
        <h3>PAY-{{ run.id }} · {{ siteName(run.site_id) }} · {{ run.month.slice(0,7) }} · {{ t(run.state) }}</h3>
        <dl v-if="run.state === 'approved' || run.state === 'posted' || run.state === 'reversed'">
          <template v-for="key in money" :key="key"><dt>{{ t(amounts[key]) }}</dt><dd>{{ run[key] }} AED</dd></template>
        </dl>
        <p v-if="run.review_note">{{ t('Review note') }} · {{ run.review_note }}</p>
        <p v-if="run.entry_id">{{ t('Journal entry') }} #{{ run.entry_id }}<span v-if="run.reversal_id"> · {{ t('Reversal') }} #{{ run.reversal_id }}</span></p>
        <ul v-if="run.payments.length"><li v-for="payment in run.payments" :key="payment.reference">{{ payment.posting_date }} · {{ payment.amount }} AED · {{ payment.reference }}</li></ul>
        <template v-if="preview">
          <h4>{{ t('Calculation preview') }}</h4>
          <p>{{ t('Gross salary') }} {{ preview.gross }} − {{ t('Deductions') }} {{ preview.deduction }} = {{ t('Net salary') }} {{ preview.net }} AED</p>
          <ul v-if="breakdown?.issues?.length" role="alert"><li v-for="(issue,index) in breakdown.issues" :key="index">{{ issue.employee }} {{ t(errorLabels[issue.code] ?? issue.code) }}</li></ul>
        </template>
        <div v-if="run.state === 'draft' && run.can_prepare" class="actions">
          <button type="button" :disabled="busy" @click="advance('calculate')">{{ t('Calculate payroll') }}</button>
          <button type="button" class="primary" :disabled="busy || !run.calculated" @click="advance('submit')">{{ t('Submit payroll') }}</button>
        </div>
        <form v-validated-form v-if="run.state === 'submitted' && run.can_approve" @submit.prevent="advance('approve')">
          <div class="field-wrap"><label>{{ t('Review note') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="actionForm.reason" maxlength="500"></label><FieldHint :label="t('Review note')" /></div>
          <div class="actions"><button class="primary" :disabled="busy">{{ t('Approve payroll') }}</button><button type="button" :disabled="busy || !actionForm.reason.trim()" @click="advance('return')">{{ t('Return for changes') }}</button></div>
        </form>
        <p v-if="run.state === 'approved' && !run.can_post" class="muted">{{ t('Posting requires payroll.post for this site.') }}</p>
        <form v-validated-form v-if="run.state === 'approved' && run.can_post" @submit.prevent="advance('post')">
          <DateInput data-field="posting_date" :label="t('Posting date')" v-model="actionForm.posting_date" type="date" required />
          <button class="primary" :disabled="busy">{{ t('Post payroll') }}</button>
        </form>
        <p v-if="run.state === 'posted' && run.outstanding === '0.00'" role="status">{{ t('No payment outstanding.') }}</p>
        <form v-validated-form v-if="run.state === 'posted' && run.can_pay && run.outstanding !== '0.00'" @submit.prevent="advance('pay')">
          <h4>{{ t('Record payment') }}</h4>
          <DateInput data-field="posting_date" :label="t('Payment date')" v-model="actionForm.posting_date" type="date" required />
          <div class="field-wrap"><label>{{ t('Amount') }}<input data-field="amount" :placeholder="t('e.g., 125.50')" v-model="actionForm.amount" inputmode="decimal" required></label><FieldHint :label="t('Amount')" required /></div>
          <div class="field-wrap"><label>{{ t('Bank reference') }}<input data-field="reference" :placeholder="t('e.g., REF-001')" v-model="actionForm.reference" maxlength="100" required></label><FieldHint :label="t('Bank reference')" required /></div>
          <div class="actions">
            <button type="button" :disabled="busy" @click="actionForm.amount = run.outstanding">{{ t('Use outstanding amount') }}</button>
            <button class="primary" :disabled="busy">{{ t('Confirm payment') }}</button>
          </div>
        </form>
        <form v-validated-form v-if="run.state === 'posted' && run.can_reverse && run.paid === '0.00'" @submit.prevent="advance('reverse')">
          <DateInput data-field="posting_date" :label="t('Reversal date')" v-model="actionForm.posting_date" type="date" required />
          <div class="field-wrap"><label>{{ t('Reason') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="actionForm.reason" maxlength="500" required></label><FieldHint :label="t('Reason')" required /></div>
          <button :disabled="busy">{{ t('Reverse payroll') }}</button>
        </form>
      </section>
      <section v-if="['draft','submitted'].includes(run.state) && (run.can_read_inputs || run.can_review_inputs)" class="panel hr-card">
        <h3>{{ t('Payroll inputs') }}</h3>
        <div class="table-wrap" v-if="adjustments.length"><table>
          <thead><tr><th>{{ t('Employee') }}</th><th>{{ t('Type') }}</th><th>{{ t('Amount') }}</th><th>{{ t('Reference') }} / {{ t('Reason') }}</th><th>{{ t('Status') }}</th><th>{{ t('Actions') }}</th></tr></thead>
          <tbody><tr v-for="row in adjustments" :key="row.id">
            <td>{{ row.employee }}</td><td>{{ t(row.kind) }}</td><td>{{ row.amount }}</td><td>{{ row.reference }} · {{ row.reason }}</td><td>{{ t(row.approved ? 'approved' : 'pending') }}</td>
            <td><button v-if="run.state === 'draft' && run.can_approve && !row.approved" :disabled="busy" @click="inputAction('approve',row.id)">{{ t('Approve') }}</button><button v-if="run.state === 'draft' && run.can_prepare" :disabled="busy" @click="inputAction('remove',row.id)">{{ t('Remove') }}</button></td>
          </tr></tbody>
        </table></div>
        <form v-validated-form v-if="run.state === 'draft' && run.can_prepare" @submit.prevent="inputAction('add')">
          <div class="field-wrap"><label>{{ t('Employee') }}<EntityAutocomplete v-model="inputForm.employee_id" lookup="employees" :site-id="run.site_id" :local-items="candidates.map(row => ({ id: row.id, label: row.number + ' · ' + row.name }))" :empty-value="0" :placeholder="t('Select employee')" data-field="employee_id" required /></label><FieldHint :label="t('Employee')" required /></div>
          <div class="field-wrap"><label>{{ t('Type') }}<select data-field="kind" v-model="inputForm.kind"><option v-for="kind in ['bonus','extra','deduction','adjustment','review']" :key="kind" :value="kind">{{ t(kind) }}</option></select></label><FieldHint :label="t('Type')" /></div>
          <div class="field-wrap"><label>{{ t('Amount') }}<input data-field="amount" :placeholder="t('e.g., 125.50')" v-model="inputForm.amount" inputmode="decimal" required></label><FieldHint :label="t('Amount')" required /></div>
          <div class="field-wrap"><label>{{ t('Reference') }}<input data-field="reference" :placeholder="t('e.g., REF-001')" v-model="inputForm.reference" required maxlength="100"></label><FieldHint :label="t('Reference')" required /></div>
          <div class="field-wrap"><label>{{ t('Reason') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="inputForm.reason" required maxlength="500"></label><FieldHint :label="t('Reason')" required /></div>
          <button :disabled="busy">{{ t('Add payroll input') }}</button>
        </form>
      </section>
    </template>
  </template>

  <template v-if="section === 'payslips'">
    <h2>{{ t('Payslips') }}</h2>
    <div class="table-wrap" v-if="slips.length"><TableToolbar v-if="!selectedId" :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table>
      <thead><tr><th>{{ t('Employee') }}</th><th>{{ t('Month') }}</th><th>{{ t('Status') }}</th><th>{{ t('Net salary') }}</th><th>{{ t('Actions') }}</th></tr></thead>
      <tbody><tr v-for="row in slips" :key="row.id"><td>{{ row.number }} · {{ row.name }}</td><td>{{ row.month.slice(0,7) }}</td><td>{{ t(row.state) }}</td><td>{{ row.net }} AED</td><td><a :href="'/hr/print/payslip/' + row.id + '?language=' + locale" target="_blank" rel="noopener">{{ t('Print or save PDF') }}</a></td></tr></tbody>
    </table></div>
    <p v-else-if="!loading">{{ t('No payslips available.') }}</p>
  </template>
  <p v-if="loading" role="status">{{ t('Loading…') }}</p>
  <p v-if="!loading && selectedId && !worker && !leave && !run">{{ t('Record not found.') }}</p>
</template>

<style scoped>
.hr-links { margin-block: 1rem; }
.hr-card { max-width: 70rem; margin-block: 1rem; }
.hr-card form { margin-block: 1rem; }
.hr-card label { display:block; margin-block: .65rem; max-width: 38rem; }
.hr-card.aligned-form .field-wrap > label { display: grid; margin: 0; max-width: none; }
.hr-card dt { font-weight: 600; }
.hr-card dd { margin: 0 0 .55rem; }
</style>
