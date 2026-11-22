<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, type Workspace } from './api'
import { message, t } from './i18n'
import SavedViews from './SavedViews.vue'
import CsvExport from './CsvExport.vue'
import { bucketLabels } from './dashboard'

type Filters = Record<string, string>
type ReportRow = Record<string, string | number | null>
type Report = {metric: string; basis: string; filters: Filters; count: number; amount: string | null; currencies?: Record<string,string>; on_time?: number;
  issued?: string; returned?: string; groups: Record<string, {count: number; amount: string}>;
  items?: ReportRow[]; page?: number; page_size?: number}
const { workspace } = defineProps<{workspace: Workspace}>()
const route = useRoute()
const router = useRouter()
const labels: Record<string, string> = {
  finance: 'Finance and balances', stock: 'Purchasing and inventory', sales: 'Sales and collections', maintenance: 'Maintenance performance',
  'ap-aging': 'Posted AP aging', 'ar-aging': 'Posted AR aging', grni: 'Historical GRNI', 'pending-postings': 'Pending postings',
  'open-purchases': 'Open PO lines', 'overdue-purchases': 'Overdue PO lines', 'undated-purchases': 'PO lines without delivery date',
  'stock-value': 'Current inventory value', deliveries: 'Confirmed deliveries', 'sales-invoices': 'Sales invoices', 'net-posted-sales': 'Net posted sales', collections: 'Customer collections',
  'unbilled-sales': 'Unbilled deliveries', 'pm-due': 'Due preventive plans', 'maintenance-open': 'Open maintenance work',
  'pm-on-time': 'Preventive maintenance completed on time', 'maintenance-parts': 'Operational parts cost', 'maintenance-posted': 'Posted parts cost',
}
const statusWords: Record<string,string> = {in_progress:'In progress', completed:'Completed', closed:'Closed', cancelled:'Cancelled',
  waiting:'Awaiting execution', pending:'Pending', issued:'Issued', confirmed:'Confirmed', recorded:'Recorded', invoice:'Sales invoice', credit:'Credit note'}
const fields: Record<string, string> = {site: 'Site', as_of: 'As of date', from: 'From', to: 'To',
  supplier: 'Supplier', customer: 'Customer', account: 'Account', item: 'Item', asset: 'Asset', bucket: 'Aging bucket', currency:'Currency'}
const columns: Record<string, string> = {reference:'Record number', description:'Description', site_id:'Site',
  party:'Party', date:'Date', due_date:'Due date', status:'Status', quantity:'Quantity', amount:'Amount',
  currency:'Currency', locations:'Stock locations', account_id:'Account ID', issued:'Issued value', returned:'Returned value', on_time:'Completed on time', order:'Purchase order'}
const states: Record<string,string> = {credit:'Credit balance', current:'Not overdue', overdue:'Overdue', undated:'Without date',
  open:'Open', due:'Due', not_generated:'Not generated'}
const boardFields: Record<string, string[]> = {finance:['site','as_of','supplier','customer','account'],
  stock:['site','supplier','item'],sales:['site','from','to','customer'],maintenance:['site','from','to','asset']}
const metricBoard: Record<string, string> = Object.fromEntries(([
  ['finance',['ap-aging','ar-aging','grni','pending-postings']],
  ['stock',['open-purchases','overdue-purchases','undated-purchases','stock-value']],
  ['sales',['deliveries','sales-invoices','net-posted-sales','collections','unbilled-sales']],
  ['maintenance',['pm-due','maintenance-open','pm-on-time','maintenance-parts','maintenance-posted']],
] as [string, string[]][]).flatMap(([board, metrics]) => metrics.map(metric => [metric, board])))
const metric = computed(() => String(route.params.metric ?? ''))
const board = computed(() => String(route.params.board ?? metricBoard[metric.value] ?? 'finance'))
const available = (permission: string, module: string) => allows(workspace, permission)
  && workspace.modules.some(row => row.id === module && row.enabled)
const tabs = computed(() => [
  ['finance', available('ap.view','accounting') || available('accounting.view','accounting') ||
    (available('sales.view','sales') && workspace.modules.some(row => row.id === 'accounting' && row.enabled))],
  ['stock', available('purchasing.view','purchasing') || available('inventory.view','inventory')],
  ['sales', available('sales.view','sales')],
  ['maintenance', available('maintenance.view','workorders') && available('workorders.view','workorders')
    && workspace.modules.some(row => row.id === 'assets' && row.enabled)],
].filter(([, enabled]) => enabled).map(([key]) => String(key)))
const filters = ref<Filters>({})
const cards = ref<Report[]>([])
const report = ref<Report>()
const choices = ref<Record<string, {id:number;name:string}[]>>({})
const busy = ref(false)
const error = ref('')
const tableColumns = computed(() => Object.keys(columns).filter(key =>
  key === 'reference' || report.value?.items?.some(row => row[key] !== undefined && row[key] !== null)))
