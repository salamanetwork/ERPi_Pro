<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {useStudio,local,presets,type Size} from './dashboardStudio'
import { useRoute, useRouter } from 'vue-router'
import { api, allows, type Workspace } from './api'
import { actionLabels } from './experience'
import { locale, message, t } from './i18n'
import { activeScreen, groups, screenVisible, visibleScreen } from './navigation'
import { bucketLabels, type Aging, type ChartSeries, type Charts, type Filters, type Link, type Summary } from './dashboard'

const DashboardChart = defineAsyncComponent(() => import('./DashboardChart.vue'))
const { workspace, refreshKey } = defineProps<{workspace: Workspace; refreshKey: number}>()
const route = useRoute()
const router = useRouter()
const shortcuts = computed(() => groups.filter(group => group.id !== 'admin').flatMap(group =>
  group.screens.filter(screen => screenVisible(workspace, screen)).slice(0, 1)))
const labels: Record<string, string> = {
  inbox: 'My open tasks', purchase: 'Purchase orders ready to issue', 'net-posted-sales': 'Net posted sales',
  'ap-aging': 'Posted AP aging', 'ar-aging': 'Posted AR aging', grni: 'Historical GRNI',
  'crm-open':'Open opportunities', 'crm-pipeline':'Open pipeline value',
  sales: 'Sales and collections', 'pm-on-time': 'Preventive maintenance completed on time', activities: 'Key activities',
}
const widgetScreen: Record<string, string> = {
  'crm-open':'crm', 'crm-pipeline':'crm', inbox:'inbox', purchase:'purchase_orders', 'net-posted-sales':'sales_documents', sales:'sales_documents',
  'ap-aging':'supplier_invoices', 'ar-aging':'sales_documents', grni:'general_accounting', 'pm-on-time':'maintenance',
}
const reportWidgets = new Set(['net-posted-sales','sales','ap-aging','ar-aging','grni','pm-on-time'])
const agingMetrics = ['ap-aging','ar-aging'] as const
function screen(key: string) {
  const target = widgetScreen[key]
  return (!target || visibleScreen(workspace, target)) && (!reportWidgets.has(key) || visibleScreen(workspace, 'reports'))
}
type Activity = {id?:string; key?:string; route:string; number:string; title?:string; action?:string; event?:string; created_at?:string}
type Preset = 'recent' | 'this-month' | 'last-month' | 'custom'
const draft = ref<Filters>({})
const applied = ref<Filters>({})
const dates = ref<Filters>({})
const summary = ref<Summary>()
const charts = ref<Charts>()
const tasks = ref<Activity[]>([])
const notices = ref<Activity[]>([])
const errors = ref({summary:'', charts:'', tasks:'', notices:''})
const loading = ref({summary:false, charts:false, activities:false})
const preset = ref<Preset>('recent')
const delivery = ref<{pending:number;failed:number;delayed:boolean;broker_ok:boolean}>()
let statusTimer: ReturnType<typeof setInterval> | undefined
let statusLoading = false
async function refreshDelivery() {
  if (document.hidden || statusLoading || !workspace.modules.some(module => module.id === 'helpdesk' && module.enabled)) return
  statusLoading = true
  try { delivery.value = await api('/workspace/notifications/status') }
  catch { /* Keep the last status during an API outage. */ }
  finally { statusLoading = false }
}
const extra=ref<Record<string,Block>>({}),blocksError=ref('')
const loadingBlocks=ref(true)
let sequence = 0
let timer: ReturnType<typeof setTimeout> | undefined

