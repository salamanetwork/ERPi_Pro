<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
const pg=ref(1),ps=ref(25),term=ref(''),total=ref(0)
const paging=()=>new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value})

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, requestKey, type Workspace } from './api'
import { message, t } from './i18n'
import SavedViews from './SavedViews.vue'
import CsvExport from './CsvExport.vue'

type Center = {id:number;code:string;name:string;site_id:number|null;active:boolean|null;revision:number}
type Account = {id:number;code:string;name:string;active:boolean}
type Budget = {id:number;site_id:number;center_id:number;account_id:number;year:number;version:number;state:string;current:boolean;
  threshold:number;reason:string;revision:number;months:Record<string,string>;created_by_id:number;created_by?:string;approved_by?:string}
type Row = {site_id:number|null;center_id:number|null;account_id:number;month:number;site:string;center:string;account:string;
  budget:string|null;debit:string;credit:string;actual:string;remaining:string|null;usage:string|null;status:string;budget_id:number|null}
type Detail = {count:number;page:number;month:number;debit:string;credit:string;actual:string;
  budgets:{id:number;version:number;amount:string;reason:string}[];
  items:{entry_id:number;number:string;date:string;description:string;debit:string;credit:string}[]}
type Filters = Record<string,string>
const {workspace} = defineProps<{workspace:Workspace}>()
const route=useRoute(), router=useRouter()
const page = computed(() => String(route.params.section ?? 'report'))
const centers = ref<Center[]>([])
const accounts = ref<Account[]>([])
const budgets = ref<Budget[]>([])
const centerEdit = ref<Partial<Center>>()
const draft = ref<Partial<Budget>>()
const selected = ref<Budget>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
const reason = ref('')
const filters = ref<Filters>({})
type Comparison = {count:number;filters:Filters;items:Row[];ytd:Row}
const report = ref<Comparison>()
const detail = ref<Detail>()
const months = Array.from({length:12},(_,i)=>String(i+1))
const permits = (action:string, site?:number|null) => allows(workspace,'budget.'+action,site)
const siteName = (id?:number|null) => workspace.sites.find(row=>row.id===id)?.name ?? t('Unassigned')
const centerName = (id?:number) => centers.value.find(row=>row.id===id)?.name ?? '#' + id
const accountName = (id?:number) => accounts.value.find(row=>row.id===id)?.name ?? '#' + id
const stateText:Record<string,string> = {draft:'Draft', submitted:'Submitted', approved:'Approved', warning:'Near budget limit',
  over:'Over budget', unbudgeted:'Without budget', ok:'Within budget'}
const rules = ['year','site','center','account','as_of']
const live = computed(()=>Object.fromEntries(Object.entries(route.query).filter(([k,v])=>rules.includes(k) && typeof v==='string')) as Filters)
let replay:{input:string;key:string}|undefined
let sequence=0
async function create(path:string,data:object) {
  const input=JSON.stringify([path,data])
  if (replay?.input!==input) replay={input,key:requestKey()}
  const result=await api(path,'POST',data,replay.key)
  replay=undefined
  return result
}
async function run(fn:()=>Promise<void>) {
  if (busy.value) return
  busy.value=true
  error.value=notice.value=''
  try {await fn()} catch(failure) {error.value=message(failure)} finally {busy.value=false}
}
async function load() {
  const token=++sequence
  const section=page.value
  const query={...live.value}
  error.value=''
  detail.value=undefined
  report.value=undefined
  try {
    const [directory, catalog] = await Promise.all([
      api<{items:Center[];count?:number}>('/budgeting/centers'+(section==='centers'?'?'+paging():'')),
      api<{items:Budget[];accounts:Account[];count?:number}>('/budgeting/budgets'+(section==='budgets'&&!route.query.budget?'?'+paging():'')),
    ])
    if (sequence!==token) return
    total.value=section==='centers'?(directory.count??directory.items.length):(catalog.count??catalog.items.length);centers.value=directory.items
    accounts.value=catalog.accounts
    budgets.value=catalog.items
    if(section==='report'){
      const result=await api<Comparison>('/budgeting/report?'+new URLSearchParams(query)+'&'+paging())
      if (sequence!==token) return
      total.value=result.count;report.value=result
      filters.value={...result.filters}
    } else if (section==='details') {
      const params={...query, month:String(route.query.month ?? ''), page:String(pg.value),page_size:String(ps.value),q:term.value}
      const result=await api<Detail>('/budgeting/report/details?'+new URLSearchParams(params))
      if (sequence===token){detail.value=result;total.value=result.count}
    } else if (section==='budgets') {
      const id=Number(route.query.budget)
      selected.value=budgets.value.find(row=>row.id===id)
    }
  } catch(failure) {if (sequence===token) error.value=message(failure)}
}
watch([pg,ps,term],load)
watch(()=>route.fullPath,()=>{void load()},{immediate:true})
function apply(query:Filters){
  void router.push({path:'/budgeting/report',query:Object.fromEntries(Object.entries(query).filter(([,v])=>v))}).then(failure=>{
    if(failure) void load()
  })
}
const link = (row:Row) => ({path:'/budgeting/details',query:{...report.value!.filters,
  site:row.site_id ?? 'unassigned',center:row.center_id ?? 'unassigned', account:row.account_id,month:row.month,
  back:new URLSearchParams(report.value!.filters).toString()}})
