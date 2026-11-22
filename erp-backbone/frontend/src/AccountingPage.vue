<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPaging,ref as pagingRef} from 'vue'
const pg=pagingRef(1),ps=pagingRef(25),term=pagingRef(''),total=pagingRef(0)
function queryPage(){return new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}).toString()}

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, createRequest, type Workspace } from './api'
import { locale, message, t } from './i18n'

type Account = {id: number; code: string; name: string; kind: string; parent_id: number | null; detail: boolean; control: boolean; active: boolean}
type Period = {id: number; start: string; end: string; closed_at: string | null}
type Rule = {id: number; kind: string; starts: string; inventory_id: number; offset_id: number; variance_id?: number}
type Reference = {center_active?:boolean|null;center_site_id?:number|null;id: number; code: string; name: string; kind: string; rate: string | null; starts: string | null}
type Setup = {cutoff: string | null; activated_at: string | null; accounts: Account[]; periods: Period[]; rules: Rule[]; references: Reference[]}
type Line = {account_id?: number; site_id?: number | null; cost_center_id?: number | null; debit: string; credit: string}
type Entry = {id: number; number: string; date: string; source_date: string | null; kind: string; description: string;
  posted_at: string | null; posted_by: string; posting_note: string; revision: number; reversal_of_id: number | null;
  original_entry_id: number | null; rule_id: number | null; lines: Line[];
  source?: {ap_invoice_ids?: number[]; work_order_id?: number; sale__document_id: number | null; movement_id: number | null; movement__receipt_id: number | null; movement__receipt__order_id: number | null}}
type Preview = {token: string; cutoff: string; movement_count: number; inventory: {site_id: number; value: string}[];
  in_transit?: {movement_id:number;site_id:number;value:string}[];
  grni: {movement_id: number; site_id: number; value: string; receipt: string | null; supplier: string | null}[]}
type Posting = {id: number; movement_id: number | null; sale_number?: string; entry_id: number | null; kind: string; state: string; error: string; source_date: string}
type Report = {opening?: string; items: Record<string, string | number | null>[]}
const route = useRoute()
const router = useRouter()
const {workspace} = defineProps<{workspace: Workspace}>()
const permissions = (key: string) => allows(workspace, 'accounting.' + key, null)
const sections: Record<string, string> = {
  journals: 'Journal entries', postings: 'Posting queue', reports: 'Reports', setup: 'Accounting setup',
}
const tabs = Object.entries(sections)
const tab = computed(() => route.params.section === 'periods' ? sections.setup : sections[String(route.params.section)] ?? sections.journals)
const config = ref<Setup>()
const rows = ref<Entry[]>([])
const nextBefore = ref<number | null>(null)
const entry = ref<Entry>()
const queue = ref<Posting[]>([])
const queueCount = ref(0)
const queueBefore = ref<number | null>(null)
const filter = ref('pending')
const retryRow = ref<Posting>()
const retryDate = ref('')
const retryReason = ref('')
const statement = ref('trial')
const report = ref<Report>()
const dateFrom = ref('')
const dateTo = ref('')
const accountId = ref<number>()
const opening = ref<Preview>()
const cutoff = ref('')
const accept = ref(false)
const periodMonth = ref('')
const periodYear = ref<number | ''>('')
const monthChoices = computed(() => {
  const format = new Intl.DateTimeFormat(locale.value, {month: 'long', timeZone: 'UTC'})
  return Array.from({length: 12}, (_, index) => ({
    value: String(index + 1).padStart(2, '0'),
    label: format.format(new Date(Date.UTC(2000, index, 1))),
  }))
})
const periodStart = computed(() => {
  const year = periodYear.value
  if (!periodMonth.value || typeof year !== 'number' || !Number.isInteger(year) || year < 1 || year > 9999) return ''
  return String(year).padStart(4, '0') + '-' + periodMonth.value + '-01'
})
const setupForm = ref('accounts')
const form = computed(() => route.params.section === 'periods' ? 'periods' : setupForm.value)
const kinds = ['asset', 'liability', 'equity', 'revenue', 'expense']
const acct = ref<Partial<Account>>({kind: 'asset', detail: true, active: true})
const rule = ref<Partial<Rule>>({kind: 'receipt'})
const refForm = ref<Partial<Reference>>({kind: 'tax'})
const draft = ref<{id?: number; revision?: number; date: string; description: string; lines: Line[]; reverse?: number}>()
const blankLine = (): Line => ({debit: '0.00', credit: '0.00'})
const error = ref('')
const notice = ref('')
const busy = ref(false)
let saveRequest = createRequest()
const code = (id?: number | null) => config.value?.accounts.find(a => a.id === id)?.code ?? '—'
const siteName = (id: number) => workspace.sites.find(s => s.id === id)?.name ?? '#' + id
const explain = (value: string) => t(value ? value : 'Waiting to post')
const normalAccounts = computed(() => config.value?.accounts.filter(a => a.active && a.detail && !a.control) ?? [])