function queryFilters(): Filters {
  return Object.fromEntries(['site','from','to','as_of'].flatMap(key => typeof route.query[key] === 'string' && route.query[key] ? [[key, route.query[key]]] : []))
}
function report(metric: string, filters: Filters): Link {
  const back = new URLSearchParams(dates.value).toString()
  return {path: '/reports/detail/' + metric, query: {...filters, dashboard: back || '1'}}
}
function visibleActivity(row: Activity) {
  const id = activeScreen(row.route, false)
  return id ? visibleScreen(workspace, id) : true
}
function activityLink(row: Activity): Link {
  return row.action ? {path:row.route,query:{fromInbox:applied.value.site ? 'site=' + applied.value.site : '1'}} : {path:row.route}
}
const query = (values: Filters) => '?' + new URLSearchParams(values).toString()
function load(resetDraft = false) {
  const token = ++sequence
  const filters = {...applied.value}
  const activityFilters: Filters = filters.site ? {site:filters.site,limit:'5'} : {limit:'5'}
  loading.value = {summary:true,charts:true,activities:true}
  errors.value = {summary:'',charts:'',tasks:'',notices:''}
  void api<Summary>('/workspace/dashboard' + query(filters)).then(result => {
    if (token !== sequence) return
    summary.value = result
    dates.value = result.filters
    if (resetDraft) {
      draft.value = {site:'',...result.filters}
      preset.value = (['recent','this-month','last-month'] as const).find(value => {
        const range = presetDates(value, result.today)
        return range.from === result.filters.from && range.to === result.filters.to
      }) ?? 'custom'
    }
  }).catch(error => {if (token === sequence) errors.value.summary = message(error)})
    .finally(() => {if (token === sequence) loading.value.summary = false})
  void api<Charts>('/workspace/dashboard/charts' + query(filters)).then(result => {
    if (token === sequence) charts.value = result
  }).catch(error => {if (token === sequence) errors.value.charts = message(error)})
    .finally(() => {if (token === sequence) loading.value.charts = false})
  loadingBlocks.value=true
  extra.value={}
  blocksError.value=''
  void api<{widgets:Record<string,Block>}>('/workspace/dashboard/blocks'+query(filters)).then(r=>{if(token===sequence)extra.value=r.widgets}).catch(e=>{if(token===sequence)blocksError.value=message(e)}).finally(()=>{if(token===sequence)loadingBlocks.value=false})
  const inbox = visibleScreen(workspace, 'inbox') ? api<{items:Activity[]}>('/inbox' + query(activityFilters)) : Promise.resolve({items:[]})
  const alerts = visibleScreen(workspace, 'notifications') ? api<{items:Activity[]}>('/notifications' + query(activityFilters)) : Promise.resolve({items:[]})
  void Promise.allSettled([inbox, alerts]).then(([open, recent]) => {
    if (token !== sequence) return
    tasks.value = open.status === 'fulfilled' ? open.value.items.filter(visibleActivity) : []
    notices.value = recent.status === 'fulfilled' ? recent.value.items.filter(visibleActivity) : []
    errors.value.tasks = open.status === 'rejected' ? message(open.reason) : ''
    errors.value.notices = recent.status === 'rejected' ? message(recent.reason) : ''
    loading.value.activities = false
  })
}
function clearAndLoad() {
  applied.value = queryFilters()
  draft.value = {...applied.value}
  summary.value = charts.value = undefined
  tasks.value = notices.value = []
  preset.value = applied.value.from || applied.value.to ? 'custom' : 'recent'
  load(true)
}
function refresh() {
  if (document.hidden) return
  clearTimeout(timer)
  timer = setTimeout(load, 180)
}
function presetDates(value: Exclude<Preset, 'custom'>, today: string) {
  const end = new Date(today + 'T12:00:00Z')
  const start = new Date(end)
  if (value === 'recent') start.setUTCDate(start.getUTCDate() - 29)
  if (value === 'this-month') start.setUTCDate(1)
  if (value === 'last-month') {
    end.setUTCDate(0)
    start.setTime(end.getTime())
    start.setUTCDate(1)
  }
  return {from:start.toISOString().slice(0,10), to:end.toISOString().slice(0,10)}
}
function setPreset(value: Preset) {
  if (value !== 'custom' && summary.value) draft.value = {...draft.value, ...presetDates(value, summary.value.today)}
}
function apply() {
  const selected = Object.fromEntries(Object.entries(draft.value).filter(([,value]) => value))
  if (query(selected) === query(applied.value)) load()
  else void router.replace({path:'/',query:selected})
}
watch(() => route.fullPath, clearAndLoad, {immediate:true})
watch(() => refreshKey, refresh)
onMounted(() => {
  window.addEventListener('erp:work-changed', refresh)
  void refreshDelivery()
  statusTimer = setInterval(refreshDelivery, 5000)
})
onBeforeUnmount(() => { ++sequence; clearTimeout(timer); clearInterval(statusTimer); window.removeEventListener('erp:work-changed', refresh) })