const back = computed(()=>Object.fromEntries(new URLSearchParams(typeof route.query.back==='string'?route.query.back:'')))
function saveCenter(){
  const row=centerEdit.value
  if (!row) return
  return run(async()=>{
    if(row.id) await api('/budgeting/centers/'+row.id,'PATCH',row)
    else await create('/budgeting/centers',row)
    centerEdit.value=undefined
    await load()
    notice.value='Changes saved.'
  })
}
function newVersion(row?:Budget){
  draft.value=row ? {...row,id:undefined,reason:'',months:{...row.months}} :
    {year:new Date().getFullYear(), threshold:80,months:{},reason:''}
}
function editBudget(row:Budget){
  draft.value={...row,months:{...row.months}}
}
function saveBudget(){
  const row=draft.value
  if(!row) return
  return run(async()=>{
    const data={...row,months:Object.fromEntries(Object.entries(row.months ?? {}).filter(([,value])=>value!==''))}
    if(row.id) await api('/budgeting/budgets/'+row.id,'PATCH',data)
    else await create('/budgeting/budgets',data)
    draft.value=undefined
    await load()
    notice.value='Changes saved.'
  })
}
function advance(row:Budget,action:string){
  return run(async()=>{
    await api('/budgeting/budgets/'+row.id,'POST',{action,revision:row.revision,reason:reason.value})
    reason.value=''
    await load()
    notice.value='Changes saved.'
  })
}
import DateInput from './DateInput.vue'
</script>

