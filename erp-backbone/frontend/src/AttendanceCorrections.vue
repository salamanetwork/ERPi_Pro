<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPage} from 'vue'
const pg=ref(1),ps=ref(25),rowCount=ref(0),term=ref('')
const paging=()=>new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value})

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { attendanceAction, duration, timestamp, type Span } from './attendance'
import { locale, message, t } from './i18n'

type Session = Span & { breaks: Span[]; timers: Span[] }
type Correction = {
  id: number; employee: string; timezone: string; session_id: number; state: string; revision: number
  reason: string; review_note: string; original: Session; proposed: Session
  original_net_seconds: number | null; proposed_net_seconds: number; net_delta_seconds: number | null; can_review: boolean
}
const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id || 0))
const session = computed(() => Number(route.params.sessionId || 0))
const row = ref<Correction>()
const rows = ref<Correction[]>([])
const details = ref<Session & {timezone: string}>()
const failure = ref('')
const busy = ref(false)
const reason = ref('')
const forms = ref<{ id?: number; started_at: string; ended_at: string; start_offset: string; end_offset: string }[]>([])
const post = attendanceAction()
let sequence = 0

function form(id?: number) {
  return {id, started_at: '', ended_at: '', start_offset: '', end_offset: ''}
}
function show(value: string | null, zone: string) {
  return value ? timestamp(value, zone, locale.value) : t('Open')
}
watchPage([pg,ps,term],()=>load().catch(e=>failure.value=message(e)))
async function load() {
  const version = ++sequence
  failure.value = reason.value = ''
  row.value = undefined
  details.value = undefined
  try {
    if (session.value) {
      const result = await api<Session & {timezone: string}>('/attendance/sessions/' + session.value + '/corrections')
      if (version !== sequence) return
      details.value = result
      forms.value = [form(), ...result.breaks.map(pause => form(pause.id))]
    } else if (id.value) {
      const result = await api<Correction>('/attendance/corrections/' + id.value)
      if (version === sequence) row.value = result
    } else {
      const result = await api<{items: Correction[];count:number}>('/attendance/corrections?'+paging())
      if (version === sequence) {rows.value=result.items;rowCount.value=result.count}
    }
  } catch (error) { if (version === sequence) failure.value = message(error) }
}
watch(() => route.path, load, {immediate: true})
function changes(entry: typeof forms.value[number]) {
  return {
    ...(entry.started_at ? {started_at: entry.started_at + entry.start_offset.trim()} : {}),
    ...(entry.ended_at ? {ended_at: entry.ended_at + entry.end_offset.trim()} : {}),
  }
}
const changedBreaks = computed(() => forms.value.slice(1).filter(entry => entry.started_at || entry.ended_at))
function correctionField(entry: typeof forms.value[number], field: string) {
  if (!entry.id) return field
  const index = changedBreaks.value.indexOf(entry)
  return index < 0 ? undefined : `breaks.${index}.${field}`
}
async function request() {
  if (!forms.value[0] || busy.value) return
  busy.value = true
  failure.value = ''
  try {
    const breaks = changedBreaks.value.map(entry => ({id: entry.id, ...changes(entry)}))
    const result = await post<Correction>('/attendance/sessions/' + session.value + '/corrections',
      { ...changes(forms.value[0]), breaks, reason: reason.value })
    await router.push('/hr/attendance/corrections/' + result.id)
  } catch (error) { failure.value = message(error) }
  finally { busy.value = false }
}
async function decide(action: string) {
  if (!row.value || busy.value) return
  const currentId = row.value.id
  busy.value = true
  failure.value = ''
  try {
    const result = await api<Correction>('/attendance/corrections/' + currentId + '/decision', 'POST',
      {revision: row.value.revision, action, reason: reason.value})
    if (currentId === id.value) row.value = result
  } catch (error) { failure.value = message(error) }
  finally { busy.value = false }
}
function total(value: number | null) {
  return value === null ? '—' : (value < 0 ? '−' : '') + duration(Math.abs(value))
}
import DateInput from './DateInput.vue'
</script>