const realWidgets = computed(() => {
  const flags: Record<string, boolean> = {
    'crm-open':Boolean(summary.value?.crm),'crm-pipeline':Boolean(summary.value?.crm),
    inbox: Boolean(summary.value), purchase: summary.value?.ready_purchase_orders !== undefined,
    'net-posted-sales': Boolean(summary.value?.cards['net-posted-sales']), grni: Boolean(summary.value?.cards.grni),
    'ap-aging': Boolean(charts.value?.widgets['ap-aging']), 'ar-aging': Boolean(charts.value?.widgets['ar-aging']),
    sales: Boolean(charts.value?.widgets.sales), 'pm-on-time': Boolean(charts.value?.widgets['pm-on-time']),
    activities: visibleScreen(workspace,'inbox') || visibleScreen(workspace,'notifications'),
  }
  return Object.keys(flags).filter(key => flags[key] && screen(key))
})
const available = (key: string) => realWidgets.value.includes(key)
type DisplayCard = {id:string; basis:string; value?:number; currencies?:Record<string,string>; link:Link; filters?:Filters}
const cards = computed<DisplayCard[]>(() => {
  const cards: DisplayCard[] = []
  const site: Filters = applied.value.site ? {site:applied.value.site} : {}
  if (summary.value && available('inbox')) cards.push({id:'inbox',basis:'current',value:summary.value.inbox,link:{path:'/inbox',query:{...site,fromTask:'1'}}})
  if (summary.value?.ready_purchase_orders !== undefined && available('purchase')) cards.push({id:'purchase',basis:'current',
    value:summary.value.ready_purchase_orders,link:{path:'/purchase-orders',query:{...site,ready:'1'}}})
  const crm=summary.value?.crm
  if(crm){
    const link={path:'/crm/opportunities',query:crm.filters}
    if(available('crm-open')) cards.push({id:'crm-open',basis:'current',value:crm.count,link})
    if(available('crm-pipeline')) cards.push({id:'crm-pipeline',basis:'current',currencies:crm.currencies,link})
  }
  for (const id of ['net-posted-sales','grni'] as const) {
    const card = summary.value?.cards[id]
    if (card && available(id)) cards.push({id,basis:id === 'grni'?'as_of':'period',currencies:card.currencies,filters:card.filters,link:report(id,card.filters)})
  }
  return cards
})
const sales = computed(() => {
  const series: Record<string, ChartSeries[]> = {}
  for (const trend of charts.value?.widgets.sales ?? []) {
    const name = t(trend.metric === 'net-posted-sales' ? 'Net posted sales' : 'Customer collections')
    const currency = series[trend.currency] ??= []
    currency.push({name, points:trend.points.map(point => ({
      value:point.value,label:dateInterval(point.from,point.to),link:report(trend.metric,point.filters),
    }))})
  }
  return series
})
function agingSeries(id: string, series: Aging['series'][number]): ChartSeries[] {
  return [{name:t(labels[id]!),points:series.points.map(point => ({
    value:point.value,label:t(bucketLabels[point.bucket] ?? point.bucket),link:report(id,point.filters),
  }))}]
}
const maintenance = computed(() => charts.value?.widgets['pm-on-time'])
const gauge = computed<ChartSeries[]>(() => {
  const row = maintenance.value
  if (!row?.count) return []
  return [{name:t('Completed on time'),points:[{label:t('During period'),value:(row.on_time * 100 / row.count).toFixed(1),link:report('pm-on-time',row.filters)}]}]
})
function dateLabel(date: string) {
  return new Intl.DateTimeFormat(locale.value, {dateStyle:'medium', timeZone:'UTC', calendar:'gregory'}).format(new Date(date+'T00:00:00Z'))
}
function dateInterval(start: string, end: string) { return start === end ? dateLabel(start) : dateLabel(start) + ' – ' + dateLabel(end) }
function caption(filters: Filters) {
  if (filters.from && filters.to) return dateInterval(filters.from,filters.to)
  return filters.as_of ? t('As of date') + ' · ' + dateLabel(filters.as_of) : t('Current position')
}
const today = computed(() => new Intl.DateTimeFormat(locale.value, {weekday:'long',day:'numeric',month:'long',year:'numeric',calendar:'gregory'}).format(new Date()))
const formatter = computed(() => new Intl.NumberFormat(locale.value))
function theme() {return workspace.theme + ':' + document.documentElement.dataset.theme}
const openLink = (link: Link) => void router.push(link)