<template>
  <nav class="section-tabs">
    <RouterLink class="tab-link" to="/budgeting/centers" :aria-current="page === 'centers' ? 'page' : undefined">{{ t('Cost centers') }}</RouterLink>
    <RouterLink class="tab-link" to="/budgeting/budgets" :aria-current="page === 'budgets' ? 'page' : undefined">{{ t('Budgets') }}</RouterLink>
    <RouterLink class="tab-link" to="/budgeting/report" :aria-current="page === 'report' ? 'page' : undefined">{{ t('Budget vs Actual') }}</RouterLink>
  </nav>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <template v-if="page==='centers'">
    <h1>{{ t('Cost centers') }}</h1>
    <button v-if="permits('centers') && !centerEdit" class="primary" @click="centerEdit={active:true}">{{ t('New cost center') }}</button>
    <form v-validated-form v-if="centerEdit" class="panel record-form" @submit.prevent="saveCenter">
      <div class="field-wrap"><label>{{ t('Code') }}<input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="centerEdit.code" maxlength="24" required :readonly="!!centerEdit.id"></label><FieldHint :label="t('Code')" required :readonly="!!centerEdit.id" /></div>
      <div class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="centerEdit.name" maxlength="120" required></label><FieldHint :label="t('Name')" required /></div>
      <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site_id" v-model="centerEdit.site_id" required :disabled="!!centerEdit.id && centerEdit.revision!==0"><option v-for="site in workspace.sites.filter(s=>s.active && permits('centers',s.id))" :key="site.id" :value="site.id">{{ site.name }}</option></select></label><FieldHint :label="t('Site')" required /></div>
      <div class="field-wrap" v-if="centerEdit.id"><label><input data-field="active" v-model="centerEdit.active" type="checkbox">{{ t('Active') }}</label><FieldHint :label="t('Active')" /></div>
      <div class="actions"><button class="primary" :disabled="busy">{{ t('Save') }}</button><button class="secondary" type="button" @click="centerEdit=undefined">{{ t('Cancel') }}</button></div>
    </form>
    <div class="table-panel"><TableToolbar :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table><thead><tr><th>{{ t('Code') }}</th><th>{{ t('Name') }}</th><th>{{ t('Site') }}</th><th>{{ t('Status') }}</th><th>{{ t('Action') }}</th></tr></thead>
      <tbody><tr v-for="row in centers" :key="row.id"><td>{{ row.code }}</td><td>{{ row.name }}</td><td>{{ siteName(row.site_id) }}</td>
        <td>{{ t(row.active===null?'Historical center':row.active?'Active':'Inactive') }}</td>
        <td><button v-if="permits('centers',row.site_id)" class="secondary" @click="centerEdit={...row,active:row.active??true}">{{ t('Edit') }}</button></td></tr></tbody></table></div>
  </template>
  <template v-else-if="page==='budgets'">
    <h1>{{ t('Budgets') }}</h1>
    <button v-if="permits('prepare') && !draft" class="primary" @click="newVersion()">{{ t('New budget') }}</button>
    <form v-validated-form v-if="draft" class="panel record-form" @submit.prevent="saveBudget">
      <div class="actions">
        <div class="field-wrap"><label>{{ t('Financial year') }}<input data-field="year" :placeholder="t('Enter {field}').replace('{field}', t('Financial year'))" v-model.number="draft.year" type="number" min="1900" max="9998" :readonly="!!draft.id" required></label><FieldHint :label="t('Financial year')" required :readonly="!!draft.id" /></div>
        <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site_id" v-model="draft.site_id" :disabled="!!draft.id" required @change="draft.center_id=undefined">
          <option v-for="site in workspace.sites.filter(s=>s.active && permits('prepare',s.id))" :key="site.id" :value="site.id">{{ site.name }}</option></select></label><FieldHint :label="t('Site')" required /></div>
        <div class="field-wrap"><label>{{ t('Cost center') }}<EntityAutocomplete v-model="draft.center_id" lookup="cost_centers" :site-id="draft.site_id" :local-items="centers.filter(c=>c.site_id===draft?.site_id&&c.active).map(c => ({ id: c.id, label: c.code + ' · ' + c.name }))" :disabled="!!draft.id" data-field="center_id" required /></label><FieldHint :label="t('Cost center')" required /></div>
        <div class="field-wrap"><label>{{ t('Account') }}<EntityAutocomplete v-model="draft.account_id" lookup="accounts" :local-items="accounts.filter(a=>a.active).map(a => ({ id: a.id, label: a.code + ' · ' + a.name }))" :disabled="!!draft.id" data-field="account_id" required /></label><FieldHint :label="t('Account')" required /></div>
      </div>
      <div class="card-grid" v-if="draft.months">
        <div class="field-wrap" v-for="month in months" :key="month"><label>{{ t('Month') }} {{ month }}<input v-model="draft.months[month]" inputmode="decimal" placeholder="—"></label><FieldHint :label="t('Month')" /></div>
      </div>
      <p>{{ t('Annual total') }} · {{ Object.values(draft.months ?? {}).reduce((sum,value)=>sum+(Number(value)||0),0).toFixed(2) }} AED</p>
      <div class="field-wrap"><label>{{ t('Warning threshold (%)') }}<input data-field="threshold" :placeholder="t('Enter {field}').replace('{field}', t('Warning threshold (%)'))" v-model.number="draft.threshold" type="number" min="1" max="100" required></label><FieldHint :label="t('Warning threshold (%)')" required /></div>
      <div class="field-wrap"><label>{{ t('Reason') }}<textarea data-field="reason" :placeholder="t('Explain the reason')" v-model="draft.reason" maxlength="1000" rows="2"></textarea></label><FieldHint :label="t('Reason')" /></div>
      <div class="actions"><button class="primary" :disabled="busy">{{ t('Save draft') }}</button><button type="button" class="secondary" @click="draft=undefined">{{ t('Cancel') }}</button></div>
    </form>
    <section v-if="selected" class="panel record-form">
      <h2>{{ t('Version') }} {{ selected.version }} · {{ centerName(selected.center_id) }}</h2>
      <p>{{ accountName(selected.account_id) }} · {{ selected.year }} · {{ t(stateText[selected.state] ?? selected.state) }}</p>
      <p v-if="selected.reason">{{ t('Reason') }} · {{ selected.reason }}</p>
      <p><span v-for="(amount,month) in selected.months" :key="month">{{ month }}: {{ amount }} AED · </span></p>
      <div class="actions">
        <template v-if="selected.state==='draft' && selected.created_by===workspace.username && permits('prepare',selected.site_id)">
          <button class="secondary" @click="editBudget(selected)">{{ t('Edit') }}</button>
          <button class="primary" :disabled="busy" @click="advance(selected,'submit')">{{ t('Submit budget') }}</button>
        </template>
        <template v-if="selected.state==='submitted' && selected.created_by!==workspace.username && permits('approve',selected.site_id)">
          <button class="primary" :disabled="busy" @click="advance(selected,'approve')">{{ t('Approve budget') }}</button>
          <div class="field-wrap"><label>{{ t('Reason') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="reason" maxlength="1000"></label><FieldHint :label="t('Reason')" /></div>
          <button class="secondary" :disabled="busy||!reason.trim()" @click="advance(selected,'return')">{{ t('Return for changes') }}</button>
        </template>
        <button v-if="selected.state==='approved' && permits('prepare',selected.site_id)" class="secondary" @click="newVersion(selected)">{{ t('New budget version') }}</button>
      </div>
    </section>
    <div class="table-panel"><TableToolbar :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table><thead><tr><th>{{ t('Site') }}</th><th>{{ t('Cost center') }}</th><th>{{ t('Account') }}</th><th>{{ t('Financial year') }}</th><th>{{ t('Version') }}</th><th>{{ t('Status') }}</th></tr></thead>
      <tbody><tr v-for="row in budgets" :key="row.id"><td>{{siteName(row.site_id)}}</td><td>{{centerName(row.center_id)}}</td><td>{{accountName(row.account_id)}}</td><td>{{row.year}}</td>
        <td><RouterLink :to="{path:'/budgeting/budgets',query:{budget:row.id}}">{{row.version}}</RouterLink></td>
        <td>{{ t(stateText[row.state] ?? row.state) }} <span v-if="row.current">· {{ t('Current') }}</span></td></tr></tbody></table></div>
  </template>
  <template v-else-if="page==='details'">
    <RouterLink :to="{path:'/budgeting/report',query:back}">{{ t('Back to budget report') }}</RouterLink>
    <h1>{{ t('Budget details') }}</h1>
    <section v-if="detail" class="panel record-form">
      <p>{{ t('Month') }} {{ detail.month }} · {{ t('Actual expense') }} {{ detail.debit }} − {{ detail.credit }} = {{ detail.actual }} AED</p>
      <p>{{ t('Site') }} · {{ siteName(Number(route.query.site)) }} · {{ t('Cost center') }} · {{ route.query.center==='unassigned'?t('Unassigned'):centerName(Number(route.query.center)) }} · {{ accountName(Number(route.query.account)) }} · {{route.query.as_of}}</p>
      <ul><li v-for="row in detail.budgets" :key="row.id"><RouterLink :to="{path:'/budgeting/budgets',query:{budget:row.id}}">{{ t('Version') }} {{ row.version }}</RouterLink> · {{ row.amount }} AED · {{ row.reason }}</li></ul>
      <div class="table-panel"><TableToolbar :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table><thead><tr><th>{{t('Date')}}</th><th>{{t('Journal entry')}}</th><th>{{t('Description')}}</th><th>{{t('Debit')}}</th><th>{{t('Credit')}}</th></tr></thead>
        <tbody><tr v-for="(row,index) in detail.items" :key="index"><td>{{row.date}}</td><td>
          <RouterLink v-if="allows(workspace,'accounting.view',null)" :to="{path:'/accounting/journals',query:{entry:row.entry_id}}">{{row.number}}</RouterLink>
          <span v-else>{{row.number}}</span></td><td>{{row.description}}</td><td>{{row.debit}}</td><td>{{row.credit}}</td></tr></tbody></table></div>
      
    </section>
  </template>
  <template v-else>
    <h1>{{ t('Budget vs Actual') }}</h1>
    <form v-validated-form class="actions" @submit.prevent="apply(filters)">
      <div class="field-wrap"><label>{{ t('Financial year') }}<input data-field="year" :placeholder="t('Enter {field}').replace('{field}', t('Financial year'))" v-model="filters.year" inputmode="numeric" @change="filters.as_of=''"></label><FieldHint :label="t('Financial year')" /></div>
      <DateInput data-field="as_of" :label="t('As of date')" v-model="filters.as_of" type="date" />
      <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site" v-model="filters.site"><option value="">{{ t('All sites') }}</option><option v-for="site in workspace.sites.filter(s=>permits('view',s.id))" :key="site.id" :value="String(site.id)">{{site.name}}</option></select></label><FieldHint :label="t('Site')" /></div>
      <div class="field-wrap"><label>{{ t('Cost center') }}<select data-field="center" v-model="filters.center"><option value="">{{t('all')}}</option><option value="unassigned">{{t('Unassigned')}}</option>
        <option v-for="c in centers" :key="c.id" :value="String(c.id)">{{c.code}} · {{c.name}}</option></select></label><FieldHint :label="t('Cost center')" /></div>
      <div class="field-wrap"><label>{{ t('Account') }}<select data-field="account" v-model="filters.account"><option value="">{{t('all')}}</option><option v-for="a in accounts" :key="a.id" :value="String(a.id)">{{a.code}} · {{a.name}}</option></select></label><FieldHint :label="t('Account')" /></div>
      <button class="primary">{{ t('Run report') }}</button>
    </form>
    <template v-if="report">
      <SavedViews :workspace="workspace" screen="budget-vs-actual" :filters="filters" :skip-default="Object.keys(route.query).length!==0" :hide-export="true" @apply="apply" />
      <CsvExport :workspace="workspace" screen="budget-vs-actual" :filters="report.filters" />
      <p>{{ t('As of date') }} · {{ report.filters.as_of }} · {{ t('Year to date') }} · {{ t('Budget') }} {{ report.ytd.budget }} AED · {{ t('Actual expense') }} {{ report.ytd.actual }} AED · {{t('Remaining')}} {{report.ytd.remaining}} AED</p>
      <div class="table-panel"><TableToolbar :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table><thead><tr><th v-for="title in ['Month','Site','Cost center','Account','Budget','Debit','Credit','Actual expense','Remaining','Usage (%)','Status']" :key="title">{{t(title)}}</th></tr></thead>
        <tbody><tr v-for="row in report.items" :key="[row.month,row.site_id,row.center_id,row.account_id].join(':')">
          <td><RouterLink :to="link(row)">{{row.month}}</RouterLink></td><td>{{row.site||t('Unassigned')}}</td><td>{{row.center||t('Unassigned')}}</td><td>{{row.account}}</td>
          <td>{{row.budget??'—'}}</td><td>{{row.debit}}</td><td>{{row.credit}}</td><td>{{row.actual}}</td><td>{{row.remaining??'—'}}</td><td>{{row.usage??'—'}}</td><td>{{t(stateText[row.status]??row.status)}}</td></tr></tbody></table></div>
    </template>
  </template>
</template>
