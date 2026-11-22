<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { allows, api, type Workspace } from './api'
import { locale, message, t } from './i18n'
import type { Opportunity, Page, Stage } from './crm'

const props = defineProps<{workspace:Workspace;stages:Stage[];filters:Record<string,string>}>()
const columns = ref<Record<number,Page<Opportunity>>>({})
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const dragging = ref<number>()
const over = ref<number>()
const change = ref<{row:Opportunity;stageId:number}>()
const reason = ref('')
let sequence = 0
const name = (stage:Stage) => locale.value === 'ar' ? stage.name_ar : stage.name_en
const displayed = computed(() => props.stages.filter(stage => stage.active &&
  (!props.filters.stage || String(stage.id) === props.filters.stage) &&
  (!props.filters.status || stage.kind === props.filters.status)))
const canMove = (row:Opportunity) => row.stage.kind==='open' && allows(props.workspace,'crm.manage',row.site_id)
const filterKey = computed(() => new URLSearchParams(props.filters).toString())
function url(stage:number,page=1) {
  return '/crm/opportunities?' + new URLSearchParams({...props.filters,stage:String(stage),page_size:'20',page:String(page)})
}
async function load() {
  const token = ++sequence
  error.value = ''
  loading.value = true
  try {
    const selected = displayed.value
    const pages = await Promise.all(selected.map(stage=>api<Page<Opportunity>>(url(stage.id))))
    if (token === sequence) columns.value = Object.fromEntries(selected.map((stage,index)=>[stage.id,pages[index]!]))
  } catch (failure) { if (token===sequence) error.value=message(failure) }
  finally { if (token===sequence) loading.value=false }
}
watch([filterKey,()=>displayed.value.map(stage=>stage.id).join(',')],()=>{columns.value={};void load()},{immediate:true})
async function more(stage:number) {
  const old = columns.value[stage]
  if (!old?.next || busy.value) return
  const token = sequence
  busy.value = true
  try {
    const next = await api<Page<Opportunity>>(url(stage,old.page+1))
    if (token===sequence) columns.value[stage]={...next,results:[...old.results,...next.results.filter(row=>!old.results.some(prior=>prior.id===row.id))]}
  } catch(failure) { error.value=message(failure) }
  finally { busy.value=false }
}
function start(event:DragEvent,row:Opportunity) {
  if (!canMove(row) || busy.value || !event.dataTransfer) { event.preventDefault(); return }
  dragging.value=row.id
  event.dataTransfer.setData('text/plain',String(row.id))
  event.dataTransfer.effectAllowed='move'
}
function stop() {dragging.value=over.value=undefined}
function enter(event:DragEvent,stage:Stage) {
  if (dragging.value===undefined || busy.value) return
  event.preventDefault()
  if(event.dataTransfer) event.dataTransfer.dropEffect='move'
  over.value=stage.id
}
function drop(event:DragEvent,stage:Stage) {
  event.preventDefault()
  const row = Object.values(columns.value).flatMap(page=>page.results).find(row=>row.id===dragging.value)
  stop()
  if (!row || !canMove(row) || stage.id===row.stage_id) return
  if (stage.kind==='lost') { openMove(row,stage.id); return }
  void move(row,stage.id,'')
}
function openMove(row:Opportunity,stageId=0) {
  reason.value=''
  change.value={row,stageId}
}
async function move(row:Opportunity,stageId:number,note:string) {
  if(busy.value || !stageId || stageId===row.stage_id) return
  busy.value=true
  error.value=''
  try {
    await api('/crm/opportunities/'+row.id+'/transition','POST',{revision:row.revision,stage_id:stageId,reason:note})
    change.value=undefined
    await load()
  } catch(failure) { error.value=message(failure) }
  finally {busy.value=false}
}
const stageNeedsReason=computed(()=>props.stages.find(stage=>stage.id===change.value?.stageId)?.kind==='lost')
</script>

<template>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="loading" role="status">{{ t('Loading') }}…</p>
  <form v-if="change" v-validated-form class="panel record-form crm-panel" @submit.prevent="move(change.row,change.stageId,reason)">
    <h2>{{ t('Move opportunity') }} · {{ change.row.number }}</h2>
    <label>{{ t('Stage') }}<select v-model="change.stageId" data-field="stage_id" required>
      <option :value="0" disabled>{{ t('Select stage') }}</option>
      <option v-for="stage in stages.filter(stage=>stage.active&&stage.id!==change!.row.stage_id)" :key="stage.id" :value="stage.id">{{ name(stage) }}</option>
    </select></label>
    <label>{{ t('Reason') }}<input v-model="reason" data-field="reason" :required="stageNeedsReason" maxlength="500" :placeholder="t('Explain the reason')"></label>
    <div class="actions"><button class="primary" :disabled="busy">{{ t('Move') }}</button>
      <button class="secondary" type="button" @click="change=undefined">{{ t('Cancel') }}</button></div>
  </form>
  <div class="crm-board" :aria-label="t('Kanban view')">
    <section v-for="stage in displayed" :key="stage.id" :data-stage="stage.code" class="crm-lane"
      :class="{'crm-lane-over':over===stage.id}" @dragover="enter($event,stage)" @drop="drop($event,stage)">
      <header><h2>{{ name(stage) }}</h2><span class="badge">{{ columns[stage.id]?.count ?? 0 }}</span></header>
      <article v-for="row in columns[stage.id]?.results" :key="row.id" class="panel crm-card"
        :draggable="canMove(row)" @dragstart="start($event,row)" @dragend="stop" :data-opportunity="row.id">
        <RouterLink :to="'/crm/opportunities/'+row.id">{{ row.title }}</RouterLink>
        <small><bdi>{{ row.number }}</bdi> · {{ row.owner }}</small>
        <strong><bdi>{{ row.estimated_amount }} {{ row.currency }}</bdi></strong>
        <button v-if="canMove(row)" class="secondary" type="button" :disabled="busy" @click="openMove(row)">{{ t('Move opportunity') }}</button>
      </article>
      <p v-if="columns[stage.id]&&!columns[stage.id]!.results.length" class="muted">{{ t('No opportunities in this stage.') }}</p>
      <button v-if="columns[stage.id]?.next" class="secondary" :disabled="busy" type="button" @click="more(stage.id)">{{ t('Load more') }}</button>
    </section>
  </div>
</template>
