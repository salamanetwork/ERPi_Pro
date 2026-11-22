<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, allows, createRequest, type Workspace } from './api'
import { message, t } from './i18n'

type Item = { actions?:string[]; is_creator?:boolean; id:number; site_id:number; title?:string; name?:string; channel?:string; status?:string; revision?:number;
  forgotten?:boolean; first_name?:string; reason?:string; created_at?:string; fingerprint?:string; responses?:number;
  timezone_changed?:boolean; scheduled_local?:string; site_timezone?:string; template_version_id?:number;
  endpoints?:{id:number; channel:string; address:string; consents:{purpose:string;status:string}[]}[];
  versions?:{id:number;number:number;subject:string;locked_at:string|null}[] }
type Page = {count:number;results:Item[];next:string|null;previous:string|null}
type Sample = {contact_id:number;subject:string;body:string;missing_variables:string[]}
const props = defineProps<{workspace:Workspace}>()
const post = createRequest()
const route = useRoute()
const router = useRouter()
const detailId = computed(() => Number(route.params.id) || undefined)
const campaignDetail = computed(() => detailId.value !== undefined)
const permitted = (action:string) => selected.value?.actions?.includes(action) ?? false
const rawSections = ['campaigns','contacts','segments','templates','settings','suppressions','dead-letters','surveys','automation-rules'] as const
type Section = typeof rawSections[number]
const labels: Record<Section,string> = {campaigns:'Campaigns',contacts:'Contacts',segments:'Segments',templates:'Templates',
  settings:'Settings',suppressions:'Suppressions','dead-letters':'Dead letters',surveys:'Surveys','automation-rules':'Automation rules'}
const sections = computed(() => rawSections.filter(section => section === 'suppressions' ?
  allowed('marketing.suppressions') : ['settings','dead-letters'].includes(section) ? allowed('marketing.admin') : true))
const sites = computed(() => props.workspace.sites.filter(site => site.active && allows(props.workspace,'marketing.view',site.id)))
const site = ref(sites.value[0]?.id)
const panel = ref<Section>('campaigns')
const rows = ref<Item[]>([])
const page=ref(1),size=ref(25),total=ref(0)
const more = ref(false)
const selected = ref<Item>()
const samples = ref<Sample[]>([])
const testDelivery = ref<number>()
const result = ref('')
const error = ref('')
const busy = ref(false)
const name = ref('')
const address = ref('')
const channel = ref('email')
const purpose = ref('marketing')
const subject = ref('')
const content = ref('')
const providerTemplate = ref('')
const zone = ref('')
const when = ref('')
const versionId = ref<number>()
const segmentIds = ref('')
const sourceId = ref<number>()
const customerId = ref<number>()
const sampleId = ref<number>()
const surveyId = ref<number>()
const delay = ref(0)
const templateId = ref<number>()
const consentMode = ref('single')
const adminAdapter = ref('mailpit')
const rate = ref(1)
const region = ref('')
const sns = ref('')
const configurationSet = ref('')
const forgetReason = ref('')
const versions = ref<{id:number; label:string}[]>([])
const segments = ref<Item[]>([])
let generation = 0

function allowed(permission:string) { return site.value !== undefined && allows(props.workspace,permission,site.value) }
const admin = computed(() => campaignDetail.value && selected.value ? allows(props.workspace,'marketing.admin',selected.value.site_id) : allowed('marketing.admin'))
const editor = computed(() => campaignDetail.value && selected.value ? allows(props.workspace,'marketing.edit',selected.value.site_id) : allowed('marketing.edit'))
const ids = (value:string) => value.split(',').filter(v=>v.trim()).map(Number)

