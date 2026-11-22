<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPage} from 'vue'
const pg=ref(1),ps=ref(25),total=ref(0),term=ref('')
const paging=()=>new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value})

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { allows, api, ApiError, type Workspace } from './api'
import { attendanceAction, duration, timestamp, type AttendanceStatus, type Span, type Timer } from './attendance'
import { locale, message, t } from './i18n'
import ProjectTimesheet, { type Allocation } from './ProjectTimesheet.vue'

type Sheet = {
  id: number; employee: string; site_id: number; week_start: string; timezone: string; ends_at: string
  standard_minutes: number; state: string; revision: number; review_note: string
}
const quantities = {
  seconds: 'Attendance time', break_seconds: 'Break time', net_seconds: 'Net work time',
  wo_seconds: 'Work order time', unassigned_seconds: 'Unallocated time', eligible_overtime_seconds: 'Eligible overtime',
} as const
type Totals = Record<keyof typeof quantities, number>
type DailyDecision = {minutes: number; reason: string}
type Decisions = Record<string, DailyDecision>
type Overtime = {requests: Decisions; decisions: Decisions}
type Version = { revision: number; approved_by: string | null; approved_at: string | null; reopened_by: string;
  reopened_at: string; reason: string; snapshot: Detail['details'] & {overtime?: Overtime} }
type Detail = Sheet & { can_submit: boolean; can_review: boolean; can_record: boolean; can_reopen: boolean;
  project_time?: Allocation[]; correction_pending: boolean; overtime_requests: Decisions; overtime_reviewed: boolean | null; history: Version[]; details: {
  overtime?: Overtime
  totals: Totals; open_session: boolean; leave_conflict: boolean
  days: (Totals & {day: string; has_leave: boolean; leave_conflict: boolean})[]
  sessions: (Span & {breaks: Span[]; timers: (Timer & {seconds: number})[]})[]
} }
type Policy = { week_start: number; standard_minutes: number; timezone: string; revision: number }
const props = defineProps<{ workspace: Workspace }>()
const route = useRoute()
const record = computed(() => allows(props.workspace, 'attendance.record'))
const review = computed(() => allows(props.workspace, 'attendance.approve') || allows(props.workspace, 'attendance.reopen'))
const correctionsAllowed = computed(() => record.value || allows(props.workspace, 'attendance.correct'))
const sites = computed(() => props.workspace.sites.filter(site => site.active && allows(props.workspace, 'attendance.setup', site.id)))
const id = computed(() => Number(route.params.id || 0))
const sheets = ref<Sheet[]>([])
const current = ref<Detail>()
const requested = ref<Decisions>({})
const approved = ref<Decisions>({})
function setDetail(value: Detail) {
  current.value = value
  requested.value = Object.fromEntries(value.details.days.map(day => [
    day.day, {...(value.overtime_requests[day.day] ?? {minutes: 0, reason: ''})},
  ]))
  approved.value = Object.fromEntries(Object.entries(value.overtime_requests).map(([day, item]) =>
    [day, {minutes: item.minutes, reason: ''}]))
}
const presence = ref<AttendanceStatus>()
const busy = ref(false)
const failure = ref('')
const presenceError = ref('')
const reason = ref('')
const site = ref<number>()
const settings = ref<Policy>()
const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const weekFilter = ref('')
const leaveWeek = ref('')
const statusFilter = ref('')
const siteFilter = ref<number>()
const post = attendanceAction()
let loadId = 0

watchPage([pg,ps,term],()=>load().catch(e=>failure.value=message(e)))
async function load() {
  const sequence = ++loadId
  failure.value = presenceError.value = ''
  if (record.value && !id.value) {
    try {
      const status = await api<AttendanceStatus>('/attendance/me/status')
      if (sequence === loadId) presence.value = status
    } catch (error) { if (sequence === loadId) presenceError.value = message(error) }
  }
  if (!record.value && !review.value) return
  try {
    if (id.value) {
      const result = await api<Detail>('/attendance/timesheets/' + id.value)
      if (sequence === loadId) setDetail(result)
    } else {
      const query=paging()
      if (weekFilter.value) query.set('week_start', weekFilter.value)
      if (siteFilter.value) query.set('site_id', String(siteFilter.value))
      if (statusFilter.value) query.set('status', statusFilter.value)
      const result = await api<{items: Sheet[];count:number}>('/attendance/timesheets?' + query)
      if (sequence === loadId) {sheets.value=result.items;total.value=result.count}
    }
  } catch (error) { if (sequence === loadId) failure.value = message(error) }
}
watch(id, () => {
  current.value = undefined
  reason.value = ''
  void load()
}, {immediate: true})