type Block={currency?:string;count?:number;amount?:string;filters?:Filters;items?:Record<string,string>[]}

const extraInfo:Record<string,[string,string,string]>={
 'subscription-active':['Active subscriptions · Billing Core','اشتراكات نشطة · الفوترة','subscriptions'],
 'subscription-trials':['Trials ending in 7 days · Billing Core','تجارب تنتهي خلال ٧ أيام · الفوترة','subscriptions'],
 'subscription-due':['Outstanding subscription invoices · Billing Core','فواتير اشتراك مستحقة · الفوترة','subscriptions'],
 'subscription-overdue':['Overdue billed balance · Billing Core','رصيد مفوتر متأخر · الفوترة','subscriptions'],
 'bank-lines':['Unreconciled bank lines','سطور البنك غير المطابقة','bank_reconciliation'],'bank-variance':['Bank statement variance','فرق تسوية البنك','bank_reconciliation'],
 'pending-expenses':['Pending expense claims','مطالبات المصروفات المعلقة','expenses'],'expense-due':['Unreimbursed employee balances','مستحقات الموظفين غير المسددة','expenses'],
 'pending-postings':['Pending postings','قيود بانتظار الترحيل','general_accounting'],
 'open-purchases':['Open PO lines','بنود الشراء المفتوحة','purchase_orders'],
 'overdue-purchases':['Overdue PO lines','بنود الشراء المتأخرة','purchase_orders'],
 'late-list':['Overdue purchase orders','أوامر الشراء المتأخرة','purchase_orders'],
 'stock-value':['Current inventory value','قيمة المخزون الحالية','stock_balances'],
 'unbilled-sales':['Unbilled deliveries','تسليمات غير مفوترة','sales_documents'],
 'pm-due':['Due preventive plans','خطط الصيانة المستحقة','maintenance'],
 'maintenance-open':['Open work orders','أوامر الشغل المفتوحة','maintenance'],
 'customers-activity':['Top customer activity by collections','أبرز العملاء حسب التحصيل','sales_documents'],
 transit:['Supply in transit','تحويلات في الطريق','supply'],tickets:['Open tickets','البلاغات المفتوحة','helpdesk'], projects:['Active projects','المشاريع النشطة','projects'],
 requests:['Purchase requisitions','طلبات الشراء','purchase_requests'],receipts:['Goods receipts','استلامات المخزون','goods_receipts'],
 billing:['Recent invoices','أحدث الفواتير','billing_core'],payments:['Receipts','إيصالات التحصيل','billing_core'],queue:['Pending approvals queue','قائمة المهام المعلقة','inbox']
}
const destinations=computed(()=>groups.flatMap(g=>g.screens).filter(s=>screenVisible(workspace,s)))
const actions=[
 {id:'quote',screen:'sales_documents',permission:'sales.quote',en:'New sales quote',ar:'عرض سعر جديد'},
 {id:'invoice',screen:'billing_core',permission:'billing_core.manage',en:'New invoice',ar:'فاتورة جديدة'},
 {id:'request',screen:'purchase_requests',permission:'purchasing.create',en:'New purchase request',ar:'طلب شراء جديد'},
 {id:'work',screen:'work_orders',permission:'workorders.create',en:'New work order',ar:'أمر شغل جديد'}
]
const links=computed(()=>[
 ...destinations.value.map(s=>({id:'go:'+s.id,label:s.label,labelAr:s.labelAr,path:s.path})),
 ...actions.flatMap(a=>{const dest=destinations.value.find(s=>s.id===a.screen)
 return dest&&allows(workspace,a.permission,applied.value.site?Number(applied.value.site):undefined)?[{id:'go:new-'+a.id,label:a.en,labelAr:a.ar,path:dest.path+'?compose=1'+(applied.value.site?'&site='+applied.value.site:'')}]:[]})
])
const shortcut=(id:string)=>links.value.find(s=>s.id===id)
const source=(id:string)=>extra.value[id==='late-list'?'overdue-purchases':id]
const catalog=computed(()=>[...realWidgets.value,...Object.keys(extraInfo).filter(id=>visibleScreen(workspace,extraInfo[id][2])&&(id==='queue'||(source(id)&&(!source(id).filters||visibleScreen(workspace,'reports'))))),...links.value.map(s=>s.id)])
function title(id:string){const dest=shortcut(id);return dest?(locale.value==='ar'&&dest.labelAr?dest.labelAr:t(dest.label)):extraInfo[id]?local(extraInfo[id][0],extraInfo[id][1]):t(labels[id]||id)}
const {layout,active,editing,busy,ready,note,boards,current,options,widgets,editBoard,move,create,reset,remove,sync}=useStudio(workspace.username,catalog)
const dialog=ref<HTMLDialogElement>()
const modal=ref(''),en=ref(''),ar=ref(''),dragged=ref(''),term=ref('')
watch(modal,value=>{if(value&&dialog.value&&!dialog.value.open)dialog.value.showModal()},{flush:'post'})
function open(mode:string){modal.value=mode;term.value='';en.value=mode==='rename'?current.value.en:'';ar.value=mode==='rename'?current.value.ar:''}
function submitName(){if(!en.value.trim()||!ar.value.trim())return
 if(modal.value==='rename')editBoard(b=>{b.en=en.value.trim();b.ar=ar.value.trim()});else create(en.value,ar.value,modal.value==='duplicate');modal.value=''
}
const tableIds=['pending-expenses','activities','queue','billing','payments','late-list','customers-activity']
const chartIds=['sales','pm-on-time','ap-aging','ar-aging']
const category=(id:string)=>id.startsWith('go:')?3:tableIds.includes(id)?2:chartIds.includes(id)?1:0
function sizeOf(id:string):Size{return category(id)===0||category(id)===3?'compact':'medium'}
function setDefault(){layout.value.default=active.value;void sync()}
watch([options,ready,()=>loading.value.charts,()=>loading.value.summary,loadingBlocks],()=>{if(ready.value&&!loadingBlocks.value&&!blocksError.value&&!errors.value.summary&&!errors.value.charts&&!loading.value.summary&&!loading.value.charts&&!options.value.includes(active.value))active.value='general'})
</script>