async function load() {
  if (site.value===undefined && !detailId.value) return
  const token=++generation
  const query='?site_id='+site.value+'&page='+page.value+'&page_size='+size.value
  try {
    if (detailId.value) {
      const detail=await api<Item>('/marketing/campaigns/'+detailId.value)
      if(token!==generation)return
      selected.value=detail
      return
    }
    let list: Page | undefined
    if (panel.value==='settings') {
      const data=await api<{consent_mode:string;timezone:string;providers:object[]}>('/marketing/settings/'+site.value)
      if(token!==generation)return
      consentMode.value=data.consent_mode;zone.value=data.timezone;result.value=JSON.stringify(data.providers,null,2)
    } else {
      list=await api<Page>('/marketing/'+panel.value+query)
    }
    if (token!==generation)return
    rows.value=list?.results ?? [];more.value=!!list?.next;total.value=list?.count||0
    if (panel.value==='campaigns') {
      const [templates,group]=await Promise.all([
        api<Page>('/marketing/templates?site_id='+site.value),api<Page>('/marketing/segments?site_id='+site.value)])
      if (token!==generation)return
      versions.value=templates.results.filter(item=>item.channel===channel.value).flatMap(item=>
        (item.versions??[]).map(v=>({id:v.id,label:item.name+' · v'+v.number})))
      segments.value=group.results
      if(selected.value) selected.value=rows.value.find(item=>item.id===selected.value?.id)
    }
  } catch(e) {if(token===generation)error.value=message(e)}
}
watch([site,panel,detailId],()=>{
  selected.value=undefined;samples.value=[];result.value='';page.value=1;error.value='';when.value=''
  void load()
},{immediate:true})
function changePanel(section:Section) {
  panel.value=section
  if(campaignDetail.value) void router.push('/marketing')
}
watch([channel,page,size],()=>{if(panel.value==='campaigns')versionId.value=undefined;void load()})
function select(row:Item) {
  if(panel.value==='campaigns') {void router.push('/marketing/campaigns/'+row.id);return}
  selected.value=row;samples.value=[];testDelivery.value=undefined;result.value='';sampleId.value=undefined;templateId.value=undefined
  address.value='';forgetReason.value=''
}
async function execute(work:()=>Promise<unknown>, refresh=true) {
  if(busy.value)return
  busy.value=true;error.value='';result.value=''
  try {const res=await work();if(res)result.value=JSON.stringify(res,null,2); if(refresh) await load()}
  catch(e){error.value=message(e)}
  finally{busy.value=false}
}
function create() {
  const common={site_id:site.value}
  const version={subject:subject.value,body:content.value,provider_template:providerTemplate.value}
  const propsBySection: Partial<Record<Section,object>>={
    contacts:{...common,first_name:name.value,address:address.value,channel:channel.value,
      lead_id:sourceId.value || null,customer_id:customerId.value || null},
    segments:{...common,name:name.value,contact_ids:ids(segmentIds.value)},
    templates:{...common,name:name.value,channel:channel.value,...version},
    campaigns:{...common,title:name.value,channel:channel.value,purpose:purpose.value,
      template_version_id:versionId.value,segment_ids:ids(segmentIds.value)},
    surveys:{...common,title:name.value,questions:content.value.split('\n').filter(Boolean)},
    'automation-rules':{...common,campaign_id:sourceId.value,survey_id:surveyId.value,delay_minutes:delay.value},
  }
  const endpoint = panel.value==='templates' && templateId.value ? '/templates/'+templateId.value+'/versions' : '/'+panel.value
  const data = panel.value==='templates' && templateId.value ? version : propsBySection[panel.value]
  if(data) void execute(async()=>{
    const created=await post<{id:number}>('/marketing'+endpoint,data)
    if(panel.value==='campaigns') await router.push('/marketing/campaigns/'+created.id)
    return created
  })
}
function campaignAction(action:string) {
  const row=selected.value
  if(!row)return
  const endpoint='/marketing/campaigns/'+row.id
  if(action==='preview') {
    void execute(async()=>{samples.value=(await api<{samples:Sample[]}>(endpoint+'/preview','POST',{})).samples},false)
  } else if(action==='metrics') void execute(()=>api(endpoint+'/metrics'),false)
  else void execute(()=>post<{delivery_id?:number}>(endpoint+'/'+action,{revision:row.revision,scheduled_at:when.value,
    address:address.value,contact_id:sampleId.value||null,template_version_id:versionId.value||null}).then(response=>{
      if(response.delivery_id) testDelivery.value=response.delivery_id
      return response
  }),action!=='test-send')
}
function consent(endpoint_id:number,change:string) {
  if(selected.value)void execute(()=>post('/marketing/contacts/'+selected.value?.id+'/consents',{
    endpoint_id,change,purpose:purpose.value,source:'documented',
  }).then(async result=>{selected.value=(await api<Page>('/marketing/contacts?site_id='+site.value)).results.find(row=>row.id===selected.value?.id);return result}))
}
function mode() {void execute(()=>post('/marketing/settings/'+site.value,{consent_mode:consentMode.value}))}
function provider() {void execute(()=>post('/marketing/settings/'+site.value,{channel:channel.value,adapter:adminAdapter.value,
  sender:address.value,rate:rate.value,region:region.value,topic_arn:sns.value,configuration_set:configurationSet.value}))}
