<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { allows, api, createRequest, type Workspace } from './api'
import { message, t } from './i18n'
import type { Lead, Opportunity, Owner } from './crm'
import CrmActivityForm from './CrmActivityForm.vue'

const props=defineProps<{workspace:Workspace;id?:number}>()
const router=useRouter()
const lead=ref<Lead>()
const blank=()=>({name:'',company:'',email:'',phone:'',source:'',site_id:0,owner_id:undefined as number|undefined,state:'new'})
const form=ref(blank())
const conversion=ref({title:'',estimated_amount:'0',currency:'AED',expected_close:''})
const convertOpen=ref(false)
const conversionTitle=ref<HTMLInputElement>()
const activityOpen=ref(false)
const owners=ref<Owner[]>([])
const busy=ref(false)
const error=ref('')
const saved=ref(false)
const create=createRequest()
const convert=createRequest()
const canEdit=computed(()=>!lead.value || lead.value.state!=='converted' && allows(props.workspace,'crm.manage',lead.value.site_id))
const canConvert=computed(()=>lead.value?.state==='qualified'&&form.value.state==='qualified')
function toggleConversion(){
  convertOpen.value=!convertOpen.value
  if(convertOpen.value)void nextTick(()=>conversionTitle.value?.focus())
}
const sites=computed(()=>props.workspace.sites.filter(site=>site.active&&allows(props.workspace,'crm.manage',site.id)&&allows(props.workspace,'crm.view',site.id)))
function setLead(row:Lead){
  lead.value=row;form.value={...row};conversion.value.title=row.company||row.name
  if(row.state!=='qualified')convertOpen.value=false
}
onMounted(async()=>{
  if (!props.id) {form.value.site_id=sites.value[0]?.id??0;return}
  try{setLead(await api<Lead>('/crm/leads/'+props.id))}
  catch(failure){error.value=message(failure)}
})
let sequence=0
watch(()=>form.value.site_id,async(site)=>{
  owners.value=[]
  const token=++sequence
  if(!site||!allows(props.workspace,'crm.manage',site))return
  try {
    const result=await api<{items:Owner[]}>('/crm/owners?site='+site)
    if(token===sequence)owners.value=result.items
  } catch(failure){if(token===sequence)error.value=message(failure)}
})
async function run(action:()=>Promise<void>){
  if(busy.value)return
  busy.value=true;error.value='';saved.value=false
  try{await action();saved.value=true}catch(failure){error.value=message(failure)}finally{busy.value=false}
}
function save(){
  return run(async()=>{
    const row=lead.value
    if(row){setLead(await api<Lead>('/crm/leads/'+row.id,'PATCH',{...form.value,revision:row.revision}));return}
    const result=await create<Lead>('/crm/leads',form.value)
    await router.push('/crm/leads/'+result.id)
  })
}
function convertLead(){
  const row=lead.value
  if(!row)return
  return run(async()=>{
    const result=await convert<Opportunity>('/crm/leads/'+row.id+'/convert',{...conversion.value,revision:row.revision})
    await router.push('/crm/opportunities/'+result.id)
  })
}
</script>

