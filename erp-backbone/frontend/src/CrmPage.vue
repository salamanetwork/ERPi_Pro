<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, type Workspace } from './api'
import { locale, message, t } from './i18n'
import type { Activity, CustomerRequest, Lead, Opportunity, Page, Stage } from './crm'
import CrmKanban from './CrmKanban.vue'
import CrmLeadPage from './CrmLeadPage.vue'
import CrmOpportunityPage from './CrmOpportunityPage.vue'
import CrmActivityForm from './CrmActivityForm.vue'
import CrmSettings from './CrmSettings.vue'

const props=defineProps<{workspace:Workspace}>()
const route=useRoute()
const router=useRouter()
const section=computed(()=>String(route.params.section||'opportunities'))
const entry=computed(()=>String(route.params.entry||''))
const id=computed(()=>entry.value&&entry.value!=='new'?Number(entry.value):undefined)
const list=ref<Page<Lead|Opportunity|Activity|CustomerRequest>>()
const stages=ref<Stage[]>([])
const activity=ref<Activity>()
const request=ref<CustomerRequest>()
const editing=ref(false)
const reason=ref('')
const action=ref('approve')
const error=ref('')
const loading=ref(false)
const busy=ref(false)
const saved=ref(false)
const filters=ref<Record<string,string>>({})
const display=ref<'list'|'kanban'>('list')
const preferenceKey=()=> 'erp:crm-opportunity-view:'+props.workspace.username
onMounted(()=>{
  try{display.value=localStorage.getItem(preferenceKey())==='kanban'?'kanban':'list'}catch{/* keep list */}
})
function view(value:'list'|'kanban'){
  display.value=value
  try{localStorage.setItem(preferenceKey(),value)}catch{/* keep this session */}
}
const columns:Record<string,string>={
  leads:'Leads',opportunities:'Opportunities',activities:'Activities','customer-requests':'Customer requests',settings:'CRM settings',
}
const pages=Object.keys(columns)
const stageName=(stage:Stage)=>locale.value==='ar'?stage.name_ar:stage.name_en
const names:Record<string,string>={new:'New',qualified:'Qualified',converted:'Converted',disqualified:'Disqualified',
  open:'Open', completed:'Completed',cancelled:'Cancelled',pending:'Pending',approved:'Approved',rejected:'Rejected',
  call:'Call',meeting:'Meeting',task:'Task',note:'Note',won:'Won',lost:'Lost'}
const stateLabel=(state:string)=>t(names[state]??state)
const scope=(site:number)=>allows(props.workspace,'crm.manage',site)
const allowNew=computed(()=>props.workspace.sites.some(site=>site.active&&scope(site.id)&&allows(props.workspace,'crm.view',site.id)
  &&(section.value==='leads'||allows(props.workspace,'sales.view',site.id))))
const allowedKeys=['site','stage','status','currency','q','owner','lead','opportunity','state','page','page_size']
function routeFilters(){
  return Object.fromEntries(allowedKeys.flatMap(key=>typeof route.query[key]==='string'&&route.query[key]?[[key,route.query[key] as string]]:[]))
}
const kanbanFilters=computed(()=>Object.fromEntries(Object.entries(routeFilters()).filter(([key])=>key!=='page')))
let sequence=0
async function load(){
  const token=++sequence
  error.value=''
  loading.value=true
  try {
    if(!entry.value&&section.value!=='settings'){
      const rows=await api<Page<Lead|Opportunity|Activity|CustomerRequest>>('/crm/'+section.value+'?'+new URLSearchParams(routeFilters()))
      if(token===sequence)list.value=rows
      if(section.value==='opportunities'){
        const result=await api<{items:Stage[]}>('/crm/stages')
        if(token===sequence)stages.value=result.items
      }
    }
    if(id.value&&section.value==='activities'){
      const row=await api<Activity>('/crm/activities/'+id.value)
      if(token===sequence)activity.value=row
    }
    if(id.value&&section.value==='customer-requests'){
      const row=await api<CustomerRequest>('/crm/customer-requests/'+id.value)
      if(token===sequence)request.value=row
    }
  }catch(failure){if(token===sequence)error.value=message(failure)}
  finally{if(token===sequence)loading.value=false}
}
watch(()=>route.fullPath,()=>{
  filters.value=routeFilters()
  list.value=activity.value=request.value=undefined
  saved.value=editing.value=false
  reason.value=''
  action.value='approve'
  void load()
},{immediate:true})
function apply(page?:number){
  const selected=Object.fromEntries(Object.entries(filters.value).filter(([key,value])=>key!=='page'&&value))
  if(page)selected.page=String(page)
  void router.replace({path:'/crm/'+section.value,query:selected})
}
function rowTitle(row:Lead|Opportunity|Activity|CustomerRequest){
  return 'title' in row?row.title:row.name
}
async function mutate(work:()=>Promise<void>){
  if(busy.value)return
  busy.value=true;error.value='';saved.value=false
  try{await work();saved.value=true}catch(failure){error.value=message(failure)}
  finally{busy.value=false}
}
function updateActivity(next:'complete'|'cancel'){
  const row=activity.value
  if(!row)return
  return mutate(async()=>{activity.value=await api<Activity>('/crm/activities/'+row.id,'PATCH',{revision:row.revision,action:next})})
}
function decide(){
  const row=request.value
  if(!row)return
  return mutate(async()=>{
    request.value=await api<CustomerRequest>('/crm/customer-requests/'+row.id+'/decision','POST',
      {revision:row.revision,action:action.value,reason:reason.value})
  })
}
const date=(value:string|null)=>value?new Date(value).toLocaleString(locale.value):'—'
</script>