async function run(task: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try { await task() } catch (failure) { error.value = message(failure) } finally { busy.value = false }
}
async function getSetup() { config.value = await api<Setup>('/accounting/setup') }
async function journals(before?: number) {
  const result = await api<{items: Entry[];count:number; next_before: number | null}>('/accounting/journals?' +queryPage()+ (before ? '&before=' + before : ''))
  rows.value = before ? [...rows.value, ...result.items] : result.items
  nextBefore.value = result.next_before;total.value=result.count
}
watchPaging([pg,ps,term],()=> (route.params.section==='postings'?postings():journals()).catch(e=>error.value=message(e)))
async function postings(before?: number) {
  const result = await api<{items: Posting[]; count: number; next_before: number | null}>('/accounting/postings?'+queryPage()+'&state=' + filter.value + (before ? '&before=' + before : ''))
  queue.value = before ? [...queue.value, ...result.items] : result.items
  queueBefore.value = result.next_before;total.value=result.count
  queueCount.value = result.count
}
function navigate(section: string) {
  void router.push('/accounting/' + section)
}
function showSetup(section: string) {
  if (section === 'periods') navigate('periods')
  else {
    setupForm.value = section
    if (route.params.section === 'periods') navigate('setup')
  }
}
function openEntry(id: number) {
  void router.push({path: '/accounting/journals', query: {entry: id}})
}
function newDraft(row?: Entry, reverse = false) {
  saveRequest = createRequest()
  draft.value = row ? {date: row.date, description: reverse ? '' : row.description,
    lines: row.lines.map(line => ({...line})), ...(reverse ? {reverse: row.id} : {id: row.id, revision: row.revision})}
    : {date: '', description: '', lines: [blankLine(), blankLine()]}
}
function saveDraft() {
  void run(async () => {
    const data = draft.value!
    if (data.reverse) {
      entry.value = await saveRequest<Entry>('/accounting/journals/' + data.reverse + '/reverse',
        {date: data.date, description: data.description})
    } else if (data.id) {
      entry.value = await api<Entry>('/accounting/journals/' + data.id, 'PATCH', data)
    } else {
      entry.value = await saveRequest<Entry>('/accounting/journals', data)
    }
    draft.value = undefined
    await journals()
    notice.value = 'Journal draft saved.'
  })
}
function post() {
  void run(async () => {
    if (!entry.value) return
    entry.value = await api<Entry>('/accounting/journals/' + entry.value.id + '/post', 'POST', {revision: entry.value.revision})
    await journals()
    notice.value = 'Journal posted.'
  })
}
function saveConfig(path: string, payload: object) {
  void run(async () => {
    await api('/accounting/' + path, 'POST', payload)
    await getSetup()
    opening.value = undefined
    notice.value = 'Changes saved.'
  })
}
function openPeriod() {
  if (periodStart.value) saveConfig('periods', {start: periodStart.value})
}
function cutover(activate = false) {
  void run(async () => {
    if (!activate) {
      opening.value = await api<Preview>('/accounting/cutover', 'POST', {cutoff: cutoff.value})
      accept.value = false
    } else {
      await api('/accounting/cutover', 'POST', {cutoff: opening.value!.cutoff, token: opening.value!.token})
      await getSetup()
      opening.value = undefined
      notice.value = 'Accounting activated.'
    }
  })
}
function retry() {
  void run(async () => {
    if (!retryRow.value) return
    const result = await api<Posting>('/accounting/postings/' + retryRow.value.id + '/retry', 'POST', {
      date: retryDate.value || undefined, reason: retryReason.value,
    })
    if (result.state !== 'pending') { retryRow.value = undefined; notice.value = 'Posting processed.' }
    else error.value = explain(result.error)
    await postings()
  })
}
function loadReport() {
  void run(async () => {
    const query = new URLSearchParams({start: dateFrom.value, end: dateTo.value})
    if (accountId.value) query.set('account_id', String(accountId.value))
    report.value = await api<Report>('/accounting/reports/' + statement.value + '?' + query)
  })
}
watch(() => route.fullPath, () => {
  void run(async () => {
    if (!config.value) await getSetup()
    entry.value = undefined
    draft.value = undefined
    if (tab.value === 'Journal entries') {
      await journals()
      const selected = Number(route.query.entry)
      if (Number.isSafeInteger(selected) && selected > 0) {
        entry.value = await api<Entry>('/accounting/journals/' + selected)
      }
    }
    if (tab.value === 'Posting queue') await postings()
  })
}, {immediate: true})
import DateInput from './DateInput.vue'
</script>