async function act(operation: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  failure.value = ''
  try { await operation() } catch (error) { failure.value = message(error) }
  finally { busy.value = false }
}
async function punch(path: string) {
  return act(async () => {
    const session = presence.value?.session
    try {
      await post('/attendance/' + path, path === 'check-in' ? {} : {session_id: session?.id})
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) await load()
      throw error
    }
    await load()
  })
}
async function prepareLeaveWeek() {
  return act(async () => {
    const row = await post<Sheet>('/attendance/timesheets', {week_start: leaveWeek.value})
    weekFilter.value = row.week_start
    statusFilter.value = ''
    siteFilter.value = undefined
    await load()
  })
}
async function transition(action: string) {
  const row = current.value
  if (!row) return
  return act(async () => {
    const overtime = action === 'approve' ? approved.value :
      Object.fromEntries(Object.entries(requested.value).filter(([, entry]) => entry.minutes !== 0))
    const result = await api<Detail>('/attendance/timesheets/' + row.id + '/' + action, 'POST',
      {revision: row.revision, reason: reason.value,
        ...(['submit', 'approve', 'overtime'].includes(action) ? {overtime} : {})})
    if (id.value === row.id) setDetail(result)
    reason.value = ''
  })
}
async function loadPolicy() {
  const selected = site.value
  settings.value = undefined
  if (!selected) return
  try {
    const result = await api<Policy>('/attendance/sites/' + selected + '/settings')
    if (selected === site.value) settings.value = result
  } catch (error) { if (selected === site.value) failure.value = message(error) }
}
async function savePolicy() {
  return act(async () => {
    if (site.value && settings.value) settings.value = await api<Policy>('/attendance/sites/' + site.value + '/settings', 'PATCH', settings.value)
  })
}
const showTime = (value: string, zone: string) => timestamp(value, zone, locale.value)
function siteName(siteId: number) { return props.workspace.sites.find(site => site.id === siteId)?.name ?? String(siteId) }
import DateInput from './DateInput.vue'
</script>