<template>
  <header class="page-heading"><h1>{{ t('CRM') }}</h1></header>
  <nav class="section-tabs" :aria-label="t('CRM')">
    <template v-for="key in pages" :key="key">
      <RouterLink v-if="key!=='settings'||allows(workspace,'crm.configure',null)" class="tab-link"
        :to="'/crm/'+key" :aria-current="section===key?'page':undefined">{{ t(columns[key]!) }}</RouterLink>
    </template>
  </nav>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="saved" class="success" role="status">{{ t('Changes saved.') }}</p>
  <p v-if="loading" role="status">{{ t('Loading') }}…</p>
  <CrmSettings v-if="section==='settings'&&allows(workspace,'crm.configure',null)" />
  <CrmLeadPage v-else-if="section==='leads'&&entry" :key="route.path" :workspace="workspace" :id="id" />
  <CrmOpportunityPage v-else-if="section==='opportunities'&&entry" :key="route.path" :workspace="workspace" :id="id" />
  <template v-else-if="activity">
    <RouterLink class="back-link" to="/crm/activities">‹ {{ t('Activities') }}</RouterLink>
    <section class="panel record-form crm-panel">
      <h2>{{ activity.number }} · {{ activity.title }}</h2>
      <p>{{ stateLabel(activity.kind) }} · {{ stateLabel(activity.state) }} · {{ activity.owner }}</p>
      <p>{{ t('Due date and time') }} · {{ date(activity.due_at) }}</p>
      <p v-if="activity.notes">{{ activity.notes }}</p>
      <RouterLink :to="activity.lead_id?'/crm/leads/'+activity.lead_id:'/crm/opportunities/'+activity.opportunity_id">{{ activity.parent }}</RouterLink>
      <div class="actions" v-if="activity.state==='open'">
        <button v-if="activity.owner===workspace.username||scope(activity.site_id)" class="primary" :disabled="busy" @click="updateActivity('complete')">{{ t('Complete activity') }}</button>
        <button v-if="scope(activity.site_id)" class="secondary" :disabled="busy" @click="editing=!editing">{{ t('Edit activity') }}</button>
        <button v-if="scope(activity.site_id)" class="secondary" :disabled="busy" @click="updateActivity('cancel')">{{ t('Cancel activity') }}</button>
      </div>
    </section>
    <CrmActivityForm v-if="editing" :key="activity.revision" :site="activity.site_id" :activity="activity" @cancel="editing=false" @saved="editing=false;load()" />
  </template>
  <template v-else-if="request">
    <RouterLink class="back-link" to="/crm/customer-requests">‹ {{ t('Customer requests') }}</RouterLink>
    <section class="panel record-form crm-panel">
      <h2>{{ request.number }} · {{ request.name }}</h2>
      <p>{{ stateLabel(request.state) }} · {{ request.requested_by }}</p>
      <p v-if="request.reason">{{ request.reason }}</p>
      <dl class="crm-summary">
        <div><dt>{{ t('Legal name') }}</dt><dd>{{ request.fields.legal_name || '—' }}</dd></div>
        <div><dt>{{ t('Contact') }}</dt><dd>{{ request.fields.contact || '—' }}</dd></div>
        <div><dt>{{ t('Tax ID') }}</dt><dd>{{ request.fields.tax_id || '—' }}</dd></div>
        <div><dt>{{ t('Billing address') }}</dt><dd>{{ request.fields.billing || '—' }}</dd></div>
        <div><dt>{{ t('Shipping address') }}</dt><dd>{{ request.fields.shipping || '—' }}</dd></div>
        <div><dt>{{ t('Payment terms (days)') }}</dt><dd>{{ request.fields.terms }}</dd></div>
      </dl>
      <RouterLink :to="'/crm/opportunities/'+request.opportunity_id">{{ t('Open opportunity') }}</RouterLink>
      <p v-if="request.customer_code"><bdi>{{ request.customer_code }}</bdi></p>
      <form v-if="request.can_decide" v-validated-form class="record-form" @submit.prevent="decide">
        <label>{{ t('Decision') }}<select v-model="action"><option value="approve">{{ t('Approve') }}</option><option value="reject">{{ t('Reject') }}</option></select></label>
        <label>{{ t('Reason') }}<input v-model="reason" data-field="reason" maxlength="500" :required="action==='reject'" :placeholder="t('Explain the reason')"></label>
        <button class="primary" :disabled="busy">{{ t('Save decision') }}</button>
      </form>
    </section>
  </template>
  <template v-else-if="!entry&&section!=='settings'">
    <div class="page-heading"><h2>{{ t(columns[section]!) }}</h2>
      <div class="actions">
        <template v-if="section==='opportunities'">
          <button type="button" class="secondary" :aria-pressed="display==='list'" @click="view('list')">{{ t('List view') }}</button>
          <button type="button" class="secondary" :aria-pressed="display==='kanban'" @click="view('kanban')">{{ t('Kanban view') }}</button>
        </template>
        <RouterLink v-if="allowNew&&['leads','opportunities'].includes(section)" class="button primary" :to="'/crm/'+section+'/new'">
          {{ t(section==='leads'?'New lead':'New opportunity') }}
        </RouterLink>
      </div>
    </div>
    <form v-if="['leads','opportunities','activities'].includes(section)" v-validated-form class="panel crm-filters" @submit.prevent="apply()">
      <label>{{ t('Search') }}<input v-model="filters.q" type="search" maxlength="120" :placeholder="t('Search by name or title')"></label>
      <label>{{ t('Site') }}<select v-model="filters.site"><option value="">{{ t('All accessible sites') }}</option>
        <option v-for="site in workspace.sites" :key="site.id" :value="String(site.id)">{{ site.name }}</option></select></label>
      <template v-if="section==='opportunities'">
        <label>{{ t('Stage') }}<select v-model="filters.stage"><option value="">{{ t('all') }}</option>
          <option v-for="stage in stages.filter(row=>row.active)" :key="stage.id" :value="String(stage.id)">{{ stageName(stage) }}</option></select></label>
        <label>{{ t('Status') }}<select v-model="filters.status"><option value="">{{ t('all') }}</option>
          <option v-for="state in ['open','won','lost']" :key="state" :value="state">{{ stateLabel(state) }}</option></select></label>
        <label>{{ t('Currency') }}<input v-model="filters.currency" maxlength="3" pattern="[A-Z]{3}" placeholder="AED"></label>
      </template>
      <button class="primary">{{ t('Apply filters') }}</button>
    </form>
    <CrmKanban v-if="section==='opportunities'&&display==='kanban'" :workspace="workspace" :stages="stages" :filters="kanbanFilters" />
    <template v-else-if="list">
      <div class="panel table-panel crm-section"><table>
        <thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Name') }}</th><th>{{ t('Status') }}</th>
          <th v-if="section==='opportunities'">{{ t('Estimated value') }}</th>
          <th v-else-if="section==='activities'">{{ t('Due date and time') }}</th>
          <th>{{ t('Site') }}</th></tr></thead>
        <tbody><tr v-for="row in list.results" :key="row.id">
          <td><RouterLink :to="'/crm/'+section+'/'+row.id">{{ row.number }}</RouterLink></td><td>{{ rowTitle(row) }}</td>
          <td>{{ 'stage' in row?stageName(row.stage):stateLabel(row.state) }}</td>
          <td v-if="'estimated_amount' in row"><bdi>{{ row.estimated_amount }} {{ row.currency }}</bdi></td>
          <td v-if="'due_at' in row">{{ date(row.due_at) }}</td>
          <td>{{ workspace.sites.find(site=>site.id===row.site_id)?.name }}</td></tr>
        </tbody>
      </table><p v-if="!list.results.length" class="empty">{{ t('No records found.') }}</p></div>
      <TableToolbar :page="list.page" :count="list.count" :size="Number(route.query.page_size)||25" @page="router.replace({query:{...route.query,page:String($event)}})" @size="router.replace({query:{...route.query,page_size:String($event),page:'1'}})"/>
    </template>
  </template>
</template>
