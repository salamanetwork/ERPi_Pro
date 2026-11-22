<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, createRequest, requestKey, type Entry, type Workspace } from './api'
import { locale, message, t } from './i18n'

const pageSize=ref(25)
type Page<T> = {count:number;results:T[]; page:number; next:string|null; previous:string|null}
type Category = {id:number; code:string; name_ar:string; name_en:string; active:boolean; revision:number}
type Policy = {id:number; priority:string; version:number; first_response_minutes:number; resolution_minutes:number}
type Ticket = {
  id:number; number:string; site_id:number; requester:string; assignee:string|null; assignee_id:number|null; subject:string; description:string;
  category_id:number|null; priority:string; status:string; revision:number; overdue:boolean;
  first_response_due:string; resolution_due:string; first_response_at:string|null; resolved_at:string|null;
  can_edit:boolean; can_assign:boolean; can_use_internal_notes:boolean; transitions:string[];
  candidates?:Entry[]; work_orders?:Entry[]; project_tasks?:Entry[];
  links:{id:number;label:string;route:string}[];
}
type Comment = {id:number; author:string; body:string; internal:boolean; created_at:string}
type Event = {id:number; event:string; actor:string|null; previous:string; current:string; reason:string; created_at:string}

const { workspace } = defineProps<{workspace:Workspace}>()
const route = useRoute()
const router = useRouter()
const base = '/helpdesk'
const tickets = base + '/tickets'
const entry = computed(() => String(route.params.entry || ''))
const sites = computed(() => workspace.sites.filter(site => site.active && allows(workspace,'helpdesk.view',site.id)))
const requestSites = computed(() => sites.value.filter(site => allows(workspace,'helpdesk.request',site.id)))
const configSites = computed(() => sites.value.filter(site => allows(workspace,'helpdesk.configure',site.id)))
const priorities = ['low','normal','high','urgent']
const states = ['open','in_progress','waiting','resolved','closed']
const labels: Record<string,string> = {
  low:'Low',normal:'Normal',high:'High',urgent:'Urgent',open:'Open',in_progress:'In progress',
  waiting:'Waiting',resolved:'Resolved',closed:'Closed',opened:'Ticket opened',edited:'Ticket edited',
  first_response_overdue:'First response overdue',resolution_overdue:'Resolution overdue',
  assigned:'Ticket assigned',commented:'Comment added',transition:'Status changed',linked:'Record linked',
}
const label = (value:string) => t(labels[value] ?? value)
const fmt = (date:string|null) => date ? new Intl.DateTimeFormat(locale.value,{dateStyle:'medium',timeStyle:'short'}).format(new Date(date)) : '—'
const siteName = (id:number) => workspace.sites.find(site => site.id === id)?.name ?? String(id)
const filter = ref({q:'',site:'', status:'',priority:'',overdue:false})
const page = ref<Page<Ticket>>()
const ticket = ref<Ticket>()
const replies = ref<Page<Comment>>()
const history = ref<Page<Event>>()
const categories = ref<Page<Category>>()
const policies = ref<Page<Policy>>()
const form = ref({site_id:0,subject:'',description:'',category_id:null as number|null,priority:'normal'})
const note = ref({body:'',internal:false})
const assignee = ref(0)
const reason = ref('')
const destination = ref('')
const target = ref({type:'work_order',object_id:0})
const category = ref({id:0,revision:0,code:'',name_ar:'',name_en:'',active:true})
const policy = ref({priority:'normal',first_response_minutes:120,resolution_minutes:1440})
const busy = ref(false)
const loading = ref(false)
const error = ref('')
const success = ref('')
const post = createRequest()
let generation = 0