<template>
  <div class="page-heading">
    <h1>{{ t(id ? 'Weekly timesheet' : 'Time & Attendance') }}</h1>
    <RouterLink v-if="id" class="button secondary" to="/hr/attendance">{{ t('Time & Attendance') }}</RouterLink>
    <RouterLink v-if="correctionsAllowed" class="button secondary" to="/hr/attendance/corrections">{{ t('Attendance corrections') }}</RouterLink>
    <button class="secondary" :disabled="busy" @click="load">{{ t('Refresh') }}</button>
  </div>
  <p v-if="failure" class="alert" role="alert">{{ t(failure) }}</p>
  <p v-if="presenceError && !id" class="alert" role="alert">{{ t(presenceError) }}</p>
  <section v-if="!id && presence" class="panel record-form">
    <h2>{{ t('Attendance') }} · {{ siteName(presence.site_id) }}</h2>
    <p>{{ presence.timezone }}</p>
    <div v-if="presence.session">
      <p>{{ t('Checked in') }} · {{ showTime(presence.session.started_at, presence.timezone) }}</p>
      <RouterLink :to="'/hr/attendance/sessions/' + presence.session.id + '/correction'">{{ t('Request correction') }}</RouterLink>
      <p v-if="presence.break">{{ t('On break') }} · {{ showTime(presence.break.started_at, presence.timezone) }}</p>
      <p v-if="presence.timer">
        {{ t('Active work order timer') }} · <RouterLink :to="'/work-orders/' + presence.timer.order_id">WO-{{ presence.timer.order_id }}</RouterLink>
      </p>
    </div>
    <div class="actions">
      <button v-if="!presence.session" class="primary" :disabled="busy" @click="punch('check-in')">{{ t('Check in') }}</button>
      <template v-else>
        <button class="secondary" :disabled="busy" @click="punch(presence.break ? 'breaks/end' : 'breaks/start')">
          {{ t(presence.break ? 'End break' : 'Start break') }}
        </button>
        <button class="primary" :disabled="busy" @click="punch('check-out')">{{ t('Check out') }}</button>
      </template>
    </div>
  </section>
  <section v-if="!id && sites.length" class="panel record-form approval-section">
    <h2>{{ t('Attendance settings') }}</h2>
    <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site" v-model="site" :disabled="busy" @change="loadPolicy">
      <option :value="undefined">{{ t('Select site') }}</option>
      <option v-for="row in sites" :key="row.id" :value="row.id">{{ row.name }}</option>
    </select></label><FieldHint :label="t('Site')" /></div>
    <form v-validated-form v-if="settings" class="record-form" @submit.prevent="savePolicy">
      <p>{{ t('Time zone') }} · {{ settings.timezone }}</p>
      <div class="field-wrap"><label>{{ t('Week starts on') }}<select data-field="week_start" v-model="settings.week_start">
        <option v-for="(day, index) in weekdays" :key="day" :value="index">{{ t(day) }}</option>
      </select></label><FieldHint :label="t('Week starts on')" /></div>
      <div class="field-wrap"><label>{{ t('Standard daily minutes') }}<input data-field="standard_minutes" :placeholder="t('Enter {field}').replace('{field}', t('Standard daily minutes'))" type="number" v-model.number="settings.standard_minutes" min="1" max="1440" required></label><FieldHint :label="t('Standard daily minutes')" required /></div>
      <p>{{ t('Standard daily hours') }} · <bdi>{{ duration(settings.standard_minutes * 60) }}</bdi></p>
      <button class="primary" :disabled="busy">{{ t('Save') }}</button>
    </form>
  </section>
  <section v-if="!id && (record || review)" class="panel record-form approval-section">
    <h2>{{ t('Weekly timesheets') }}</h2><TableToolbar :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/>
    <form v-validated-form class="actions" @submit.prevent="load">
      <DateInput data-field="weekFilter" :label="t('Week start')" v-model="weekFilter" type="date" />
      <div class="field-wrap"><label>{{ t('Site') }}<select data-field="siteFilter" v-model="siteFilter"><option :value="undefined">{{ t('all') }}</option>
        <option v-for="row in workspace.sites" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><FieldHint :label="t('Site')" /></div>
      <div class="field-wrap"><label>{{ t('Status') }}<select data-field="statusFilter" v-model="statusFilter"><option value="">{{ t('all') }}</option>
        <option v-for="state in ['draft','submitted','approved']" :key="state" :value="state">{{ t(state) }}</option></select></label><FieldHint :label="t('Status')" /></div>
      <button class="secondary">{{ t('Apply filters') }}</button>
    </form>
    <form v-validated-form v-if="record" class="actions" @submit.prevent="prepareLeaveWeek">
      <DateInput data-field="leaveWeek" :label="t('Leave-only week start')" v-model="leaveWeek" type="date" required />
      <button class="secondary" :disabled="busy">{{ t('Prepare leave week') }}</button>
    </form>
    <div class="table-wrap">
      <table>
        <thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Employee') }}</th><th>{{ t('Site') }}</th>
          <th>{{ t('Week start') }}</th><th>{{ t('Status') }}</th></tr></thead>
        <tbody><tr v-for="row in sheets" :key="row.id">
          <td><RouterLink :to="'/hr/attendance/' + row.id">TS-{{ row.id }}</RouterLink></td>
          <td>{{ row.employee }}</td><td>{{ siteName(row.site_id) }}</td><td>{{ row.week_start }}</td><td>{{ t(row.state) }}</td>
        </tr></tbody>
      </table>
      <p v-if="!sheets.length">{{ t('No timesheets yet.') }}</p>
    </div>
  </section>
  <section v-if="current" class="panel record-form">
    <h2>TS-{{ current.id }} · {{ current.employee }} · {{ t(current.state) }}</h2>
    <p>{{ siteName(current.site_id) }} · {{ current.week_start }} · {{ current.timezone }}</p>
    <p>{{ t('Week ends at') }} · {{ showTime(current.ends_at, current.timezone) }} · {{ t('Standard daily hours') }} <bdi>{{ duration(current.standard_minutes * 60) }}</bdi></p>
    <p v-if="current.details.open_session" class="alert">{{ t('Close the open attendance session before submitting.') }}</p>
    <p v-if="current.details.leave_conflict" class="alert">{{ t('Recorded attendance overlaps approved leave.') }}</p>
    <p v-if="current.correction_pending" class="alert">{{ t('A correction is pending for this week.') }}</p>
    <p v-if="current.overtime_reviewed === false">{{ t('Overtime was not reviewed.') }}</p>
    <p v-if="current.review_note">{{ t('Review note') }} · {{ current.review_note }}</p>
    <ProjectTimesheet :key="current.id" :id="current.id" :revision="current.revision"
      :enabled="workspace.modules.some(module=>module.id==='projects'&&module.enabled)" :rows="current.project_time??[]" @changed="load()" />
    <div class="table-wrap"><table>
      <thead><tr><th>{{ t('Date') }}</th><th v-for="label in quantities" :key="label">{{ t(label) }}</th><th>{{ t('Leave') }}</th></tr></thead>
      <tbody><tr v-for="day in current.details.days" :key="day.day"><td>{{ day.day }}</td>
        <td v-for="(_, field) in quantities" :key="field"><bdi>{{ duration(day[field]) }}</bdi></td>
        <td>{{ day.leave_conflict ? t('Conflict') : day.has_leave ? t('Approved leave') : '—' }}</td>
      </tr></tbody>
      <tfoot><tr><th>{{ t('Total') }}</th><td v-for="(_, field) in quantities" :key="field"><bdi>{{ duration(current.details.totals[field]) }}</bdi></td><td></td></tr></tfoot>
    </table></div>
    <ul v-if="current.details.sessions.length">
      <li v-for="row in current.details.sessions" :key="row.id">
        {{ showTime(row.started_at, current.timezone) }} → {{ row.ended_at ? showTime(row.ended_at, current.timezone) : t('Open') }}
        <RouterLink v-if="current.can_record" :to="'/hr/attendance/sessions/' + row.id + '/correction'">{{ t('Request correction') }}</RouterLink>
        <ul>
          <li v-for="pause in row.breaks" :key="'break-' + pause.id">
            {{ t('Break time') }} · {{ showTime(pause.started_at, current.timezone) }} → {{ pause.ended_at ? showTime(pause.ended_at, current.timezone) : t('Open') }}
          </li>
          <li v-for="timer in row.timers" :key="'timer-' + timer.id">
            <RouterLink :to="'/work-orders/' + timer.order_id">WO-{{ timer.order_id }}</RouterLink> · <bdi>{{ duration(timer.seconds) }}</bdi>
          </li>
        </ul>
      </li>
    </ul>
    <section v-if="current.state === 'draft' && current.can_record || Object.keys(current.overtime_requests).length" class="record-form">
      <h3>{{ t('Overtime requests') }}</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>{{ t('Date') }}</th><th>{{ t('Eligible minutes') }}</th><th>{{ t('Requested minutes') }}</th>
          <th>{{ t('Reason') }}</th><th>{{ t('Approved minutes') }}</th><th>{{ t('Decision reason') }}</th></tr></thead>
        <tbody><tr v-for="day in current.details.days.filter(day => day.eligible_overtime_seconds >= 60 || current!.overtime_requests[day.day])" :key="day.day">
          <td>{{ day.day }}</td><td>{{ Math.floor(day.eligible_overtime_seconds / 60) }}</td>
          <td><div class="field-wrap table-field" v-if="current.state === 'draft' && current.can_record"><input :placeholder="t('Enter a value')" type="number" min="0" step="1"
            :max="Math.floor(day.eligible_overtime_seconds / 60)" v-model.number="requested[day.day]!.minutes" :aria-label="t('Requested minutes') + ' ' + day.day"><FieldHint :label="t('Requested minutes')" /></div>
            <template v-else>{{ current.overtime_requests[day.day]?.minutes ?? '—' }}</template></td>
          <td><div class="field-wrap table-field" v-if="current.state === 'draft' && current.can_record"><input :placeholder="t('Explain the reason')" v-model="requested[day.day]!.reason" maxlength="500" :aria-label="t('Reason') + ' ' + day.day"><FieldHint :label="t('Reason')" /></div>
            <template v-else>{{ current.overtime_requests[day.day]?.reason ?? '—' }}</template></td>
          <td><div class="field-wrap table-field" v-if="current.can_review && approved[day.day]"><input :placeholder="t('Enter a value')" type="number" min="0" step="1"
            :max="current.overtime_requests[day.day]?.minutes" v-model.number="approved[day.day]!.minutes" :aria-label="t('Approved minutes') + ' ' + day.day"><FieldHint :label="t('Approved minutes')" /></div>
            <template v-else>{{ current.details.overtime?.decisions[day.day]?.minutes ?? '—' }}</template></td>
          <td><div class="field-wrap table-field" v-if="current.can_review && approved[day.day]"><input :placeholder="t('Explain the reason')" v-model="approved[day.day]!.reason" maxlength="500" :aria-label="t('Decision reason') + ' ' + day.day"><FieldHint :label="t('Decision reason')" /></div>
            <template v-else>{{ current.details.overtime?.decisions[day.day]?.reason || '—' }}</template></td>
        </tr></tbody>
      </table></div>
      <button v-if="current.state === 'draft' && current.can_record" class="secondary" :disabled="busy" @click="transition('overtime')">{{ t('Save overtime requests') }}</button>
    </section>
    <button v-if="current.can_submit" class="primary" :disabled="busy || current.correction_pending || current.details.open_session || current.details.leave_conflict"
      @click="transition('submit')">{{ t('Submit timesheet') }}</button>
    <form v-validated-form v-if="current.can_review" class="actions" @submit.prevent="transition(($event as SubmitEvent).submitter?.getAttribute('value') === 'return' ? 'return' : 'approve')">
      <div class="field-wrap"><label>{{ t('Review note') }}<input data-field="reason" data-required-action="return" :placeholder="t('Explain the reason')" v-model="reason" maxlength="500"></label><FieldHint :label="t('Review note')" /></div>
      <button class="primary" :disabled="busy">{{ t('Approve timesheet') }}</button>
      <button value="return" class="secondary" :disabled="busy">{{ t('Return for changes') }}</button>
    </form>
    <form v-validated-form v-if="current.can_reopen" class="actions" @submit.prevent="transition('reopen')">
      <div class="field-wrap"><label>{{ t('Reopen reason') }}<input data-field="reason" required :placeholder="t('Explain the reason')" v-model="reason" maxlength="500"></label><FieldHint :label="t('Reopen reason')" /></div>
      <button class="secondary" :disabled="busy">{{ t('Reopen timesheet') }}</button>
    </form>
    <section v-if="current.history.length" class="record-form">
      <h3>{{ t('Approval history') }}</h3>
      <details v-for="version in current.history" :key="version.revision">
        <summary>{{ t('Version') }} {{ version.revision }} · {{ version.approved_by || '—' }} · {{ t('Net work time') }} {{ duration(version.snapshot.totals.net_seconds) }}</summary>
        <p v-if="version.approved_at">{{ t('Approved') }} · {{ showTime(version.approved_at, current.timezone) }}</p>
        <p>{{ t('Reopened by') }} {{ version.reopened_by }} · {{ showTime(version.reopened_at, current.timezone) }} · {{ version.reason }}</p>
        <p v-if="!version.snapshot.overtime">{{ t('Overtime was not reviewed.') }}</p>
        <div class="table-wrap"><table>
          <thead><tr><th>{{ t('Date') }}</th><th>{{ t('Net work time') }}</th><th>{{ t('Work order time') }}</th>
            <th>{{ t('Requested minutes') }}</th><th>{{ t('Approved minutes') }}</th><th>{{ t('Decision reason') }}</th></tr></thead>
          <tbody><tr v-for="day in version.snapshot.days" :key="day.day"><td>{{ day.day }}</td><td>{{ duration(day.net_seconds) }}</td>
            <td>{{ duration(day.wo_seconds) }}</td><td>{{ version.snapshot.overtime?.requests[day.day]?.minutes ?? '—' }}</td>
            <td>{{ version.snapshot.overtime?.decisions[day.day]?.minutes ?? '—' }}</td>
            <td>{{ version.snapshot.overtime?.decisions[day.day]?.reason || '—' }}</td></tr></tbody>
        </table></div>
      </details>
    </section>
  </section>
</template>