</script>

<template>
  <main>
    <header class="split"><h1>{{ t(campaignDetail ? 'Campaign details' : 'Marketing') }}</h1>
      <label v-if="!campaignDetail">{{ t('Site') }} <select v-model="site"><option v-for="item in sites" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
    </header>
    <RouterLink v-if="campaignDetail" class="button secondary" to="/marketing">{{ t('Back to campaigns') }}</RouterLink>
    <nav v-else class="actions" :aria-label="t('Marketing')">
      <button v-for="item in sections" :key="item" :class="panel===item?'primary':'secondary'" @click="changePanel(item)">{{ t(labels[item]) }}</button>
    </nav>
    <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
    <section v-if="panel==='settings' && admin" class="panel">
      <form class="record-form" @submit.prevent="mode">
        <label>{{ t('Marketing Consent Mode') }}<select v-model="consentMode"><option value="single">{{ t('Single Opt-in') }}</option><option value="double">{{ t('Double Opt-in') }}</option></select></label>
        <p>{{ t('Site timezone') }} · {{ zone }}</p>
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
      </form>
      <hr>
      <form class="record-form" @submit.prevent="provider">
        <label>{{ t('Channel') }}<select v-model="channel" @change="adminAdapter=channel==='email'?'mailpit':'mock'"><option>email</option><option>sms</option><option>whatsapp</option></select></label>
        <label>{{ t('Provider') }}<select v-model="adminAdapter"><option v-if="channel==='email'">mailpit</option><option v-if="channel==='email'">ses</option><option v-if="channel!=='email'">mock</option><option v-if="channel!=='email'">twilio</option></select></label>
        <label>{{ t('Sender') }}<input v-model="address" required></label>
        <label>{{ t('Requests per second') }}<input v-model.number="rate" type="number" min="1" required></label>
        <template v-if="adminAdapter==='ses'">
          <label>AWS Region <input v-model="region" required></label><label>Configuration Set <input v-model="configurationSet" required></label>
          <label>SNS Topic ARN <input v-model="sns" required></label>
        </template>
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
      </form>
    </section>
    <template v-else-if="!campaignDetail">
      <form v-if="editor && ['contacts','segments','templates','campaigns','surveys','automation-rules'].includes(panel)" class="panel record-form" @submit.prevent="create">
        <h2>{{ t(labels[panel]) }}</h2>
        <label v-if="panel!=='automation-rules'">{{ t('Name') }}<input v-model="name" maxlength="160" :required="panel!=='contacts'"></label>
        <label v-if="['campaigns','contacts','templates'].includes(panel)">{{ t('Channel') }}
          <select v-model="channel"><option>email</option><option>sms</option><option>whatsapp</option></select>
        </label>
        <template v-if="panel==='contacts'">
          <label>{{ t('Address') }}<input v-model="address"></label>
          <label>{{ t('Lead ID') }}<input v-model.number="sourceId" type="number" min="1"></label>
          <label>{{ t('Customer ID') }}<input v-model.number="customerId" type="number" min="1"></label>
        </template>
        <label v-if="panel==='segments'">{{ t('Contact IDs, comma separated') }}<input v-model="segmentIds" required></label>
        <template v-if="panel==='templates'">
          <label>{{ t('Subject') }}<input v-model="subject"></label>
          <label>{{ t('Message body') }}<textarea v-model="content" placeholder="Hello {{first_name}}"></textarea></label>
          <label v-if="channel==='whatsapp'">Content SID <input v-model="providerTemplate"></label>
          <label>{{ t('New version of template ID') }}<input v-model.number="templateId" type="number" min="1"></label>
        </template>
        <template v-if="panel==='campaigns'">
          <label>{{ t('Purpose') }} <select v-model="purpose"><option>marketing</option><option>utility</option></select></label>
          <label>{{ t('Template version') }}<select v-model="versionId" required>
            <option v-for="v in versions" :key="v.id" :value="v.id">{{ v.label }}</option></select></label>
          <label>{{ t('Segment IDs, comma separated') }}<input v-model="segmentIds" required></label>
          <p>{{ segments.map(row=>row.id+' · '+row.name).join(' / ') }}</p>
        </template>
        <label v-if="panel==='surveys'">{{ t('Questions, one per line') }}<textarea v-model="content" required></textarea></label>
        <template v-if="panel==='automation-rules'">
          <label>{{ t('Campaign ID') }}<input v-model.number="sourceId" type="number" required min="1"></label>
          <label>{{ t('Survey ID') }}<input v-model.number="surveyId" type="number" required min="1"></label>
          <label>{{ t('Delay in minutes') }}<input v-model.number="delay" type="number" required min="0"></label>
        </template>
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
      </form>
      <section class="panel">
        <div class="actions"><button class="secondary" :disabled="busy" @click="load">{{ t('Refresh') }}</button>
          </div><TableToolbar :page="page" :size="size" :count="total" @page="page=$event" @size="page=1;size=$event"/>
        <table>
          <thead><tr><th>ID</th><th>{{ t('Name') }}</th><th>{{ t('Status') }}</th><th>{{ t('Details') }}</th></tr></thead>
          <tbody><tr v-for="row in rows" :key="row.id">
            <td>{{ row.id }}</td><td><RouterLink v-if="panel==='campaigns'" :to="'/marketing/campaigns/'+row.id">{{ row.title }}</RouterLink>
              <span v-else>{{ row.title ?? row.name ?? row.first_name ?? row.channel ?? '—' }}</span></td>
            <td>{{ row.forgotten ? t('Forgotten') : row.status ?? row.reason ?? '—' }}<br v-if="row.created_at"><time v-if="row.created_at" :datetime="row.created_at">{{ row.created_at }}</time></td>
            <td><button class="secondary" @click="select(row)">{{ t('Details') }}</button></td>
          </tr></tbody>
        </table>
      </section>
    </template>
    <section v-if="selected && campaignDetail" class="panel record-form">
      <h2>{{ selected.title }}</h2>
      <p>{{ selected.status }} · {{ selected.scheduled_local }} {{ selected.site_timezone }}</p>
      <p v-if="selected.timezone_changed" class="alert">{{ t('Site timezone changed. Review the scheduled local time.') }}</p>
      <div class="actions"><button v-if="permitted('preview')" class="secondary" :disabled="busy" @click="campaignAction('preview')">{{ t('Preview') }}</button>
        <button class="secondary" @click="campaignAction('metrics')">{{ t('Campaign metrics') }}</button></div>
      <p v-if="selected.status==='draft' && selected.is_creator && admin" class="alert" role="status">{{ t('Another marketing administrator must approve this campaign.') }}</p>
      <p v-if="!admin" role="status">{{ t('Campaign approval and sending require marketing.admin for this site.') }}</p>
      <template v-if="admin">
        <div class="actions">
          <button v-if="selected.status==='draft'" class="primary" :disabled="busy || !permitted('approve')" @click="campaignAction('approve')">{{ t('Approve') }}</button>
          <button class="primary" :disabled="busy || !permitted('send')" @click="campaignAction('send')">{{ t('Send now') }}</button>
        </div>
        <p v-if="selected.status==='draft'" class="muted">{{ t('Approve this campaign before sending or scheduling.') }}</p>
        <label>{{ t('Test recipient') }}<input v-model="address"></label>
        <label>{{ t('Contact ID') }}<input v-model.number="sampleId" type="number" min="1"></label>
        <div class="actions"><button class="secondary" :disabled="busy || !address.trim()" @click="campaignAction('test-send')">{{ t('Test send') }}</button>
          <button v-if="testDelivery" class="secondary" @click="execute(()=>api('/marketing/deliveries/'+testDelivery),false)">{{ t('Test delivery status') }}</button>
          <button class="secondary" @click="campaignAction('preflight')">{{ t('Provider preflight') }}</button></div>
        <label>{{ t('Scheduled time in site timezone') }}<input v-model="when" type="datetime-local"></label>
        <div class="actions">
          <button v-if="['approved','scheduled','paused'].includes(selected.status ?? '')" class="primary" :disabled="busy || !when" @click="campaignAction('schedule')">{{ t('Schedule') }}</button>
          <button v-if="selected.status==='scheduled'" class="secondary" @click="campaignAction('pause')">{{ t('Pause') }}</button>
          <button v-if="selected.status==='paused'" class="secondary" @click="campaignAction('resume')">{{ t('Resume') }}</button>
          <button v-if="['approved','scheduled','paused'].includes(selected.status ?? '')" class="secondary" @click="campaignAction('cancel')">{{ t('Cancel') }}</button>
        </div>
        <label v-if="['draft','approved','scheduled','paused'].includes(selected.status ?? '')">{{ t('Template version') }}<input v-model.number="versionId" type="number" min="1"></label>
        <button v-if="['draft','approved','scheduled','paused'].includes(selected.status ?? '')" class="secondary" :disabled="!versionId" @click="campaignAction('template')">{{ t('Change template version') }}</button>
      </template>
      <article v-for="sample in samples" :key="sample.contact_id">
        <h3>#{{ sample.contact_id }} · {{ sample.subject }}</h3><pre style="white-space:pre-wrap">{{ sample.body }}</pre>
        <p v-if="sample.missing_variables.length" class="alert">{{ t('Missing variables') }} · {{ sample.missing_variables.join(', ') }}</p>
      </article>
    </section>
    <section v-if="selected && panel==='contacts'" class="panel record-form">
      <h2>{{ selected.first_name }} · #{{ selected.id }}</h2>
      <template v-if="editor && !selected.forgotten">
        <label>{{ t('Purpose') }} <select v-model="purpose"><option>marketing</option><option>utility</option></select></label>
        <div v-for="item in selected.endpoints" :key="item.id">
          <p>{{ item.channel }} · {{ item.address }} · {{ item.consents.map(c=>c.purpose+' '+c.status).join(', ') }}</p>
          <div class="actions"><button class="secondary" @click="consent(item.id,'in')">{{ t('Opt in') }}</button>
            <button class="secondary" @click="consent(item.id,'out')">{{ t('Opt out') }}</button></div>
        </div>
        <label>{{ t('Address') }}<input v-model="address"></label>
        <label>{{ t('Channel') }}<select v-model="channel"><option>email</option><option>sms</option><option>whatsapp</option></select></label>
        <button class="secondary" :disabled="!address" @click="execute(()=>post('/marketing/contacts/'+selected?.id+'/endpoints',{channel,address})).then(()=>{selected=undefined})">{{ t('Add endpoint') }}</button>
      </template>
      <button class="secondary" @click="execute(()=>api('/marketing/contacts/'+selected?.id+'/consents'),false)">{{ t('Consent history') }}</button>
      <template v-if="admin && !selected.forgotten">
        <label>{{ t('Reason') }}<input v-model="forgetReason"></label>
        <button class="secondary" :disabled="!forgetReason.trim()" @click="execute(()=>post('/marketing/contacts/'+selected?.id+'/forget',{reason:forgetReason})).then(()=>{selected=undefined})">{{ t('Forget contact') }}</button>
      </template>
    </section>
    <section v-if="selected && ['suppressions','dead-letters','templates','surveys','segments','automation-rules'].includes(panel)" class="panel">
      <pre style="white-space:pre-wrap">{{ JSON.stringify(selected,null,2) }}</pre>
      <button v-if="panel==='surveys'" class="secondary" @click="execute(()=>api('/marketing/surveys/'+selected?.id+'/results'),false)">{{ t('Survey responses') }}</button>
      <button v-if="panel==='dead-letters' && admin" class="secondary" @click="execute(()=>post('/marketing/dead-letters/'+selected?.id+'/requeue',{}))">{{ t('Requeue') }}</button>
    </section>
    <pre v-if="result" class="panel" role="status" style="white-space:pre-wrap">{{ result }}</pre>
  </main>
</template>