<template>
  <h1>{{ t('Accounting') }}</h1>
  <nav class="section-tabs" :aria-label="t('Accounting')">
    <button v-for="[section, page] in tabs" :key="section" class="tab-link" :aria-current="tab === page ? 'page' : undefined" :disabled="busy" @click="navigate(section)">{{ t(page) }}</button>
  </nav>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <p v-if="config">{{ t('Ledger currency') }} · AED <span v-if="config.cutoff"> · {{ t('Cutover date') }} · {{ config.cutoff }}</span></p>

  <template v-if="tab === 'Journal entries'">
    <button v-if="permissions('manual') && !draft" class="primary" :disabled="busy" @click="newDraft()">{{ t('New journal entry') }}</button>
    <form v-validated-form v-if="draft" class="panel record-form purchase-form" @submit.prevent="saveDraft">
      <h2>{{ t(draft.reverse ? 'Reverse journal' : 'Manual journal') }}</h2>
      <DateInput data-field="date" :label="t('Posting date')" v-model="draft.date" type="date" required />
      <div class="field-wrap"><label>{{ t('Description / Reason') }}<textarea data-field="description" :placeholder="t('Describe the item or activity')" v-model="draft.description" required maxlength="1000"></textarea></label><FieldHint :label="t('Description / Reason')" required /></div>
      <fieldset v-for="(line, index) in draft.reverse ? [] : draft.lines" :key="index" class="purchase-line">
        <legend>{{ t('Line item') }} {{ index + 1 }}</legend>
        <EntityAutocomplete :data-field="'lines.' + index + '.account_id'" :label="t('Account')" lookup="accounts" :params="{ detail_only: 1 }" :choices="normalAccounts" :placeholder="t('Select')" :empty-value="undefined" required v-model="line.account_id" />
        <div class="field-wrap"><label>{{ t('Debit') }}<input :data-field="'lines.' + index + '.debit'" :placeholder="t('e.g., 125.50')" v-model="line.debit" required inputmode="decimal"></label><FieldHint :label="t('Debit')" required /></div>
        <div class="field-wrap"><label>{{ t('Credit') }}<input :data-field="'lines.' + index + '.credit'" :placeholder="t('e.g., 125.50')" v-model="line.credit" required inputmode="decimal"></label><FieldHint :label="t('Credit')" required /></div>
        <div class="field-wrap"><label>{{ t('Site') }}<select :data-field="'lines.' + index + '.site_id'" v-model="line.site_id" @change="line.cost_center_id=null"><option :value="null">{{ t('None') }}</option><option v-for="s in workspace.sites.filter(site=>site.active || site.id===line.site_id)" :key="s.id" :value="s.id">{{ s.name }}</option></select></label><FieldHint :label="t('Site')" /></div>
        <EntityAutocomplete :data-field="'lines.' + index + '.cost_center_id'" :label="t('Cost center')" lookup="cost_centers" :params="{ site_id: line.site_id }" :choices="(config?.references ?? []).filter(r => r.kind === 'cost_center' && (r.center_active && r.center_site_id === line.site_id))" :placeholder="t('None')" :empty-value="null" v-model="line.cost_center_id" />
        <button v-if="draft.lines.length > 2" type="button" class="secondary" @click="draft.lines.splice(index, 1)">{{ t('Remove item') }}</button>
      </fieldset>
      <div class="actions">
        <button v-if="!draft.reverse" type="button" class="secondary" :disabled="draft.lines.length >= 100" @click="draft.lines.push(blankLine())">{{ t('Add item') }}</button>
        <button class="primary" :disabled="busy">{{ t('Save draft') }}</button>
        <button type="button" class="secondary" @click="draft = undefined">{{ t('Cancel') }}</button>
      </div>
    </form>
    <section v-else-if="entry" class="panel record-form purchase-form">
      <h2>{{ entry.number }} · {{ t(entry.posted_at ? 'Posted' : 'Draft') }}</h2>
      <p>{{ entry.date }} · {{ entry.description }}</p>
      <p v-if="entry.source_date">{{ t('Source date') }} · {{ entry.source_date }}</p>
      <p v-if="entry.posting_note">{{ t('Reason') }} · {{ entry.posting_note }}</p>
      <p v-if="entry.posted_by">{{ t('Posted by') }} · {{ entry.posted_by }} · {{ new Date(entry.posted_at!).toLocaleString() }}</p>
      <p v-if="entry.rule_id">{{ t('Posting rule version') }} #{{ entry.rule_id }}</p>
      <div v-if="entry.source" class="actions">
        <RouterLink v-if="entry.source.work_order_id" :to="'/work-orders/' + entry.source.work_order_id">WO-{{ entry.source.work_order_id }}</RouterLink>
        <RouterLink v-for="id in entry.source.ap_invoice_ids ?? []" :key="id" :to="'/payables/invoices/' + id">{{ t('Supplier invoice') }} #{{ id }}</RouterLink>
        <span><template v-if="entry.source.movement_id">{{ t('Movement') }} #{{ entry.source.movement_id }}</template></span>
        <RouterLink v-if="entry.source.sale__document_id" :to="'/sales/' + entry.source.sale__document_id">{{ t('Sales document') }}</RouterLink>
        <RouterLink v-if="entry.source.movement__receipt_id" :to="'/inventory/receipts/' + entry.source.movement__receipt_id">GRN</RouterLink>
        <RouterLink v-if="entry.source.movement__receipt__order_id" :to="'/purchase-orders/' + entry.source.movement__receipt__order_id">PO</RouterLink>
      </div>
      <button v-if="entry.original_entry_id || entry.reversal_of_id" class="secondary" @click="openEntry((entry.original_entry_id || entry.reversal_of_id)!)">{{ t('Original journal') }}</button>
      <div class="table-panel"><table><thead><tr><th>{{ t('Account') }}</th><th>{{ t('Site') }}</th><th>{{ t('Cost center') }}</th><th>{{ t('Debit') }}</th><th>{{ t('Credit') }}</th></tr></thead>
        <tbody><tr v-for="(line, index) in entry.lines" :key="index"><td>{{ code(line.account_id) }}</td><td>{{ line.site_id ? siteName(line.site_id) : '—' }}</td>
          <td>{{ config?.references.find(r => r.id === line.cost_center_id)?.name ?? '—' }}</td><td>{{ line.debit }}</td><td>{{ line.credit }}</td></tr></tbody></table></div>
      <div v-if="entry.kind === 'manual'" class="actions">
        <button v-if="!entry.posted_at && !entry.reversal_of_id && permissions('manual')" class="secondary" :disabled="busy" @click="newDraft(entry)">{{ t('Edit') }}</button>
        <button v-if="!entry.posted_at && permissions('manual.post')" class="primary" :disabled="busy" @click="post">{{ t('Post journal') }}</button>
        <button v-if="entry.posted_at && permissions('manual')" class="secondary" :disabled="busy" @click="newDraft(entry, true)">{{ t('Reverse journal') }}</button>
      </div>
    </section>
    <section class="panel table-panel approval-section">
      <TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table v-if="rows.length"><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Posting date') }}</th><th>{{ t('Description') }}</th><th>{{ t('Status') }}</th></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.id"><td><button class="secondary" @click="openEntry(row.id)">{{ row.number }}</button></td>
          <td>{{ row.date }}</td><td>{{ row.description }}</td><td>{{ t(row.posted_at ? 'Posted' : 'Draft') }}</td></tr></tbody></table>
      <p v-else class="empty">{{ t('No journal entries yet.') }}</p>
    </section>
    <button v-if="nextBefore" class="secondary" :disabled="busy" @click="run(() => journals(nextBefore!))">{{ t('Load more') }}</button>
  </template>

  <template v-if="tab === 'Posting queue'">
    <div class="field-wrap"><label>{{ t('Status') }}<select data-field="filter" v-model="filter" :disabled="busy" @change="run(() => postings())">
      <option v-for="state in ['pending', 'posted', 'opening', 'zero', 'all']" :key="state" :value="state">{{ t(state) }}</option>
    </select></label><FieldHint :label="t('Status')" /></div>
    <form v-validated-form v-if="retryRow" class="panel record-form" @submit.prevent="retry">
      <h2>{{ t('Retry posting') }} · {{ retryRow.sale_number ?? ('#' + retryRow.movement_id) }}</h2>
      <DateInput data-field="retryDate" :label="t('Posting date')" v-model="retryDate" type="date" :min="retryRow.source_date" />
      <div class="field-wrap"><label>{{ t('Description / Reason') }}<textarea data-field="retryReason" :placeholder="t('Enter details')" v-model="retryReason" :required="!!retryDate" maxlength="1000"></textarea></label><FieldHint :label="t('Description / Reason')" :required="!!retryDate" /></div>
      <button class="primary" :disabled="busy">{{ t('Retry posting') }}</button>
    </form>
    <section class="panel table-panel">
      <TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table v-if="queue.length"><thead><tr><th>{{ t('Movement') }}</th><th>{{ t('Source date') }}</th><th>{{ t('Status') }}</th><th>{{ t('Reason') }}</th><th>{{ t('Actions') }}</th></tr></thead>
        <tbody><tr v-for="row in queue" :key="row.id"><td>{{ row.sale_number ?? ('#' + row.movement_id) }} · {{ t(row.kind) }}</td><td>{{ row.source_date }}</td><td>{{ t(row.state) }}</td>
          <td>{{ row.state === 'pending' ? explain(row.error) : '—' }}</td><td>
            <button v-if="row.entry_id" class="secondary" @click="openEntry(row.entry_id)">{{ t('Journal entry') }}</button>
            <button v-else-if="row.state === 'pending' && permissions('post')" class="secondary" @click="retryRow = row; retryDate = ''; retryReason = ''">{{ t('Retry posting') }}</button>
          </td></tr></tbody></table>
      <p v-else class="empty">{{ t('No postings in this view.') }}</p>
    </section>
    <button v-if="queueBefore" class="secondary" :disabled="busy" @click="run(() => postings(queueBefore!))">{{ t('Load more') }}</button>
    <p v-if="queueCount > queue.length">{{ queue.length }} / {{ queueCount }}</p>
  </template>

  <template v-if="tab === 'Reports'">
    <form v-validated-form class="panel record-form" @submit.prevent="loadReport">
      <div class="field-wrap"><label>{{ t('Report') }}<select data-field="statement" v-model="statement" @change="report = undefined">
        <option value="trial">{{ t('Trial Balance') }}</option><option value="ledger">{{ t('General Ledger') }}</option><option value="grni">{{ t('GRNI reconciliation') }}</option>
      </select></label><FieldHint :label="t('Report')" /></div>
      <template v-if="statement !== 'grni'">
        <DateInput data-field="dateFrom" :label="t('From date')" v-model="dateFrom" type="date" required />
        <DateInput data-field="dateTo" :label="t('To date')" v-model="dateTo" type="date" required />
      </template>
      <div class="field-wrap" v-if="statement === 'ledger'"><label>{{ t('Account') }}<EntityAutocomplete v-model="accountId" lookup="accounts" :local-items="(config?.accounts ?? []).filter(a => a.detail).map(a => ({ id: a.id, label: a.code + ' · ' + a.name }))" data-field="accountId" required /></label><FieldHint :label="t('Account')" required /></div>
      <button class="primary" :disabled="busy">{{ t('Run report') }}</button>
    </form>
    <section v-if="report" class="panel table-panel approval-section">
      <p v-if="report.opening" class="empty">{{ t('Opening balance') }} · {{ report.opening }} AED</p>
      <table v-if="statement === 'trial'"><thead><tr><th>{{ t('Account') }}</th><th>{{ t('Opening balance') }}</th><th>{{ t('Debit') }}</th><th>{{ t('Credit') }}</th><th>{{ t('Closing balance') }}</th></tr></thead>
        <tbody><tr v-for="row in report.items" :key="Number(row.id)"><td>{{ row.code }} · {{ row.name }}</td><td>{{ row.opening }}</td><td>{{ row.debit }}</td><td>{{ row.credit }}</td><td>{{ row.closing }}</td></tr></tbody></table>
      <table v-else-if="statement === 'ledger'"><thead><tr><th>{{ t('Journal entry') }}</th><th>{{ t('Posting date') }}</th><th>{{ t('Description') }}</th><th>{{ t('Amount') }} AED</th></tr></thead>
        <tbody><tr v-for="(row, index) in report.items" :key="index"><td><button class="secondary" @click="openEntry(Number(row.entry_id))">{{ row.number }}</button></td><td>{{ row.date }}</td><td>{{ row.description }}</td><td>{{ row.amount }}</td></tr></tbody></table>
      <table v-else><thead><tr><th>{{ t('Supplier') }}</th><th>GRN</th><th>PO</th><th>{{ t('Site') }}</th><th>{{ t('Movement') }}</th><th>{{ t('Original amount') }}</th><th>{{ t('Returned amount') }}</th><th>{{ t('Invoiced amount') }}</th><th>{{ t('Open balance') }}</th></tr></thead>
        <tbody><tr v-for="row in report.items" :key="Number(row.movement_id)"><td>{{ row.supplier }}</td><td>{{ row.receipt }}</td><td>{{ row.order }}</td><td>{{ siteName(Number(row.site_id)) }}</td><td>#{{ row.movement_id }}</td><td>{{ row.original_value }}</td><td>{{ row.returned_value }}</td><td>{{ row.invoiced_value }}</td><td>{{ row.value }}</td></tr></tbody></table>
      <p v-if="!report.items.length" class="empty">{{ t('No report rows.') }}</p>
    </section>
  </template>
  <template v-if="tab === 'Accounting setup' && config">
    <nav class="section-tabs">
      <button v-for="item in [{id:'accounts',label:'Chart of accounts'},{id:'periods',label:'Financial periods'},{id:'rules',label:'Posting rules'},{id:'cutover',label:'Cutover'},{id:'references',label:'Reference data'}]" :key="item.id" class="tab-link"
        :aria-current="form === item.id ? 'page' : undefined" @click="showSetup(item.id)">{{ t(item.label) }}</button>
    </nav>
    <template v-if="form === 'accounts'">
      <form v-validated-form v-if="permissions('setup')" class="panel record-form" @submit.prevent="saveConfig('accounts', acct)">
        <h2>{{ t(acct.id ? 'Edit account' : 'New account') }}</h2>
        <div class="field-wrap"><label>{{ t('Code') }}<input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="acct.code" required maxlength="24"></label><FieldHint :label="t('Code')" required /></div>
        <div class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="acct.name" required maxlength="120"></label><FieldHint :label="t('Name')" required /></div>
        <div class="field-wrap"><label>{{ t('Account type') }}<select data-field="kind" v-model="acct.kind"><option v-for="kind in kinds" :key="kind" :value="kind">{{ t(kind) }}</option></select></label><FieldHint :label="t('Account type')" /></div>
        <div class="field-wrap"><label>{{ t('Parent account') }}<EntityAutocomplete v-model="acct.parent_id" lookup="accounts" :local-items="config.accounts.filter(a => !a.detail && a.kind === acct.kind && a.id !== acct.id).map(a => ({ id: a.id, label: a.code + ' · ' + a.name }))" :empty-label="t('None')" data-field="parent_id" /></label><FieldHint :label="t('Parent account')" /></div>
        <div class="field-wrap"><label class="delegation-check"><input data-field="detail" v-model="acct.detail" type="checkbox">{{ t('Posting account') }}</label><FieldHint :label="t('Posting account')" /></div>
        <div class="field-wrap"><label class="delegation-check"><input data-field="active" v-model="acct.active" type="checkbox">{{ t('Active') }}</label><FieldHint :label="t('Active')" /></div>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Save') }}</button>
          <button type="button" class="secondary" @click="acct = {kind: 'asset', detail: true, active: true}">{{ t('New account') }}</button></div>
      </form>
      <section class="panel table-panel approval-section"><table><thead><tr><th>{{ t('Account') }}</th><th>{{ t('Parent account') }}</th><th>{{ t('Account type') }}</th><th>{{ t('Status') }}</th></tr></thead>
        <tbody><tr v-for="a in config.accounts" :key="a.id"><td>{{ a.code }} · {{ a.name }}</td><td>{{ code(a.parent_id) }}</td><td>{{ t(a.kind) }} · {{ t(a.control ? 'Control account' : a.detail ? 'Posting account' : 'Group') }}</td>
          <td>{{ t(a.active ? 'Active' : 'Inactive') }} <button v-if="permissions('setup')" class="secondary" @click="acct = {...a}">{{ t('Edit') }}</button></td></tr></tbody></table></section>
    </template>
    <template v-if="form === 'periods'">
      <form v-validated-form v-if="permissions('setup')" class="panel record-form" @submit.prevent="openPeriod">
        <div class="field-wrap"><label>{{ t('Financial month') }}
          <select data-field="periodMonth" v-model="periodMonth" required>
            <option disabled value="">{{ t('Choose a month') }}</option>
            <option v-for="month in monthChoices" :key="month.value" :value="month.value">{{ month.label }}</option>
          </select>
        </label><FieldHint :label="t('Financial month')" required /></div>
        <div class="field-wrap"><label>{{ t('Financial year') }}
          <input data-field="periodYear" v-model.number="periodYear" type="number" inputmode="numeric" min="1" max="9999" step="1" placeholder="2026" required>
        </label><FieldHint :label="t('Financial year')" required /></div>
        <button class="primary" :disabled="busy">{{ t('Open period') }}</button>
      </form>
      <section class="panel table-panel"><table><thead><tr><th>{{ t('From date') }}</th><th>{{ t('To date') }}</th><th>{{ t('Status') }}</th><th>{{ t('Actions') }}</th></tr></thead>
        <tbody><tr v-for="p in config.periods" :key="p.id"><td>{{ p.start }}</td><td>{{ p.end }}</td><td>{{ t(p.closed_at ? 'Closed' : 'Open') }}</td>
          <td><button v-if="!p.closed_at && permissions('close')" class="secondary" :disabled="busy" @click="saveConfig('periods/' + p.id + '/close', {})">{{ t('Close period') }}</button></td></tr></tbody></table></section>
    </template>
    <template v-if="form === 'rules'">
      <form v-validated-form v-if="permissions('setup')" class="panel record-form" @submit.prevent="saveConfig('rules', rule)">
        <div class="field-wrap"><label>{{ t('Movement') }}<select data-field="kind" v-model="rule.kind" @change="rule = {kind: rule.kind}"><option v-for="kind in ['receipt', 'return', 'correction', 'opening', 'maint_issue', 'transfer_out', 'mfg_issue']" :key="kind" :value="kind">{{ t(kind) }}</option></select></label><FieldHint :label="t('Movement')" /></div>
        <DateInput data-field="starts" :label="t('Effective date')" v-model="rule.starts" type="date" required />
        <template v-if="['receipt','opening','maint_issue','transfer_out','mfg_issue'].includes(rule.kind ?? '')">
          <div class="field-wrap"><label>{{ t('Inventory account') }}<EntityAutocomplete v-model="rule.inventory_id" lookup="accounts" :local-items="config.accounts.filter(a => a.active && a.detail && a.kind === 'asset').map(a => ({ id: a.id, label: a.code + ' · ' + a.name }))" data-field="inventory_id" required /></label><FieldHint :label="t('Inventory account')" required /></div>
          <div class="field-wrap"><label>{{ t('Offset account') }}<EntityAutocomplete v-model="rule.offset_id" lookup="accounts" :local-items="config.accounts.filter(a => a.active && a.detail && a.kind === (rule.kind === 'opening' ? 'equity' : rule.kind === 'maint_issue' ? 'expense' : ['transfer_out','mfg_issue'].includes(rule.kind ?? '') ? 'asset' : 'liability')).map(a => ({ id: a.id, label: a.code + ' · ' + a.name }))" data-field="offset_id" required /></label><FieldHint :label="t('Offset account')" required /></div>
        </template>
        <div class="field-wrap" v-if="rule.kind === 'return' || rule.kind === 'transfer_out' || rule.kind === 'mfg_issue'"><label>{{ t('Variance account') }}<EntityAutocomplete v-model="rule.variance_id" lookup="accounts" :local-items="config.accounts.filter(a => a.active && a.detail && a.kind === 'expense').map(a => ({ id: a.id, label: a.code + ' · ' + a.name }))" data-field="variance_id" required /></label><FieldHint :label="t('Variance account')" required /></div>
        <button class="primary" :disabled="busy">{{ t('Publish rule') }}</button>
      </form>
      <section class="panel table-panel"><table><thead><tr><th>{{ t('Movement') }}</th><th>{{ t('Effective date') }}</th><th>{{ t('Inventory account') }}</th><th>{{ t('Offset account') }}</th><th>{{ t('Variance account') }}</th></tr></thead>
        <tbody><tr v-for="r in config.rules" :key="r.id"><td>{{ t(r.kind) }} · #{{ r.id }}</td><td>{{ r.starts }}</td>
          <td>{{ ['return','correction'].includes(r.kind) ? t('Original receipt') : code(r.inventory_id) }}</td>
          <td>{{ ['return','correction'].includes(r.kind) ? t('Original receipt') : code(r.offset_id) }}</td><td>{{ code(r.variance_id) }}</td></tr></tbody></table></section>
    </template>
    <template v-if="form === 'cutover'">
      <form v-validated-form v-if="!config.activated_at && permissions('setup')" class="panel record-form" @submit.prevent="cutover()">
        <DateInput data-field="cutoff" :label="t('Cutover date')" v-model="cutoff" type="date" required @change="opening = undefined" />
        <button class="primary" :disabled="busy">{{ t('Preview opening balances') }}</button>
      </form>
      <p v-if="config.activated_at">{{ t('Accounting activated.') }} · {{ config.cutoff }}</p>
      <section v-if="opening" class="panel record-form purchase-form">
        <h2>{{ t('Opening balance review') }} · {{ opening.cutoff }}</h2>
        <p>{{ t('Historical movements') }} · {{ opening.movement_count }}</p>
        <div class="table-panel"><table><thead><tr><th>{{ t('Site') }}</th><th>{{ t('Inventory value') }} AED</th></tr></thead>
          <tbody><tr v-for="row in opening.inventory" :key="row.site_id"><td>{{ siteName(row.site_id) }}</td><td>{{ row.value }}</td></tr></tbody></table></div>
        <div class="table-panel"><table><thead><tr><th>{{ t('Site') }}</th><th>{{ t('Movement') }}</th><th>{{ t('Supplier') }}</th><th>GRN</th><th>GRNI · AED</th></tr></thead>
          <tbody><tr v-for="row in opening.grni" :key="row.movement_id"><td>{{ siteName(row.site_id) }}</td><td>#{{ row.movement_id }}</td><td>{{ row.supplier }}</td><td>{{ row.receipt }}</td><td>{{ row.value }}</td></tr></tbody></table></div>
        <div v-if="opening.in_transit?.length" class="table-panel"><table><thead><tr><th>{{ t('Site') }}</th><th>{{ t('Movement') }}</th><th>{{ t('In transit') }} · AED</th></tr></thead>
          <tbody><tr v-for="row in opening.in_transit" :key="row.movement_id"><td>{{ siteName(row.site_id) }}</td><td>#{{ row.movement_id }}</td><td>{{ row.value }}</td></tr></tbody></table></div>
        <div class="field-wrap"><label class="delegation-check"><input data-field="accept" v-model="accept" type="checkbox">{{ t('I reviewed inventory and open GRNI.') }}</label><FieldHint :label="t('I reviewed inventory and open GRNI.')" /></div>
        <button class="primary" :disabled="busy || !accept" @click="cutover(true)">{{ t('Approve cutover') }}</button>
      </section>
    </template>
    <template v-if="form === 'references'">
      <form v-validated-form v-if="permissions('setup')" class="panel record-form" @submit.prevent="saveConfig('references', refForm)">
        <div class="field-wrap"><label>{{ t('Type') }}<select data-field="kind" v-model="refForm.kind"><option value="tax">{{ t('Tax code') }}</option></select></label><FieldHint :label="t('Type')" /></div>
        <div class="field-wrap"><label>{{ t('Code') }}<input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="refForm.code" maxlength="24" required></label><FieldHint :label="t('Code')" required /></div>
        <div class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="refForm.name" maxlength="120" required></label><FieldHint :label="t('Name')" required /></div>
        <div class="field-wrap" v-if="refForm.kind === 'tax'"><label>{{ t('Tax rate (%)') }}<input data-field="rate" :placeholder="t('e.g., 125.50')" v-model="refForm.rate" required inputmode="decimal"></label><FieldHint :label="t('Tax rate (%)')" required /></div>
        <DateInput data-field="starts" :label="t('Effective date')" v-if="refForm.kind === 'tax'" v-model="refForm.starts" required type="date" />
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
      </form>
      <section class="panel table-panel"><table><thead><tr><th>{{ t('Code') }}</th><th>{{ t('Name') }}</th><th>{{ t('Type') }}</th><th>{{ t('Effective date') }}</th><th>{{ t('Tax rate (%)') }}</th></tr></thead>
        <tbody><tr v-for="r in config.references" :key="r.id"><td>{{ r.code }}</td><td>{{ r.name }}</td><td>{{ t(r.kind === 'tax' ? 'Tax code' : 'Cost center') }}</td><td>{{ r.starts ?? '—' }}</td><td>{{ r.rate ?? '—' }}</td></tr></tbody></table></section>
    </template>
  </template>
</template>
