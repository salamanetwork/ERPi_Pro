<script setup lang="ts">
import { ref, watch } from 'vue'
import { api, allows, ApiError, type Workspace } from './api'
import { attendanceAction, duration, type AttendanceStatus, type Span } from './attendance'
import { message, t } from './i18n'

const props = defineProps<{ id: number; site: number; canStart: boolean; stage: string; workspace: Workspace }>()
type OrderTimers = { open_timer: Span | null; totals: { employee: string; seconds: number }[] }
const totals = ref<OrderTimers>()
const state = ref<AttendanceStatus>()
const failure = ref('')
const busy = ref(false)
const post = attendanceAction()
let sequence = 0

async function load() {
  const version = ++sequence
  const id = props.id
  try {
    const results = await Promise.all([
      api<OrderTimers>('/work-orders/' + id + '/timers'),
      props.canStart && allows(props.workspace, 'attendance.record', props.site)
        ? api<AttendanceStatus>('/attendance/me/status').catch(() => undefined) : Promise.resolve(undefined),
    ])
    if (version === sequence) [totals.value, state.value] = results
  } catch (error) { if (version === sequence) failure.value = message(error) }
}
watch(() => [props.id, props.stage], load, {immediate: true})

async function change() {
  if (busy.value || !totals.value) return
  const timer = totals.value.open_timer
  busy.value = true
  failure.value = ''
  try {
    await post('/work-orders/' + props.id + '/timers/' + (timer ? timer.id + '/stop' : 'start'),
      timer ? {} : {session_id: state.value?.session?.id})
    await load()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) await load()
    failure.value = message(error)
  } finally { busy.value = false }
}
</script>

<template>
  <section class="approval-section">
    <h3>{{ t('Work order time') }}</h3>
    <p v-if="failure" class="alert" role="alert">{{ t(failure) }}</p>
    <ul v-if="totals?.totals.length">
      <li v-for="(row, index) in totals.totals" :key="index">{{ row.employee }} · <bdi>{{ duration(row.seconds) }}</bdi></li>
    </ul>
    <div v-if="totals" class="actions">
      <button v-if="totals.open_timer && allows(workspace, 'attendance.record', site)" type="button" class="primary" :disabled="busy" @click="change">{{ t('Stop timer') }}</button>
      <button v-else-if="canStart && state?.session && !state.break && !state.timer && state.site_id === site"
        type="button" class="primary" :disabled="busy" @click="change">{{ t('Start timer') }}</button>
      <RouterLink v-else-if="canStart && allows(workspace, 'attendance.record', site)" class="button secondary" to="/hr/attendance">{{ t('Time & Attendance') }}</RouterLink>
    </div>
  </section>
</template>
