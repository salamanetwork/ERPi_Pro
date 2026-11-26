<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { allows, api, createRequest, type Workspace } from './api'
import { locale, localizedText, message, t } from './i18n'
import { blankCustomer, type Customer, type CustomerRequest, type History, type Opportunity, type Owner, type Page, type Stage } from './crm'
import CrmCustomerForm from './CrmCustomerForm.vue'
import CrmQuotationForm from './CrmQuotationForm.vue'
import CrmActivityForm from './CrmActivityForm.vue'

const props=defineProps<{workspace:Workspace;id?:number}>()
const router=useRouter()
const opportunity=ref<Opportunity>()
const blank=()=>({site_id:0,title:'',estimated_amount:'0',currency:'AED',expected_close:'',owner_id:undefined as number|undefined})
const form=ref(blank())
const owners=ref<Owner[]>([])
const stages=ref<Stage[]>([])
const errors=ref('')
const saved=ref(false)
const busy=ref(false)
const activityOpen=ref(false)
const quoteOpen=ref(false)
const customerMode=ref<'existing'|'new'|''>('')
const customerForm=ref(blankCustomer())
const linked=ref(0)
const customers=ref<Customer[]>([])
const stageId=ref(0)
const stageReason=ref('')
const history=ref<Page<History>>()
const requests=ref<CustomerRequest[]>([])
const create=createRequest()
const createCustomer=createRequest()
const sites=computed(()=>props.workspace.sites.filter(site=>site.active&&allows(props.workspace,'crm.manage',site.id)&&allows(props.workspace,'crm.view',site.id)&&allows(props.workspace,'sales.view',site.id)))
const canEdit=computed(()=>!opportunity.value||opportunity.value.stage.kind==='open'&&allows(props.workspace,'crm.manage',opportunity.value.site_id))
const canSales=computed(()=>allows(props.workspace,'sales.view',form.value.site_id))
const canCreateCustomer=computed(()=>canSales.value&&allows(props.workspace,'sales.customer',null))
const pending=computed(()=>requests.value.some(row=>row.state==='pending'))
const lost=computed(()=>stages.value.some(row=>row.id===stageId.value&&row.kind==='lost'))
const stageName=(stage:Stage)=>locale.value==='ar'?stage.name_ar:stage.name_en
const customerPath='/crm/customer-lookup'
function setOpportunity(row:Opportunity){
  opportunity.value=row
  form.value={...row,expected_close:row.expected_close??''}
  stageId.value=row.stage_id
  stageReason.value=''
}
async function loadHistory(page=1){
  if(props.id)history.value=await api<Page<History>>('/crm/opportunities/'+props.id+'/history?page='+page)
}
async function loadRequests(){
  if(props.id)requests.value=(await api<Page<CustomerRequest>>('/crm/customer-requests?opportunity='+props.id)).results
}
async function run(action:()=>Promise<void>){
  if(busy.value)return
  busy.value=true;errors.value='';saved.value=false
  try{await action();saved.value=true}catch(failure){errors.value=message(failure)}finally{busy.value=false}
}
onMounted(async()=>{
  try {
    const values=await api<{items:Stage[]}>('/crm/stages')
    stages.value=values.items
    if(props.id){
      setOpportunity(await api<Opportunity>('/crm/opportunities/'+props.id))
      await Promise.all([loadHistory(),loadRequests()])
    }else form.value.site_id=sites.value[0]?.id??0
  }catch(failure){errors.value=message(failure)}
})
let sequence=0
watch(()=>form.value.site_id,async(site)=>{
  owners.value=[]
  customers.value=[]
  const token=++sequence
  if(!site||!allows(props.workspace,'crm.manage',site))return
  try {
    const tasks=[api<{items:Owner[]}>('/crm/owners?site='+site)]
    const results=await Promise.all(tasks)
    if(token===sequence){
      owners.value=results[0]!.items
      if(!props.id&&canSales.value)await findCustomers()
    }
  }catch(failure){if(token===sequence)errors.value=message(failure)}
})
let searchSequence=0
async function findCustomers(){
  const token=++searchSequence
  try {
    const result=await api<{items:Customer[]}>(customerPath+'?'+new URLSearchParams({site:String(form.value.site_id)}))
    if(token===searchSequence)customers.value=result.items
  }catch(failure){if(token===searchSequence)errors.value=message(failure)}
}
function save(){
  return run(async()=>{
    const row=opportunity.value
    if(row){setOpportunity(await api<Opportunity>('/crm/opportunities/'+row.id,'PATCH',{...form.value,revision:row.revision}));return}
    const result=await create<Opportunity>('/crm/opportunities',{...form.value,customer_id:linked.value})
    await router.push('/crm/opportunities/'+result.id)
  })
}
function move(){
  const row=opportunity.value
  if(!row||stageId.value===row.stage_id)return
  return run(async()=>{
    setOpportunity(await api<Opportunity>('/crm/opportunities/'+row.id+'/transition','POST',
      {revision:row.revision,stage_id:stageId.value,reason:stageReason.value}))
    await loadHistory()
  })
}
function showCustomer(mode:'new'|'existing'){
  customerMode.value=mode;linked.value=0
  if(mode==='new'){
    customerForm.value=blankCustomer()
  }else void findCustomers()
}
function linkCustomer(){
  const row=opportunity.value
  if(!row)return
  return run(async()=>{
    setOpportunity(await api<Opportunity>('/crm/opportunities/'+row.id+'/customer-link','POST',{revision:row.revision,customer_id:linked.value}))
    customerMode.value=''
  })
}
function submitCustomer(){
  const row=opportunity.value
  if(!row)return
  return run(async()=>{
    const path='/crm/opportunities/'+row.id
    const values={...customerForm.value,revision:row.revision}
    if(canCreateCustomer.value)setOpportunity(await createCustomer<Opportunity>(path+'/customer',values))
    else await createCustomer<CustomerRequest>(path+'/customer-requests',values)
    customerMode.value=''
    await loadRequests()
  })
}
function historyPage(page:number){void run(()=>loadHistory(page))}
</script>