<template>
  <div class="page-heading">
    <h1>{{ t(session ? 'Request correction' : 'Attendance corrections') }}<template v-if="row"> · TC-{{ row.id }}</template></h1>
    <RouterLink class="button secondary" to="/hr/attendance">{{ t('Time & Attendance') }}</RouterLink>
    <RouterLink v-if="row || session" class="button secondary" to="/hr/attendance/corrections">{{ t('Attendance corrections') }}</RouterLink>
  </div>
  <p v-if="failure" role="alert" class="alert">{{ t(failure) }}</p>
  <form v-validated-form v-if="session && details" class="panel record-form" @submit.prevent="request">
    <p>{{ details.timezone }} · {{ t('Enter only the times you want to change.') }}</p>
    <p>{{ t('For a repeated local hour, enter the UTC offset, such as -04:00.') }}</p>
    <fieldset v-for="entry in forms" :key="entry.id || 0" class="record-form purchase-line">
      <legend>{{ entry.id ? t('Break time') + ' #' + entry.id : t('Attendance time') }}</legend>
      <p v-if="!entry.id">{{ show(details.started_at, details.timezone) }} → {{ show(details.ended_at, details.timezone) }}</p>
      <p v-else-if="details.breaks.find(pause => pause.id === entry.id)">
        {{ show(details.breaks.find(pause => pause.id === entry.id)!.started_at, details.timezone) }} →
        {{ show(details.breaks.find(pause => pause.id === entry.id)!.ended_at, details.timezone) }}
      </p>
      <DateInput :data-field="correctionField(entry, 'started_at')" :label="t('Proposed start')" type="datetime-local" v-model="entry.started_at" />
      <div class="field-wrap"><label>{{ t('Start UTC offset') }}<input data-field="start_offset" v-model="entry.start_offset" placeholder="+04:00" maxlength="6"></label><FieldHint :label="t('Start UTC offset')" /></div>
      <DateInput :data-field="correctionField(entry, 'ended_at')" :label="t('Proposed end')" type="datetime-local" v-model="entry.ended_at" />
      <div class="field-wrap"><label>{{ t('End UTC offset') }}<input data-field="end_offset" v-model="entry.end_offset" placeholder="+04:00" maxlength="6"></label><FieldHint :label="t('End UTC offset')" /></div>
    </fieldset>
    <div class="field-wrap"><label>{{ t('Reason') }}<textarea data-field="reason" :placeholder="t('Explain the reason')" v-model="reason" maxlength="500" required></textarea></label><FieldHint :label="t('Reason')" required /></div>
    <button class="primary" :disabled="busy">{{ t('Request correction') }}</button>
  </form>
  <section v-else-if="row" class="panel record-form">
    <h2>{{ row.employee }} · {{ t(row.state) }}</h2>
    <p>{{ t('Reason') }} · {{ row.reason }}</p>
    <div class="table-wrap"><table>
      <thead><tr><th>{{ t('Entry') }}</th><th>{{ t('Original') }}</th><th>{{ t('Proposed') }}</th></tr></thead>
      <tbody>
        <tr><th>{{ t('Attendance time') }}</th><td>{{ show(row.original.started_at, row.timezone) }} → {{ show(row.original.ended_at, row.timezone) }}</td>
          <td>{{ show(row.proposed.started_at, row.timezone) }} → {{ show(row.proposed.ended_at, row.timezone) }}</td></tr>
        <tr v-for="(pause, i) in row.original.breaks" :key="pause.id"><th>{{ t('Break time') }} #{{ pause.id }}</th>
          <td>{{ show(pause.started_at, row.timezone) }} → {{ show(pause.ended_at, row.timezone) }}</td>
          <td>{{ show(row.proposed.breaks[i]!.started_at, row.timezone) }} → {{ show(row.proposed.breaks[i]!.ended_at, row.timezone) }}</td></tr>
        <tr><th>{{ t('Net work time') }}</th><td><bdi>{{ total(row.original_net_seconds) }}</bdi></td><td><bdi>{{ total(row.proposed_net_seconds) }}</bdi></td></tr>
      </tbody>
    </table></div>
    <p>{{ t('Net time difference') }} · <bdi>{{ total(row.net_delta_seconds) }}</bdi></p>
    <p v-if="row.review_note">{{ t('Review note') }} · {{ row.review_note }}</p>
    <form v-validated-form v-if="row.can_review" class="actions" @submit.prevent="decide(($event as SubmitEvent).submitter?.getAttribute('value') === 'reject' ? 'reject' : 'approve')">
      <div class="field-wrap"><label>{{ t('Review note') }}<input data-field="reason" data-required-action="reject" :placeholder="t('Explain the reason')" v-model="reason" maxlength="500"></label><FieldHint :label="t('Review note')" /></div>
      <button class="primary" :disabled="busy">{{ t('Approve correction') }}</button>
      <button value="reject" class="secondary" :disabled="busy">{{ t('Reject correction') }}</button>
    </form>
  </section>
  <section v-else-if="!session && !id" class="panel record-form">
    <TableToolbar :page="pg" :size="ps" :count="rowCount" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><ul v-if="rows.length">
      <li v-for="item in rows" :key="item.id"><RouterLink :to="'/hr/attendance/corrections/' + item.id">TC-{{ item.id }}</RouterLink>
        · {{ item.employee }} · {{ t(item.state) }} · {{ item.reason }}</li>
    </ul>
    <p v-else>{{ t('No attendance corrections.') }}</p>
  </section>
</template>