<template>
  <div class="analytics-workspace">
    <header class="workspace-welcome">
      <div><p class="workspace-today">{{ today }}</p>
        <h1>{{ t('Welcome back') }}<span aria-hidden="true">, </span><bdi>{{ workspace.display_name }}</bdi></h1>
      </div>
      <button v-if="ready" class="secondary" @click="editing=!editing">{{editing?local('Done','تم'):local('Edit dashboard','تعديل اللوحة')}}</button>
    </header>
    <nav class="studio-tabs" :aria-label="local('Dashboards','اللوحات')"><button v-for="id in options" :key="id" :aria-pressed="active===id" @click="active=id;modal=''">{{boards[id][locale]}}</button><button :disabled="!ready||busy" @click="open('new')">＋ {{local('New dashboard','لوحة جديدة')}}</button></nav>
    <p v-if="note" role="status">{{note}}</p>
    <fieldset v-if="editing" :disabled="busy" class="studio-actions"><button @click="open('catalog')">＋ {{local('Add widget','إضافة عنصر')}}</button><button @click="open('duplicate')">{{local('Duplicate','نسخ اللوحة')}}</button><button v-if="!presets[active]" @click="open('rename')">{{local('Rename','تغيير الاسم')}}</button><button :disabled="layout.default===active" @click="setDefault">{{local('Set as default','تعيين كافتراضية')}}</button><button @click="reset">{{local('Reset dashboard','استعادة اللوحة')}}</button><button v-if="!presets[active]" @click="remove">{{local('Delete','حذف')}}</button></fieldset>
    <dialog ref="dialog" v-if="modal" @cancel.prevent="modal=''" class="studio-dialog panel" @keydown.esc="modal=''"><header class="actions"><h2>{{modal==='catalog'?local('Widget catalog','دليل العناصر'):local('Dashboard name','اسم اللوحة')}}</h2><button @click="modal=''" :aria-label="local('Close','إغلاق')">×</button></header>
    <template v-if="modal==='catalog'"><input v-model="term" type="search" :placeholder="t('Search')"><section v-for="(heading,n) in [local('Metrics','المؤشرات'),local('Charts','الرسوم البيانية'),local('Tables & queues','الجداول والمهام'),local('Shortcuts','الاختصارات')]" :key="heading"><h3>{{heading}}</h3><div class="actions"><button v-for="id in catalog.filter(x=>category(x)===n&&title(x).toLowerCase().includes(term.toLowerCase()))" :disabled="busy||current.widgets.some(w=>w.id===id)" @click="editBoard(b=>b.widgets.push({id,size:sizeOf(id)}))">{{title(id)}}</button></div></section></template>
    <form v-else @submit.prevent="submitName" class="stack"><label>{{local('English name','الاسم بالإنجليزية')}}<input v-model="en" required maxlength="100"></label><label>{{local('Arabic name','الاسم بالعربية')}}<input v-model="ar" required maxlength="100" dir="rtl"></label><button class="primary" :disabled="busy">{{t('Save')}}</button></form></dialog>
    <p v-if="delivery?.delayed" class="alert" role="status">
      {{ t('Helpdesk notification delivery is delayed.') }}
      {{ t('Pending deliveries') }}: {{ delivery.pending }} · {{ t('Failed deliveries') }}: {{ delivery.failed }}
    </p>
    <form v-validated-form class="panel dashboard-filters"  @submit.prevent="apply">
      <label>{{ t('Site') }}<select v-model="draft.site" data-field="site">
        <option value="">{{ t('All accessible sites') }}</option>
        <option v-for="item in workspace.sites" :key="item.id" :value="String(item.id)">{{ item.name }}</option>
      </select></label>
      <label>{{ t('Period') }}<select v-model="preset" @change="setPreset(preset)">
        <option value="recent">{{ t('Last 30 days') }}</option><option value="this-month">{{ t('This month') }}</option>
        <option value="last-month">{{ t('Last month') }}</option><option value="custom">{{ t('Custom period') }}</option>
      </select></label>
      <label>{{ t('From') }}<input v-model="draft.from" data-field="from" type="date" required @input="preset='custom'"></label>
      <label>{{ t('To') }}<input v-model="draft.to" data-field="to" type="date" :min="draft.from" required @input="preset='custom'"></label>
      <label>{{ t('As of date') }}<input v-model="draft.as_of" data-field="as_of" type="date" required></label>
      <button class="primary" :disabled="loading.summary">{{ t('Apply filters') }}</button>
    </form>

    <p v-if="errors.summary" class="alert" role="alert">{{ t(errors.summary) }} <button type="button" class="secondary" @click="load()">{{ t('Retry') }}</button></p>
    <p v-else-if="loading.summary && !summary" class="dashboard-status" role="status">{{ t('Loading dashboard') }}…</p>

    <p v-if="blocksError||errors.charts" class="alert">{{t(blocksError||errors.charts)}} <button @click="load()">{{t('Retry')}}</button></p>
    <div v-if="loading.summary && !summary" class="studio-skeleton" aria-hidden="true"></div>
    <div class="studio-grid"><article v-for="(w,index) in widgets" :key="w.id" :class="'studio-'+w.size" @dragover.prevent @drop.prevent="editing&&dragged&&move(dragged,w.id);dragged=''">
    <fieldset v-if="editing" :disabled="busy" class="studio-tools"><button draggable="true" @dragstart="dragged=w.id;$event.dataTransfer?.setData('text/plain',w.id)" @dragend="dragged=''" :aria-label="local('Drag widget','سحب العنصر')">⠿</button><button :disabled="!index" @click="move(w.id,widgets[index-1].id)" :aria-label="local('Move up','لأعلى')">↑</button><button :disabled="index===widgets.length-1" @click="move(w.id,widgets[index+1].id)" :aria-label="local('Move down','لأسفل')">↓</button><select :aria-label="local('Width','العرض')" :value="w.size" @change="editBoard(b=>b.widgets.find(x=>x.id===w.id)!.size=($event.target as HTMLSelectElement).value as Size)"><option value="compact">{{local('Compact','مدمج')}}</option><option value="medium">{{local('Medium','متوسط')}}</option><option value="full">{{local('Full row','صف كامل')}}</option></select><button @click="editBoard(b=>b.widgets=b.widgets.filter(x=>x.id!==w.id))" :aria-label="local('Remove widget','إزالة العنصر')">×</button></fieldset>
