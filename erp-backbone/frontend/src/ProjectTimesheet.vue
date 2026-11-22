<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, createRequest } from './api'
import { duration } from './attendance'
import { message, t } from './i18n'

export type Allocation = {id:number; project:string; task:string; task_id:number; project_id:number; timer_id:number|null; start:string|null; end:string|null; seconds:number}
type Options = {status:string;tasks:{id:number;name:string}[];timers:{id:number;order_id:number;seconds:number}[]}
const props=defineProps<{id:number;revision:number;enabled:boolean;rows:Allocation[]}>()
const emit=defineEmits<{changed:[]}>()
const options=ref<Options>()
const task=ref<number>()
const timer=ref<number>()
const start=ref('')
const end=ref('')
const error=ref('')
const busy=ref(false)
const loading=ref(false)
const editing=ref<number>()
const mode=ref('manual')
const request=createRequest()
const statusText:Record<string,string>={
  locked:'Project time can be changed only while this timesheet is a draft.',
  not_owner:'Only the employee can allocate project time on this timesheet.',
  permission:'Ask your administrator for project time and attendance recording access at this site.',
  no_projects:'You need active membership in an active project at this site.',
  no_tasks:'Add a task to your active project before recording time.',
}
const timers=computed(()=>options.value?.timers.filter(row=>!props.rows.some(item=>item.timer_id===row.id&&item.id!==editing.value))??[])
const ready=computed(()=>props.enabled&&options.value?.status==='ready')
let sequence=0
async function load(){
  const token=++sequence
  options.value=undefined
  error.value=''
  if(!props.enabled)return
  loading.value=true
  try{
    const result=await api<Options>('/projects/timesheets/'+props.id)
    if(token!==sequence)return
    options.value=result
    if(!editing.value)mode.value=timers.value.length?'timer':'manual'
  }catch(failure){
    if(token===sequence)error.value=message(failure)
  }finally{
    if(token===sequence)loading.value=false
  }
}
watch(()=>[props.id,props.enabled,props.revision],()=>void load(),{immediate:true})
function dateInput(value:string|null){
  if(!value)return ''
  const stamp=new Date(value)
  return new Date(stamp.getTime()-stamp.getTimezoneOffset()*60000).toISOString().slice(0,16)
}
function clearForm(){
  editing.value=task.value=timer.value=undefined
  start.value=end.value=''
  mode.value=timers.value.length?'timer':'manual'
}
function edit(row:Allocation){
  editing.value=row.id
  task.value=row.task_id
  mode.value=row.timer_id?'timer':'manual'
  timer.value=row.timer_id??undefined
  start.value=dateInput(row.start)
  end.value=dateInput(row.end)
}
async function save(remove?:number){
  if(busy.value||!ready.value)return
  busy.value=true
  error.value=''
  try{
    const patch=remove?{id:remove,action:'remove'}:{id:editing.value,task_id:task.value,
      timer_id:mode.value==='timer'?timer.value:undefined,
      start:mode.value==='manual'?new Date(start.value).toISOString():undefined,
      end:mode.value==='manual'?new Date(end.value).toISOString():undefined}
    await request('/projects/timesheets/'+props.id,{revision:props.revision,...patch})
    clearForm()
    emit('changed')
  }catch(failure){error.value=message(failure)}
  finally{busy.value=false}
}
</script>
<template>
  <section class="project-time record-form" aria-labelledby="project-time-title">
    <h3 id="project-time-title">{{ t('Project time allocations') }}</h3>
    <p v-if="!enabled" class="muted">{{ t('Enable Projects in Module Manager to allocate project time.') }}</p>
    <p v-else-if="loading" class="muted" role="status">{{ t('Loading project time options…') }}</p>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-else-if="options&&!ready" class="muted">{{ t(statusText[options.status]) }}</p>
    <ul v-if="rows.length"><li v-for="row in rows" :key="row.id">
      {{ row.project }} · {{ row.task }} · {{ duration(row.seconds) }}
      <span v-if="row.timer_id"> · {{ t('Work order timer') }} #{{ row.timer_id }}</span>
      <template v-if="ready">
        <button type="button" class="secondary" @click="edit(row)">{{ t('Edit') }}</button>
        <button type="button" class="secondary" :disabled="busy" @click="save(row.id)">{{ t('Remove') }}</button>
      </template>
    </li></ul>
    <form v-if="ready" v-validated-form class="record-form" @submit.prevent="save()">
      <div class="allocation-fields">
        <label>{{ t('Project task') }}<select v-model.number="task" data-field="task_id" required>
          <option :value="undefined" disabled>{{ t('Select a project task') }}</option>
          <option v-for="row in options?.tasks" :key="row.id" :value="row.id">{{ row.name }}</option>
        </select></label>
        <label>{{ t('Time source') }}<select v-model="mode">
          <option value="timer" :disabled="!timers.length">{{ t('Work order timer') }}</option>
          <option value="manual">{{ t('Manual project time') }}</option>
        </select></label>
        <label v-if="mode==='timer'">{{ t('Work order timer') }}<select v-model.number="timer" data-field="timer_id" required>
          <option :value="undefined" disabled>{{ t('Select a work order timer') }}</option>
          <option v-for="row in timers" :key="row.id" :value="row.id">WO-{{ row.order_id }} · #{{ row.id }} · {{ duration(row.seconds) }}</option>
        </select></label>
        <template v-else>
          <label>{{ t('Start time') }}<input v-model="start" type="datetime-local" required data-field="start"></label>
          <label>{{ t('End time') }}<input v-model="end" type="datetime-local" required data-field="end"></label>
        </template>
      </div>
      <p v-if="mode==='manual'" class="muted">{{ t('Manual times use your device time zone.') }}</p>
      <div class="actions">
        <button class="primary" :disabled="busy">{{ t('Save project time') }}</button>
        <button v-if="editing" type="button" class="secondary" @click="clearForm">{{ t('Cancel') }}</button>
      </div>
    </form>
    <button v-if="enabled&&error&&!options" type="button" class="secondary" @click="load">{{ t('Retry') }}</button>
  </section>
</template>
<style scoped>
.project-time {padding:1rem;border:1px solid var(--border);border-radius:.75rem;background:var(--subtle)}
.allocation-fields {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr));gap:.8rem}
</style>