<template>
  <RouterLink class="back-link" to="/crm/opportunities">‹ {{ t('Opportunities') }}</RouterLink>
  <header class="page-heading"><h2>{{ opportunity?.number ?? t('New opportunity') }}</h2>
    <span v-if="opportunity" class="badge">{{ stageName(opportunity.stage) }}</span></header>
  <p v-if="errors" class="alert" role="alert">{{ t(errors) }}</p>
  <p v-if="saved" class="success" role="status">{{ t('Changes saved.') }}</p>
  <form v-if="(!id||opportunity)&&canEdit" v-validated-form class="panel record-form crm-panel" @submit.prevent="save">
    <div class="crm-fields">
      <label>{{ t('Title') }}<input v-model.trim="form.title" data-field="title" required maxlength="160" :placeholder="t('Enter a short, descriptive title')"></label>
      <label>{{ t('Estimated value') }}<input v-model="form.estimated_amount" data-field="estimated_amount" inputmode="decimal" required :placeholder="t('e.g., 125.50')"></label>
      <label>{{ t('Currency') }}<input v-model.trim="form.currency" data-field="currency" required maxlength="3" pattern="[A-Za-z]{3}" placeholder="AED" :readonly="!!opportunity?.quotation"></label>
      <label>{{ t('Expected close date') }}<input v-model="form.expected_close" data-field="expected_close" type="date"></label>
      <label v-if="!opportunity">{{ t('Site') }}<select v-model="form.site_id" data-field="site_id" required @change="form.owner_id=undefined;linked=0">
        <option :value="0" disabled>{{ t('Select site') }}</option>
        <option v-for="site in sites" :key="site.id" :value="site.id">{{ site.name }}</option>
      </select></label>
      <label>{{ t('Owner') }}<EntityAutocomplete v-model="form.owner_id" lookup="users" :local-items="owners.map(row => ({ id: row.id, label: row.username }))" :empty-label="t(opportunity ? 'Keep current owner' : 'Assigned to me')" data-field="owner_id" /></label>
      <template v-if="!opportunity">
        <label>{{ t('Customer') }}<EntityAutocomplete v-model="linked" lookup="customers" :site-id="form.site_id" :local-items="customers.map(row => ({ id: row.id, label: row.code + ' · ' + row.name }))" :empty-value="0" :placeholder="t('Select customer')" data-field="customer_id" required /></label>
      </template>
    </div>
    <button class="primary" :disabled="busy">{{ t('Save opportunity') }}</button>
  </form>
  <dl v-else-if="opportunity" class="panel crm-panel crm-summary">
    <div><dt>{{ t('Title') }}</dt><dd>{{ opportunity.title }}</dd></div><div><dt>{{ t('Owner') }}</dt><dd>{{ opportunity.owner }}</dd></div>
    <div><dt>{{ t('Estimated value') }}</dt><dd><bdi>{{ opportunity.estimated_amount }} {{ opportunity.currency }}</bdi></dd></div>
    <div><dt>{{ t('Expected close date') }}</dt><dd>{{ opportunity.expected_close || '—' }}</dd></div>
  </dl>
  <template v-if="opportunity">
    <form v-if="canEdit" v-validated-form class="panel record-form crm-panel" @submit.prevent="move">
      <h3>{{ t('Move opportunity') }}</h3>
      <div class="crm-fields">
        <label>{{ t('Stage') }}<select v-model="stageId" data-field="stage_id">
          <option v-for="row in stages.filter(row=>row.active)" :key="row.id" :value="row.id">{{ stageName(row) }}</option></select></label>
        <label>{{ t('Reason') }}<input v-model="stageReason" data-field="reason" :required="lost" maxlength="500" :placeholder="t('Explain the reason')"></label>
      </div>
      <button class="secondary" :disabled="busy||stageId===opportunity.stage_id">{{ t('Move') }}</button>
    </form>
    <section class="panel record-form crm-panel">
      <h3>{{ t('Customer and quotation') }}</h3>
      <p v-if="opportunity.customer">{{ t('Customer') }} · {{ opportunity.customer }}</p>
      <p v-else-if="opportunity.has_customer">{{ t('Customer linked') }}</p>
      <p v-else>{{ t('No customer linked.') }}</p>
      <div class="actions" v-if="canEdit&&!opportunity.has_customer&&!pending">
        <button v-if="canSales" type="button" class="secondary" @click="showCustomer('existing')">{{ t('Link existing customer') }}</button>
        <button type="button" class="secondary" @click="showCustomer('new')">{{ t(canCreateCustomer ? 'Create Sales customer' : 'Request new customer') }}</button>
      </div>
      <form v-if="customerMode==='existing'" v-validated-form class="record-form" @submit.prevent="linkCustomer">
        <label>{{ t('Customer') }}<EntityAutocomplete v-model="linked" lookup="customers" :site-id="opportunity?.site_id" :local-items="customers.map(row => ({ id: row.id, label: row.code + ' · ' + row.name }))" :empty-value="0" :placeholder="t('Select customer')" data-field="customer_id" required /></label>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Link customer') }}</button>
          <button type="button" class="secondary" @click="customerMode=''">{{ t('Cancel') }}</button></div>
      </form>
      <CrmCustomerForm v-if="customerMode==='new'" v-model="customerForm" :busy="busy" :direct="canCreateCustomer" @cancel="customerMode=''" @submit="submitCustomer" />
      <ul v-if="requests.length"><li v-for="row in requests" :key="row.id">
        <RouterLink :to="'/crm/customer-requests/'+row.id">{{ row.number }} · {{ t(row.state==='pending'?'Pending':row.state==='approved'?'Approved':'Rejected') }}</RouterLink>
      </li></ul>
      <RouterLink v-if="opportunity.quotation" :to="'/sales/'+opportunity.quotation.id">{{ opportunity.quotation.number }} · {{ t(opportunity.quotation.status) }}</RouterLink>
      <p v-else-if="canEdit&&opportunity.has_customer&&canSales&&opportunity.currency!=='AED'" class="muted">{{ t('Sales quotations require AED.') }}</p>
      <button v-else-if="canEdit&&opportunity.has_customer&&canSales&&allows(workspace,'sales.quote',opportunity.site_id)&&!quoteOpen"
        class="secondary" type="button" @click="quoteOpen=true">{{ t('Create quotation') }}</button>
      <CrmQuotationForm v-if="quoteOpen" :opportunity="opportunity.id" :revision="opportunity.revision"
        @cancel="quoteOpen=false" @created="id=>router.push('/sales/'+id)" />
    </section>
    <div class="actions crm-section">
      <button v-if="allows(workspace,'crm.manage',opportunity.site_id)" class="secondary" type="button" @click="activityOpen=!activityOpen">{{ t('New activity') }}</button>
      <RouterLink :to="{path:'/crm/activities',query:{opportunity:opportunity.id}}" class="button secondary">{{ t('Activities') }}</RouterLink>
      <RouterLink v-if="opportunity.lead_id" :to="'/crm/leads/'+opportunity.lead_id" class="button secondary">{{ t('Source lead') }}</RouterLink>
    </div>
    <CrmActivityForm v-if="activityOpen" :site="opportunity.site_id" :opportunity="opportunity.id" @cancel="activityOpen=false" @saved="id=>router.push('/crm/activities/'+id)" />
    <section v-if="history" class="panel record-form crm-panel">
      <h3>{{ t('Stage history') }}</h3>
      <ol class="crm-history"><li v-for="row in history.results" :key="row.id">
        <strong>{{ localizedText(row.previous, locale) || '—' }} → {{ localizedText(row.stage, locale) }}</strong>
        <small>{{ row.actor }} · {{ new Date(row.created_at).toLocaleString(locale) }}</small>
        <p v-if="row.reason">{{ row.reason }}</p>
      </li></ol>
      <div class="actions">
        <button v-if="history.previous" type="button" class="secondary" @click="historyPage(history.page-1)">{{ t('Previous') }}</button>
        <button v-if="history.next" type="button" class="secondary" @click="historyPage(history.page+1)">{{ t('Next') }}</button>
      </div>
    </section>
  </template>
</template>