const boardPath = computed(() => '/reports/' + board.value)
const section = (key: string) => '/reports/' + key
const detailPath = (key: string) => '/reports/detail/' + key
const applied = ref<Filters>({})
const dashboardBack = computed(() => typeof route.query.dashboard === 'string'
  ? {path:'/',query:Object.fromEntries(new URLSearchParams(route.query.dashboard))}
  : undefined)
const backLink = computed(() => new URLSearchParams(applied.value).toString())
const backQuery = computed(() => {
  const raw = route.query.back
  if (typeof raw === 'string') {
    try { return Object.fromEntries(new URLSearchParams(raw)) } catch { return report.value?.filters ?? {} }
  }
  return Object.fromEntries(Object.entries(report.value?.filters ?? {}).filter(([key]) => boardFields[board.value]?.includes(key)))
})
let sequence = 0
async function load() {
  const token = ++sequence
  busy.value = true
  error.value = ''
  report.value = undefined
  cards.value = []
  const input = Object.fromEntries(Object.entries(route.query).filter(([key, value]) =>
    (boardFields[board.value]?.includes(key) || Boolean(metric.value && ['page','page_size','bucket','currency'].includes(key))) && typeof value === 'string')) as Filters
  if(metric.value)input.page_size ||= '25'
  filters.value = input
  try {
    if (!metric.value) {
      const data = await api<{filters: Filters; cards: Report[]; options: typeof choices.value}>('/dashboards/' + board.value + '?' + new URLSearchParams(input))
      if (token !== sequence) return
      filters.value = {...data.filters}
      applied.value = data.filters
      choices.value = data.options
      cards.value = data.cards
    } else {
      const data = await api<Report>('/reports/advanced/' + metric.value + '?' + new URLSearchParams(input))
      if (token !== sequence) return
      report.value = data
      filters.value = data.filters
    }
  } catch (failure) {
    if (token === sequence) error.value = message(failure)
  } finally {
    if (token === sequence) busy.value = false
  }
}
watch(() => route.fullPath, () => { void load() }, {immediate: true})
function apply(query: Filters) {
  const selected = Object.fromEntries(Object.entries(query).filter(([, value]) => value))
  void router.push({path: boardPath.value, query: selected}).then(failure => {
    if (failure) void load()
  })
}
const cell = (row: ReportRow, key: string) => {
  const value = row[key]
  if (value === null || value === undefined) return '—'
  if (key === 'site_id') return workspace.sites.find(s => s.id === Number(value))?.name ?? value
  if (key === 'status') return t(bucketLabels[String(value)] ?? states[String(value)] ?? statusWords[String(value)] ?? String(value))
  if (key === 'on_time') return t(Number(value) ? 'Yes' : 'No')
  return value
}
const basisText = (value: string) => t(value === 'as_of' ? 'As of date' : value === 'period' ? 'During period' : 'Current position')
const ratio = (row: Report) => row.on_time === undefined ? '' :
  row.on_time + ' / ' + row.count + (row.count ? ' (' + (100 * row.on_time / row.count).toFixed(1) + '%)' : '')
</script>