async function config(site:number,pageNumber=1) {
  const token = generation
  if (!site) {categories.value=undefined;policies.value=undefined;return}
  const editing = entry.value === 'settings'
  const values = '?' + new URLSearchParams({site:String(site),page:String(pageNumber),page_size:'100',...(editing ? {all:'1'} : {})})
  try {
    const [categoryRows,policyRows] = await Promise.all([
      api<Page<Category>>(base+'/categories'+values),
      editing ? api<Page<Policy>>(base+'/sla-policies'+values) : Promise.resolve(undefined),
    ])
    if (token === generation && site === form.value.site_id) {
      categories.value=categoryRows
      policies.value=policyRows
    }
  } catch (failure) { if (token === generation) error.value=message(failure) }
}
async function journal(kind:'comments'|'events',number=1) {
  const token=generation
  const value=await api<Page<Comment>|Page<Event>>(tickets+'/'+entry.value+'/'+kind+'?page='+number)
  if (token !== generation) return
  if (kind === 'comments') replies.value=value as Page<Comment>
  else history.value=value as Page<Event>
}
async function load(number=1) {
  const token=++generation
  const current=entry.value
  loading.value=true
  error.value=''
  try {
    if (!current) {
      const {q,site,status,priority,overdue}=filter.value
      const values = new URLSearchParams({page:String(number),page_size:String(pageSize.value),...(q ? {q} : {}),...(site ? {site} : {}),
        ...(status ? {status} : {}),...(priority ? {priority} : {}),...(overdue ? {overdue:'1'} : {})})
      const result=await api<Page<Ticket>>(tickets+'?'+values)
      if (token === generation) page.value=result
    } else if (current === 'settings' || current === 'new') {
      const allowed=current === 'settings' ? configSites.value : requestSites.value
      form.value.site_id=allowed.some(site => site.id===form.value.site_id) ? form.value.site_id : allowed[0]?.id ?? 0
      await config(form.value.site_id)
    } else {
      const result=await api<Ticket>(tickets+'/'+current)
      if (token !== generation) return
      ticket.value=result
      form.value={site_id:result.site_id,subject:result.subject,description:result.description,
        category_id:result.category_id,priority:result.priority}
      assignee.value=result.assignee_id ?? 0
      await Promise.all([journal('comments'),journal('events'),config(result.site_id)])
    }
  } catch (failure) {if (token === generation) error.value=message(failure)}
  finally {if (token === generation) loading.value=false}
}
watch(entry,() => {
  ticket.value=undefined
  replies.value=history.value=undefined
  destination.value=reason.value=''
  note.value={body:'',internal:false}
  page.value=undefined
  categories.value=undefined
  policies.value=undefined
  success.value=''
  if (entry.value==='new') form.value={site_id:0,subject:'',description:'',category_id:null,priority:'normal'}
  void load()
},{immediate:true})
async function save(operation:()=>Promise<unknown>, reload:()=>Promise<unknown>=load) {
  busy.value=true
  success.value=error.value=''
  try {await operation();await reload();success.value='Changes saved.'}
  catch (failure) {error.value=message(failure)}
  finally {busy.value=false}
}
function submitTicket() {
  if (ticket.value) {
    const url=tickets+'/'+ticket.value.id
    void save(() => api(url,'PATCH',{...form.value,revision:ticket.value!.revision},requestKey()))
  } else {
    void save(() => post<Ticket>(tickets,form.value).then(row => router.push(base+'/'+row.id)), async () => {})
  }
}
function act(action:string,data:object) {
  void save(() => post(tickets+'/'+entry.value+'/'+action,{revision:ticket.value!.revision,...data}).then(() => {
    if (action === 'transition') destination.value=reason.value=''
  }))
}
function reply() {
  void save(() => post(tickets+'/'+entry.value+'/comments',note.value).then(() => {
    note.value={body:'',internal:false}
  }))
}
function changeSite() {
  form.value.category_id=null
  editCategory()
  void config(form.value.site_id)
}
function editCategory(row?:Category) {
  category.value=row ? {...row} : {id:0,revision:0,code:'',name_ar:'',name_en:'',active:true}
}
function saveCategory() {
  const url=base+'/categories'
  const value={...category.value,site_id:form.value.site_id}
  void save(() => category.value.id ? api(url+'/'+category.value.id,'PATCH',value,requestKey()) : post(url,value),
    () => {editCategory();return config(form.value.site_id)})
}
const links = computed(() => (target.value.type==='work_order' ? ticket.value?.work_orders : ticket.value?.project_tasks) ?? [])
</script>