<RouterLink v-for="card in cards.filter(c=>c.id===w.id)" :key="card.id" :to="card.link" class="panel dashboard-kpi">
        <span>{{ t(labels[card.id]!) }}</span>
        <strong v-if="card.value !== undefined">{{ formatter.format(card.value) }}</strong>
        <strong v-for="(value,currency) in card.currencies" :key="currency"><bdi>{{ value }} <small>{{ currency }}</small></bdi></strong>
        <strong v-if="card.currencies && !Object.keys(card.currencies).length" class="muted">—</strong>
        <small class="muted">{{ card.filters ? caption(card.filters) : t('Current position') }}</small>
      </RouterLink>      <section v-if="w.id==='sales' && charts" class="panel dashboard-panel dashboard-wide">
        <header><h2>{{ t('Sales and collections') }}</h2><p>{{ caption(charts!.filters) }}</p></header>
        <p v-if="!Object.keys(sales).length" class="empty">{{ t('No data for this selection.') }}</p>
        <div v-for="(series,currency) in sales" :key="currency" class="dashboard-currency">
          <h3><bdi>{{ currency }}</bdi></h3>
          <DashboardChart :title="t('Sales and collections') + ' · ' + currency" kind="line"
            :series="series" :currency="currency" :theme="theme()" @open="openLink" />
        </div>
      </section>
      <template v-for="id in agingMetrics" :key="id">
        <section v-if="w.id===id && charts?.widgets[id]" class="panel dashboard-panel">
          <header>
            <h2><RouterLink :to="report(id,charts.widgets[id].filters)">{{ t(labels[id]!) }}</RouterLink></h2>
            <p>{{ caption(charts.widgets[id].filters) }}</p>
          </header>
          <p v-if="!charts.widgets[id].series.length" class="empty">{{ t('No open balances for this selection.') }}</p>
          <div v-for="series in charts.widgets[id].series" :key="series.currency" class="dashboard-currency">
            <RouterLink class="dashboard-balance" :to="report(id,{...charts.widgets[id].filters,currency:series.currency})"><bdi>{{ series.total }} {{ series.currency }}</bdi></RouterLink>
            <DashboardChart :title="t(labels[id]!) + ' · ' + series.currency" kind="bar"
              :series="agingSeries(id,series)" :currency="series.currency" :theme="theme()" @open="openLink" />
          </div>
        </section>
      </template>
      <section v-if="w.id==='pm-on-time' && maintenance" class="panel dashboard-panel">
        <header>
          <h2><RouterLink :to="report('pm-on-time',maintenance.filters)">{{ t(labels['pm-on-time']!) }}</RouterLink></h2>
          <p>{{ caption(maintenance.filters) }}</p>
        </header>
        <template v-if="maintenance.count">
          <DashboardChart :title="t(labels['pm-on-time']!)" kind="gauge" :series="gauge" :theme="theme()" @open="openLink" />
          <p class="dashboard-gauge-count">{{ formatter.format(maintenance.on_time) }} / {{ formatter.format(maintenance.count) }} · {{ t('Completed on time') }}</p>
        </template>
        <p v-else class="empty">{{ t('No preventive maintenance due in this period.') }}</p>
      </section>
      <section v-if="['activities','queue'].includes(w.id)" class="panel dashboard-panel">
        <header><h2>{{ title(w.id) }}</h2>
          <RouterLink v-if="visibleScreen(workspace,'inbox')" :to="{path:'/inbox',query:{site:applied.site,fromTask:'1'}}">{{ t('Open Inbox') }}</RouterLink>
        </header>
        <p v-if="loading.activities" class="dashboard-status" role="status">{{ t('Loading') }}…</p>
        <p v-for="failure in new Set([errors.tasks,errors.notices].filter(Boolean))" :key="failure" class="alert" role="alert">{{ t(failure) }}</p>
        <p v-if="!loading.activities && !tasks.length && !notices.length && !errors.tasks && !errors.notices" class="empty">{{ t('No recent activity.') }}</p>
        <ul v-if="tasks.length || notices.length" class="dashboard-activities">
          <li v-for="row in (w.id==='queue'?tasks:[...tasks,...notices])" :key="row.id ?? row.key">
            <RouterLink :to="activityLink(row)">
              <strong>{{ row.number }}</strong><span>{{ t(actionLabels[row.action ?? row.event ?? ''] ?? (row.action ?? row.event ?? '')) }}</span>
              <small v-if="row.title">{{ row.title }}</small>
            </RouterLink>
          </li>
        </ul>
      </section>

    <RouterLink v-if="shortcut(w.id)" class="panel dashboard-kpi" :to="shortcut(w.id)!.path">{{title(w.id)}} ↗</RouterLink>
    <section v-if="source(w.id)" class="panel dashboard-panel"><header><h2>{{title(w.id)}}</h2><small>{{source(w.id).filters?caption(source(w.id).filters!):w.id==='payments'||w.id==='receipts'||w.id==='customers-activity'?caption(dates):t('Current position')}}</small></header>
    <template v-if="tableIds.includes(w.id)"><p v-if="!source(w.id).items?.length">{{t('No data for this selection.')}}</p><div v-else class="table-scroll"><table class="studio-table"><thead><tr><th>{{w.id==='customers-activity'?t('Customer'):local('Document','المستند')}}</th><th v-if="w.id==='billing'||w.id==='late-list'">{{t('Status')}}</th><th>{{w.id==='late-list'?local('Due date','الاستحقاق'):t('Amount')}}</th></tr></thead><tbody><tr v-for="(row,n) in source(w.id).items" :key="(row.number||row.reference||row.party)+n"><td><RouterLink v-if="row.path" :to="row.path">{{row.reference}}</RouterLink><span v-else>{{row.number||row.party||row.reference}}</span></td><td v-if="w.id==='billing'||w.id==='late-list'"><span class="badge">{{t(row.status)}}</span></td><td><bdi>{{w.id==='late-list'?row.due_date:row.total||row.amount}} {{row.currency||row.invoice__currency}}</bdi></td></tr></tbody></table></div></template><strong v-else>{{['bank-variance','expense-due','subscription-overdue'].includes(w.id)?source(w.id).amount+' '+source(w.id).currency:w.id==='stock-value'?source(w.id).amount:source(w.id).count}}</strong>
    <RouterLink v-if="source(w.id).filters" :to="report(w.id==='late-list'?'overdue-purchases':w.id,source(w.id).filters!)">{{local('Open report','فتح التقرير')}}</RouterLink></section>
    </article></div>
    <section v-if="active==='general' && shortcuts.length" class="workspace-shortcuts">
      <h2>{{ t('Modules') }}</h2>
      <div class="actions">
        <RouterLink v-for="entry in shortcuts" :key="entry.id" class="button secondary" :to="entry.path">{{ t(entry.label) }}</RouterLink>
      </div>
    </section>
    <section v-else-if="active==='general' && !shortcuts.length" class="panel empty">
      <h2>{{ t('No modules enabled') }}</h2>
      <RouterLink v-if="workspace.admin" class="button primary" to="/modules">{{ t('Manage modules') }}</RouterLink>
    </section>
  </div>
</template>