<template>
  <RouterLink v-if="metric" class="back-link" :to="dashboardBack ?? {path: boardPath, query: backQuery}">‹ {{ t('Back to dashboard') }}</RouterLink>
  <h1>{{ t(labels[metric || board] || 'Reports and dashboards') }}</h1>
  <nav v-if="!metric" class="section-tabs">
    <RouterLink v-for="key in tabs" :key="key" class="tab-link" :to="section(key)"
      :aria-current="key === board ? 'page' : undefined">{{ t(labels[key]!) }}</RouterLink>
  </nav>
  <p v-if="error" role="alert" class="alert">{{ t(error) }}</p>
  <p v-if="busy">{{ t('Loading') }}…</p>
  <template v-else-if="!metric && cards.length">
    <form v-validated-form class="actions" @submit.prevent="apply(filters)">
      <div class="field-wrap" v-for="field in boardFields[board]" :key="field"><label>{{ t(fields[field]!) }}
        <select v-if="field === 'site'" v-model="filters[field]"><option :value="undefined">{{ t('All sites') }}</option>
          <option v-for="site in workspace.sites" :key="site.id" :value="String(site.id)">{{ site.name }}</option></select>
        <select v-else-if="choices[field]" v-model="filters[field]"><option :value="undefined">{{ t('all') }}</option>
          <option v-if="filters[field] && !choices[field]!.some(option => String(option.id) === filters[field])" :value="filters[field]">#{{ filters[field] }}</option>
          <option v-for="option in choices[field]" :key="option.id" :value="String(option.id)">{{ option.name }}</option>
        </select>
        <input :placeholder="t('Enter a value')" v-else v-model="filters[field]" :type="['from','to','as_of'].includes(field) ? 'date' : 'text'"
          :inputmode="['from','to','as_of'].includes(field) ? undefined : 'numeric'">
      </label><FieldHint :label="t(fields[field]!)" /></div>
      <button class="primary">{{ t('Run report') }}</button>
    </form>
    <SavedViews :key="board" :workspace="workspace" :screen="'reports-' + board" :filters="filters"
      :skip-default="Object.keys(route.query).length !== 0" :hide-export="true" @apply="apply" />
    <div class="card-grid">
      <section v-for="card in cards" :key="card.metric" class="panel record-form">
        <h2><RouterLink :to="{path: detailPath(card.metric), query: {...card.filters, back: backLink}}">{{ t(labels[card.metric]!) }}</RouterLink></h2>
        <p class="muted">{{ basisText(card.basis) }}</p>
        <p v-if="card.currencies && Object.keys(card.currencies).length"><strong v-for="(amount, currency) in card.currencies" :key="currency" class="report-currency"><bdi>{{ amount }} {{ currency }}</bdi></strong></p>
        <p v-else><strong>{{ card.on_time !== undefined ? ratio(card) : card.amount !== null ? card.amount + ' AED' : card.count }}</strong></p>
        <ul v-if="Object.keys(card.groups).length"><li v-for="(group, name) in card.groups" :key="name">
          {{ t(states[String(name)] ?? statusWords[String(name)] ?? String(name)) }} · {{ group.count }}<span v-if="card.amount !== null && Object.keys(card.currencies ?? {}).length <= 1"> · {{ group.amount }} {{ Object.keys(card.currencies ?? {})[0] ?? 'AED' }}</span>
        </li></ul>
      </section>
    </div>
    <RouterLink v-if="board === 'finance' && available('sales.view','sales')" to="/sales/aging">{{ t('Operational AR aging') }}</RouterLink>
  </template>
  <template v-else-if="report">
    <p>{{ basisText(report.basis) }}</p>
    <dl v-if="Object.keys(report.filters).length">
      <template v-for="(value, key) in report.filters" :key="key">
        <dt>{{ t(fields[String(key)] ?? String(key)) }}</dt><dd>{{ key === 'site' ? workspace.sites.find(s => s.id === Number(value))?.name ?? value : key === 'bucket' ? t(bucketLabels[value] ?? value) : value }}</dd>
      </template>
    </dl>
    <p>{{ t('Rows') }} · {{ report.count }}
      <span v-if="report.amount !== null && !Object.keys(report.currencies ?? {}).length"> · {{ t('Total') }} {{ report.amount }} {{ report.filters.currency ?? 'AED' }}</span>
      <span v-if="report.on_time !== undefined"> · {{ ratio(report) }}</span></p>
    <p v-if="report.currencies && Object.keys(report.currencies).length">
      {{ t('Total') }} <strong v-for="(amount,currency) in report.currencies" :key="currency" class="report-currency"><bdi>{{ amount }} {{ currency }}</bdi></strong>
    </p>
    <p v-if="report.issued !== undefined"><bdi>{{ report.issued }} − {{ report.returned }} = {{ report.amount }} AED</bdi></p>
    <CsvExport :workspace="workspace" :screen="metric" :filters="report.filters" :report="true" />
    <section v-if="report.items?.length" class="panel table-panel approval-section">
      <table><thead><tr><th v-for="key in tableColumns" :key="key">{{ t(columns[key]!) }}</th></tr></thead>
        <tbody><tr v-for="(row, index) in report.items" :key="index">
          <td v-for="key in tableColumns" :key="key">
            <RouterLink v-if="key === 'reference' && row.path" :to="String(row.path)">{{ row.reference }}</RouterLink>
            <span v-else>{{ cell(row,key) }}</span>
          </td>
        </tr></tbody></table>
    </section>
    <p v-else>{{ t('No report rows.') }}</p>
    <TableToolbar v-if="report.page && report.page_size" :page="report.page" :count="report.count" :size="report.page_size" @page="router.replace({query:{...route.query,page:$event}})" @size="router.replace({query:{...route.query,page_size:$event,page:1}})"/>
  </template>
</template>