<template>
  <header class="split">
    <h1>{{ ticket?.number ?? t(entry==='settings' ? 'Helpdesk settings' : entry==='new' ? 'New ticket' : 'Helpdesk') }}</h1>
    <div class="actions">
      <RouterLink v-if="entry" class="button secondary" :to="base">{{ t('All tickets') }}</RouterLink>
      <RouterLink v-if="!entry && requestSites.length" class="button primary" :to="base+'/new'">{{ t('New ticket') }}</RouterLink>
      <RouterLink v-if="!entry && configSites.length" class="button secondary" :to="base+'/settings'">{{ t('Helpdesk settings') }}</RouterLink>
    </div>
  </header>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="success" class="success" role="status">{{ t(success) }}</p>
  <p v-if="loading" role="status">{{ t('Loading') }}…</p>

  <template v-if="!entry">
    <form class="panel dashboard-filters" @submit.prevent="load()">
      <label>{{ t('Search') }}<input v-model="filter.q" maxlength="120" type="search" :placeholder="t('Search by ticket number or subject')"></label>
      <label>{{ t('Site') }}<select v-model="filter.site"><option value="">{{ t('All accessible sites') }}</option>
        <option v-for="site in sites" :key="site.id" :value="String(site.id)">{{ site.name }}</option></select></label>
      <label>{{ t('Status') }}<select v-model="filter.status"><option value="">{{ t('All') }}</option>
        <option v-for="state in states" :key="state" :value="state">{{ label(state) }}</option></select></label>
      <label>{{ t('Priority') }}<select v-model="filter.priority"><option value="">{{ t('All') }}</option>
        <option v-for="priority in priorities" :key="priority" :value="priority">{{ label(priority) }}</option></select></label>
      <label class="check"><input v-model="filter.overdue" type="checkbox"> {{ t('Overdue only') }}</label>
      <button class="secondary">{{ t('Apply filters') }}</button>
    </form>
    <div v-if="page?.results.length" class="table-panel">
      <table><thead><tr><th>{{ t('Ticket') }}</th><th>{{ t('Subject') }}</th><th>{{ t('Site') }}</th>
        <th>{{ t('Priority') }}</th><th>{{ t('Status') }}</th><th>{{ t('Resolution due') }}</th></tr></thead>
        <tbody><tr v-for="row in page.results" :key="row.id">
          <td><RouterLink :to="base+'/'+row.id">{{ row.number }}</RouterLink></td><td>{{ row.subject }}</td>
          <td>{{ siteName(row.site_id) }}</td><td>{{ label(row.priority) }}</td><td>{{ label(row.status) }}</td>
          <td>{{ fmt(row.resolution_due) }} <strong v-if="row.overdue">{{ t('Overdue') }}</strong></td>
        </tr></tbody></table>
    </div>
    <p v-else-if="!loading && page" class="panel empty">{{ t('No tickets found.') }}</p>
    <TableToolbar v-if="page" :page="page.page" :size="pageSize" :count="page.count" @page="load($event)" @size="pageSize=$event;load(1)"/>
  </template>

  <template v-if="(entry==='new' && requestSites.length) || ticket">
    <div v-if="ticket" class="panel">
      <h2>{{ ticket.subject }}</h2>
      <p>{{ siteName(ticket.site_id) }} · {{ label(ticket.status) }} · {{ label(ticket.priority) }}</p>
      <p>{{ t('Requester') }} · <bdi>{{ ticket.requester }}</bdi> · {{ t('Assignee') }} · <bdi>{{ ticket.assignee ?? '—' }}</bdi></p>
      <p v-if="!ticket.can_edit" style="white-space: pre-wrap">{{ ticket.description }}</p>
      <dl><dt>{{ t('First response due') }}</dt><dd>{{ fmt(ticket.first_response_due) }}</dd>
        <dt>{{ t('First response') }}</dt><dd>{{ fmt(ticket.first_response_at) }}</dd>
        <dt>{{ t('Resolution due') }}</dt><dd>{{ fmt(ticket.resolution_due) }}</dd>
        <dt>{{ t('Resolved at') }}</dt><dd>{{ fmt(ticket.resolved_at) }}</dd></dl>
      <p v-if="ticket.overdue" class="alert">{{ t('SLA overdue') }}</p>
    </div>
    <form v-if="!ticket || ticket.can_edit" v-validated-form class="panel record-form" @submit.prevent="submitTicket">
      <label v-if="!ticket">{{ t('Site') }}<select v-model="form.site_id" data-field="site_id" required @change="changeSite">
        <option v-for="site in requestSites" :key="site.id" :value="site.id">{{ site.name }}</option></select></label>
      <label>{{ t('Subject') }}<input v-model="form.subject" data-field="subject" required maxlength="140" :placeholder="t('Briefly describe the issue')"></label>
      <label>{{ t('Description') }}<textarea v-model="form.description" data-field="description" required maxlength="10000" :placeholder="t('What happened and what is affected?')"></textarea></label>
      <label>{{ t('Category') }}<select v-model="form.category_id" data-field="category_id">
        <option :value="null">{{ t('No category') }}</option>
        <option v-for="row in categories?.results" :key="row.id" :value="row.id">{{ locale==='ar' ? row.name_ar : row.name_en }}</option></select></label>
      <label v-if="!ticket">{{ t('Priority') }}<select v-model="form.priority" data-field="priority">
        <option v-for="priority in priorities" :key="priority" :value="priority">{{ label(priority) }}</option></select></label>
      <div class="actions"><button class="primary" :disabled="busy || loading">{{ t(ticket ? 'Save' : 'Open ticket') }}</button></div>
    </form>
  </template>

  <template v-if="ticket">
    <form v-if="ticket.can_assign && !['resolved','closed'].includes(ticket.status)" v-validated-form class="panel record-form" @submit.prevent="act('assign',{assignee_id:assignee})">
      <EntityAutocomplete
        data-field="assignee_id"
        :label="t('Assignee')"
        :choices="ticket.candidates ?? []"
        :placeholder="t('Select an agent')"
        :empty-value="0"
        required
        v-model="assignee"
      />
      <div class="actions"><button class="secondary" :disabled="busy || !assignee">{{ t('Assign ticket') }}</button></div>
    </form>
    <form v-if="ticket.transitions.length" v-validated-form class="panel record-form" @submit.prevent="act('transition',{status:destination,reason})">
      <label for="helpdesk-status">{{ t('New status') }}<select id="helpdesk-status" v-model="destination" data-field="status" required><option value="" disabled>{{ t('Select a status') }}</option>
        <option v-for="state in ticket.transitions" :key="state" :value="state">{{ label(state) }}</option></select></label>
      <label>{{ t('Reason') }}<textarea v-model="reason" data-field="reason" maxlength="500" :required="destination==='in_progress' && ['resolved','closed'].includes(ticket.status)" :placeholder="t('Explain the status change')"></textarea></label>
      <div class="actions"><button class="secondary" :disabled="busy || !ticket.transitions.includes(destination)">{{ t('Change status') }}</button></div>
    </form>
    <section class="panel">
      <h2>{{ t('Comments') }}</h2>
      <article v-for="row in replies?.results" :key="row.id">
        <strong><bdi>{{ row.author }}</bdi></strong> · {{ fmt(row.created_at) }} <span v-if="row.internal" class="badge">{{ t('Internal note') }}</span>
        <p style="white-space: pre-wrap">{{ row.body }}</p>
      </article>
      <div v-if="replies?.next || replies?.previous" class="actions">
        <button class="secondary" :disabled="!replies.previous" @click="save(() => journal('comments',replies!.page-1),async () => {})">{{ t('Previous') }}</button>
        <button class="secondary" :disabled="!replies.next" @click="save(() => journal('comments',replies!.page+1),async () => {})">{{ t('Next') }}</button>
      </div>
      <form v-if="!['resolved','closed'].includes(ticket.status)" v-validated-form class="record-form" @submit.prevent="reply">
        <label>{{ t('Comment') }}<textarea v-model="note.body" data-field="body" required maxlength="8000" :placeholder="t('Write a reply')"></textarea></label>
        <label v-if="ticket.can_use_internal_notes" class="check"><input v-model="note.internal" type="checkbox"> {{ t('Internal note') }}</label>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Add comment') }}</button></div>
      </form>
    </section>
    <section v-if="ticket.links.length || ticket.can_assign && ticket.status!=='closed'" class="panel">
      <h2>{{ t('Linked records') }}</h2>
      <ul v-if="ticket.links.length"><li v-for="link in ticket.links" :key="link.id"><RouterLink :to="link.route">{{ link.label }}</RouterLink></li></ul>
      <form v-if="ticket.can_assign && ticket.status!=='closed'" v-validated-form class="record-form" @submit.prevent="act('links',target)">
        <label>{{ t('Record type') }}<select v-model="target.type" data-field="type" @change="target.object_id=0">
          <option value="work_order">{{ t('Work order') }}</option><option value="project_task">{{ t('Project task') }}</option></select></label>
        <label>{{ t('Record') }}<select v-model="target.object_id" data-field="object_id" required><option :value="0" disabled>{{ t('Select a record') }}</option>
          <option v-for="link in links" :key="link.id" :value="link.id">{{ link.name }}</option></select></label>
        <div class="actions"><button class="secondary" :disabled="busy || !target.object_id">{{ t('Link record') }}</button></div>
      </form>
    </section>
    <section class="panel">
      <h2>{{ t('History') }}</h2>
      <ul><li v-for="row in history?.results" :key="row.id">
        {{ label(row.event) }} · <bdi>{{ row.actor ?? t('System') }}</bdi> · {{ fmt(row.created_at) }}
        <span v-if="row.event==='transition' || row.event==='resolved'"> · {{ label(row.previous) }} → {{ label(row.current) }}</span>
        <span v-if="row.reason"> · {{ row.reason }}</span>
      </li></ul>
      <div v-if="history?.next || history?.previous" class="actions">
        <button class="secondary" :disabled="!history.previous" @click="save(() => journal('events',history!.page-1),async () => {})">{{ t('Previous') }}</button>
        <button class="secondary" :disabled="!history.next" @click="save(() => journal('events',history!.page+1),async () => {})">{{ t('Next') }}</button>
      </div>
    </section>
  </template>

  <template v-if="entry==='settings' && configSites.length">
    <div class="panel record-form"><label>{{ t('Site') }}
      <select v-model="form.site_id" @change="changeSite"><option v-for="site in configSites" :key="site.id" :value="site.id">{{ site.name }}</option></select>
    </label></div>
    <section class="panel">
      <h2>{{ t('Ticket categories') }}</h2>
      <ul><li v-for="row in categories?.results" :key="row.id">
        <button type="button" class="secondary" @click="editCategory(row)">{{ t('Edit') }} · {{ row.code }}</button>
        {{ locale==='ar' ? row.name_ar : row.name_en }} · {{ t(row.active ? 'Active' : 'Inactive') }}
      </li></ul>
      <div v-if="categories?.next || categories?.previous" class="actions">
        <button class="secondary" :disabled="!categories.previous" @click="config(form.site_id,categories!.page-1)">{{ t('Previous') }}</button>
        <button class="secondary" :disabled="!categories.next" @click="config(form.site_id,categories!.page+1)">{{ t('Next') }}</button>
      </div>
      <form v-validated-form class="record-form" @submit.prevent="saveCategory">
        <label>{{ t('Code') }}<input v-model="category.code" data-field="code" maxlength="30" required :disabled="!!category.id" placeholder="IT"></label>
        <label>{{ t('Arabic name') }}<input v-model="category.name_ar" data-field="name_ar" maxlength="80" required placeholder="تقنية المعلومات"></label>
        <label>{{ t('English name') }}<input v-model="category.name_en" data-field="name_en" maxlength="80" required placeholder="IT Support"></label>
        <label v-if="category.id" class="check"><input v-model="category.active" type="checkbox"> {{ t('Active') }}</label>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Save category') }}</button>
          <button v-if="category.id" type="button" class="secondary" @click="editCategory()">{{ t('Cancel') }}</button></div>
      </form>
    </section>
    <section class="panel">
      <h2>{{ t('SLA policies') }}</h2>
      <ul><li v-for="row in policies?.results" :key="row.id">{{ label(row.priority) }} · {{ t('Version') }} {{ row.version }} · {{ row.first_response_minutes }} / {{ row.resolution_minutes }} {{ t('Minutes') }}</li></ul>
      <div v-if="policies?.next || policies?.previous" class="actions">
        <button class="secondary" :disabled="!policies.previous" @click="config(form.site_id,policies!.page-1)">{{ t('Previous') }}</button>
        <button class="secondary" :disabled="!policies.next" @click="config(form.site_id,policies!.page+1)">{{ t('Next') }}</button>
      </div>
      <form v-validated-form class="record-form" @submit.prevent="save(() => post(base+'/sla-policies',{...policy,site_id:form.site_id}), () => config(form.site_id))">
        <label>{{ t('Priority') }}<select v-model="policy.priority" data-field="priority">
          <option v-for="priority in priorities" :key="priority" :value="priority">{{ label(priority) }}</option></select></label>
        <label>{{ t('First response minutes') }}<input v-model.number="policy.first_response_minutes" data-field="first_response_minutes" type="number" min="1" required></label>
        <label>{{ t('Resolution minutes') }}<input v-model.number="policy.resolution_minutes" data-field="resolution_minutes" type="number" min="1" required></label>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Save SLA version') }}</button></div>
      </form>
    </section>
  </template>
</template>