<template>
  <RouterLink class="back-link" to="/crm/leads">‹ {{ t('Leads') }}</RouterLink>
  <h2>{{ lead?.number ?? t('New lead') }}</h2>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="saved" class="success" role="status">{{ t('Changes saved.') }}</p>
  <form v-if="(!id||lead)&&canEdit" v-validated-form class="panel record-form crm-panel" @submit.prevent="save">
    <div class="crm-fields">
      <label>{{ t('Name') }}<input v-model.trim="form.name" data-field="name" maxlength="120" :placeholder="t('e.g., John Doe')" required></label>
      <label>{{ t('Company') }}<input v-model.trim="form.company" data-field="company" maxlength="120" :placeholder="t('Company registered name')"></label>
      <label>{{ t('Email') }}<input v-model.trim="form.email" data-field="email" type="email" maxlength="254" :placeholder="t('e.g., name@example.com')"></label>
      <label>{{ t('Phone') }}<input v-model.trim="form.phone" data-field="phone" maxlength="48" :placeholder="t('e.g., +971 50 123 4567')"></label>
      <label>{{ t('Lead source') }}<input v-model.trim="form.source" data-field="source" maxlength="80" :placeholder="t('e.g., Referral')"></label>
      <label v-if="!lead">{{ t('Site') }}<select v-model="form.site_id" data-field="site_id" required @change="form.owner_id=undefined">
        <option :value="0" disabled>{{ t('Select site') }}</option>
        <option v-for="site in sites" :key="site.id" :value="site.id">{{ site.name }}</option>
      </select></label>
      <label>{{ t('Owner') }}<EntityAutocomplete v-model="form.owner_id" lookup="users" :local-items="owners.map(person => ({ id: person.id, label: person.username }))" :empty-label="t(lead ? 'Keep current owner' : 'Assigned to me')" data-field="owner_id" /></label>
      <label v-if="lead">{{ t('Status') }}<select v-model="form.state" data-field="state">
        <option value="new">{{ t('New') }}</option><option value="qualified">{{ t('Qualified') }}</option>
        <option value="disqualified">{{ t('Disqualified') }}</option>
      </select></label>
    </div>
    <div class="actions">
      <button class="primary" :disabled="busy">{{ t('Save lead') }}</button>
      <button v-if="lead" class="secondary" type="button" :disabled="busy||!canConvert"
        :aria-expanded="convertOpen&&canConvert" :aria-describedby="canConvert?undefined:'lead-convert-hint'"
        @click="toggleConversion">{{ t('Convert to opportunity') }}</button>
    </div>
    <p v-if="lead&&!canConvert" id="lead-convert-hint" class="muted">
      {{ t('Set Status to Qualified and save the lead to enable conversion.') }}
    </p>
  </form>
  <dl v-else-if="lead" class="panel crm-panel crm-summary">
    <div><dt>{{ t('Name') }}</dt><dd>{{ lead.name }}</dd></div><div><dt>{{ t('Company') }}</dt><dd>{{ lead.company || '—' }}</dd></div>
    <div><dt>{{ t('Email') }}</dt><dd>{{ lead.email || '—' }}</dd></div><div><dt>{{ t('Phone') }}</dt><dd>{{ lead.phone || '—' }}</dd></div>
    <div><dt>{{ t('Owner') }}</dt><dd>{{ lead.owner }}</dd></div>
  </dl>
  <div v-if="lead" class="actions crm-section">
    <RouterLink v-if="lead.opportunity_id" class="button secondary" :to="'/crm/opportunities/'+lead.opportunity_id">{{ t('Open opportunity') }}</RouterLink>
    <button v-if="allows(workspace,'crm.manage',lead.site_id)" class="secondary" type="button" @click="activityOpen=!activityOpen">{{ t('New activity') }}</button>
    <RouterLink class="button secondary" :to="{path:'/crm/activities',query:{lead:lead.id}}">{{ t('Activities') }}</RouterLink>
  </div>
  <form v-if="convertOpen&&lead&&canEdit&&canConvert" v-validated-form class="panel record-form crm-panel" @submit.prevent="convertLead">
    <h3>{{ t('Convert to opportunity') }}</h3>
    <div class="crm-fields">
      <label>{{ t('Title') }}<input ref="conversionTitle" v-model.trim="conversion.title" data-field="title" maxlength="160" required :placeholder="t('Enter a short, descriptive title')"></label>
      <label>{{ t('Estimated value') }}<input v-model="conversion.estimated_amount" data-field="estimated_amount" inputmode="decimal" required :placeholder="t('e.g., 125.50')"></label>
      <label>{{ t('Currency') }}<input v-model.trim="conversion.currency" data-field="currency" maxlength="3" pattern="[A-Za-z]{3}" placeholder="AED" required></label>
      <label>{{ t('Expected close date') }}<input v-model="conversion.expected_close" data-field="expected_close" type="date"></label>
    </div>
    <button class="primary" :disabled="busy">{{ t('Convert') }}</button>
  </form>
  <CrmActivityForm v-if="lead&&activityOpen" :site="lead.site_id" :lead="lead.id" @cancel="activityOpen=false" @saved="id=>router.push('/crm/activities/'+id)" />
</template>
