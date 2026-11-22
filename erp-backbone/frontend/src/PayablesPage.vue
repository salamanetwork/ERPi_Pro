<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
const pg=ref(1),ps=ref(25),term=ref(''),total=ref(0)
function pagedQuery(){return '?'+new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value})}

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, createRequest, requestKey, type Workspace } from './api'
import { message, t } from './i18n'
import DocumentTools from './DocumentTools.vue'

type Match = {movement_id: number; receipt: string; receipt_id: number; item_id: number; unit_id: number; po_line_id: number;
  quantity: string; unit_price: string; expected_price: string; available: string; returned: string; billed: string; amount: string; issues: string[]}
type Line = {movement_id: number; quantity: string; unit_price: string}
type Invoice = {id: number; number: string; supplier_number: string; supplier: string; supplier_id: number; site_id: number;
  order_id: number; order: string; invoice_date: string; due_date: string; posting_date: string | null;
  status: string; revision: number; total: string; declared_total: string; entry_id: number | null; reversal_entry_id: number | null; balance: string;
  can_post?: boolean; posting_routing?: {site_id: number; created_by: string; eligible_reviewers: string[]};
  lines: Line[]; match: Match[];
  settlements: {kind: string; date: string; reference: string; amount: string; entry_id: number; reason: string; return_id: number | null}[]}
type Credit = {movement_id: number; receipt: string; ready: boolean; amount: string}
type Templates = {items: Match[]; matched: boolean; total: string; issues: string[]}
type Aging = {as_of: string; items: (Invoice & {paid: string; credited: string; bucket: string})[]; total: string;
  buckets: Record<string, string>}
type Order = {id: number; number: string; supplier: string; site_id: number}

const {workspace} = defineProps<{workspace: Workspace}>()
const route = useRoute()
const router = useRouter()
const invoiceId = computed(() => Number(route.params.id) || 0)
const section = computed(() => route.params.section === 'aging' ? 'aging' : 'invoices')
const permits = (action: string, site?: number) => allows(workspace, 'ap.' + action, site)
const orders = ref<Order[]>([])
const rows = ref<Invoice[]>([])
const current = ref<Invoice>()
const eligible = ref<Match[]>([])
const uncredited = ref<Credit[]>([])
const busy = ref(false)
const loading = ref(false)
const error = ref('')
const status = ref('')
const createMode = ref(false)
const order = ref<number>()
const form = ref({supplier_number: '', invoice_date: '', due_date: '', declared_total: ''})
const quantities = ref<Record<number, {quantity: string; unit_price: string}>>({})
const today = () => new Intl.DateTimeFormat('sv-SE').format(new Date())
const date = ref(today())
const preview = ref<Templates>()
const result = ref<Aging>()
const asOf = ref(today())
const agingSite = ref('')
const agingSupplier = ref('')
const settlement = ref<'payment' | 'credit' | 'reverse'>()
const reference = ref('')
const reason = ref('')
const creditMove = ref<number>()
const allocations = ref<Record<number, string>>({})
const amount = ref('')
let create = createRequest()
let replay: {payload: string; key: string} | undefined
let sequence = 0

const editable = computed(() => createMode.value || (current.value?.status === 'draft' && permits('create', current.value.site_id)))
const candidateOrders = computed(() => orders.value.filter(row => permits('create', row.site_id)))
const supplierOptions = computed(() => Array.from(new Map(rows.value.map(row => [row.supplier_id, row.supplier]))))
const candidates = computed(() => current.value ? rows.value.filter(row => row.status === 'posted' &&
  row.supplier_id === current.value?.supplier_id && row.site_id === current.value?.site_id) : [])
const siteName = (id: number) => workspace.sites.find(row => row.id === id)?.name ?? String(id)
const issue = (code: string) => t(issueLabels[code] ?? code)
const issueLabels: Record<string, string> = {
  ap_invalid_receipt: 'Select a confirmed receipt line from this purchase order.',
  ap_quantity_conflict: 'Quantity exceeds the remaining unbilled receipt quantity.',
  ap_price_mismatch: 'Unit price differs from the issued purchase order.',
  ap_receipt_not_posted: 'Post the receipt and any pending returns first.',
  ap_before_source: 'The posting date is before a source entry.',
  ap_before_invoice: 'The posting date is before the invoice date.',
  ap_total_mismatch: 'The declared amount does not match the allocated amount.',
}

async function loadReceipts(key?: number) {
  eligible.value = key ? (await api<{items: Match[]}>('/ap/orders/' + key + '/receipts')).items : []
  quantities.value = Object.fromEntries(eligible.value.map(row => {
    const existing = current.value?.lines.find(line => line.movement_id === row.movement_id)
    return [row.movement_id, {quantity: existing?.quantity ?? '', unit_price: existing?.unit_price ?? row.expected_price}]
  }))
}
async function load() {
  const token = ++sequence
  loading.value = true
  current.value = undefined
  createMode.value = false
  preview.value = undefined
  settlement.value = undefined
  error.value = ''
  try {
    const [list, record] = await Promise.all([
      api<{items: Invoice[];count:number}>('/ap/invoices'+pagedQuery()),
      invoiceId.value ? api<Invoice>('/ap/invoices/' + invoiceId.value) : Promise.resolve(undefined),
    ])
    if (token !== sequence) return
    rows.value = list.items;total.value=list.count
    current.value = record
    if (record) {
      order.value = record.order_id
      form.value = {supplier_number: record.supplier_number, invoice_date: record.invoice_date, due_date: record.due_date,
        declared_total: record.declared_total}
      if (record.status === 'draft') await loadReceipts(record.order_id)
      else {
        uncredited.value = (await api<{items: Credit[]}>('/ap/invoices/' + record.id + '/credits')).items
      }
    }
  } catch (failure) {
    if (token === sequence) error.value = message(failure)
  } finally {
    if (token === sequence) loading.value = false
  }
}
watch([pg,ps,term],load)
watch([invoiceId, section], () => { void load() }, {immediate: true})
watch(form, () => { preview.value = undefined }, {deep: true})
watch(quantities, () => { preview.value = undefined }, {deep: true})
watch(date, () => { preview.value = undefined })

async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = status.value = ''
  try { await action() } catch (failure) { error.value = message(failure) } finally { busy.value = false }
}
function start() {
  return run(async () => {
    create = createRequest()
    orders.value = (await api<{items: Order[]}>('/ap/orders')).items
    const from = Number(route.query.order)
    order.value = candidateOrders.value.find(row => row.id === from)?.id
    form.value = {supplier_number: '', invoice_date: today(), due_date: '', declared_total: ''}
    await loadReceipts(order.value)
    createMode.value = true
  })
}
const enteredLines = computed(() => Object.entries(quantities.value)
  .filter(([, line]) => line.quantity && Number(line.quantity) !== 0)
  .map(([key, line]) => ({movement_id: Number(key), ...line})))
function invoiceLineField(movement: number, field: string) {
  const index = enteredLines.value.findIndex(row => row.movement_id === movement)
  return index < 0 ? undefined : `lines.${index}.${field}`
}
function save() {
  return run(async () => {
    const data = {...form.value, lines: enteredLines.value}
    if (current.value) {
      await api('/ap/invoices/' + current.value.id, 'PATCH', {...data, revision: current.value.revision})
      await load()
      status.value = 'Changes saved.'
    } else {
      const row = await create<Invoice>('/ap/invoices', {...data, order_id: order.value})
      await router.push('/payables/invoices/' + row.id)
    }
  })
}
async function mutate(path: string, data: object) {
  const payload = JSON.stringify([path, data])
  if (replay?.payload !== payload) replay = {payload, key: requestKey()}
  await api(path, 'POST', data, replay.key)
}
function match(post = false) {
  const row = current.value
  if (!row) return
  return run(async () => {
    const path = '/ap/invoices/' + row.id
    const data = {posting_date: date.value, revision: row.revision}
    if (post) {
      await mutate(path + '/post', data)
      await load()
      status.value = 'Supplier invoice posted.'
    } else preview.value = await api<Templates>(path + '/match', 'POST', data)
  })
}
function startSettlement(kind: 'payment' | 'credit' | 'reverse') {
  settlement.value = kind
  reference.value = reason.value = amount.value = ''
  creditMove.value = undefined
  allocations.value = {}
}
function settle() {
  const row = current.value
  if (!row) return
  return run(async () => {
    const data = {posting_date: date.value, reference: reference.value, reason: reason.value}
    if (settlement.value === 'payment') {
      await mutate('/ap/payments', {...data, allocations: Object.entries(allocations.value)
        .filter(([, amount]) => Number(amount) > 0).map(([id, amount]) => ({invoice_id: Number(id), amount}))})
    } else if (settlement.value === 'reverse') await mutate('/ap/invoices/' + row.id + '/reverse', {...data, revision: row.revision})
    else await mutate('/ap/invoices/' + row.id + '/credits', {...data, movement_id: creditMove.value, amount: amount.value})
    await load()
    status.value = 'Supplier settlement posted.'
  })
}
const showAging = () => run(async () => {
  result.value = await api<Aging>('/ap/aging?' + new URLSearchParams({as_of: asOf.value, site: agingSite.value,
    supplier: agingSupplier.value}))
})
import DateInput from './DateInput.vue'
</script>

<template>
  <RouterLink v-if="invoiceId" class="back-link" to="/payables/invoices">‹ {{ t('Supplier invoices') }}</RouterLink>
  <h1>{{ current?.number ?? t(section === 'aging' ? 'AP aging' : 'Supplier invoices') }}</h1>
  <nav v-if="!invoiceId" class="section-tabs">
    <RouterLink class="tab-link" to="/payables/invoices" :aria-current="section === 'invoices' ? 'page' : undefined">{{ t('Supplier invoices') }}</RouterLink>
    <RouterLink class="tab-link" to="/payables/aging" :aria-current="section === 'aging' ? 'page' : undefined">{{ t('AP aging') }}</RouterLink>
  </nav>
  <div v-if="!invoiceId" class="actions"><button v-if="section === 'invoices' && permits('create') && !createMode" class="primary" @click="start">{{ t('New supplier invoice') }}</button></div>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="status" class="success" role="status">{{ t(status) }}</p>
  <p v-if="loading">{{ t('Loading') }}…</p>
  <template v-else-if="section === 'aging'">
    <form v-validated-form class="actions" @submit.prevent="showAging">
      <DateInput data-field="asOf" :label="t('As of date')" v-model="asOf" type="date" required />
      <div class="field-wrap"><label>{{ t('Site') }}<select data-field="agingSite" v-model="agingSite"><option value="">{{ t('all') }}</option>
        <option v-for="site in workspace.sites.filter(s => permits('view', s.id))" :key="site.id" :value="String(site.id)">{{ site.name }}</option></select></label><FieldHint :label="t('Site')" /></div>
      <EntityAutocomplete data-field="agingSupplier" :label="t('Supplier')" lookup="suppliers" :choices="Array.from(supplierOptions).map(([key, supplier]) => ({ id: String(key), name: String(supplier) }))" :placeholder="t('all')" :empty-value="''" v-model="agingSupplier" />
      <button class="primary" :disabled="busy">{{ t('Run report') }}</button>
    </form>
    <section v-if="result" class="panel table-panel">
      <p>{{ t('As of date') }} · {{ result.as_of }} · {{ t('Open balance') }} · {{ result.total }} AED</p>
      <p v-for="(value, key) in result.buckets" :key="key">{{ t(key === 'current' ? 'Not overdue' : key === 'credit' ? 'Supplier credits' : key) }} · {{ value }}</p>
      <table><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Supplier') }}</th><th>{{ t('Site') }}</th><th>{{ t('Due date') }}</th>
        <th>{{ t('Total') }}</th><th>{{ t('Paid') }}</th><th>{{ t('Credited') }}</th><th>{{ t('Open balance') }}</th><th>{{ t('Aging bucket') }}</th></tr></thead>
        <tbody><tr v-for="row in result.items" :key="row.id">
          <td><RouterLink :to="'/payables/invoices/' + row.id">{{ row.number }}</RouterLink> · <bdi>{{ row.supplier_number }}</bdi></td>
          <td>{{ row.supplier }}</td><td>{{ siteName(row.site_id) }}</td><td>{{ row.due_date }}</td><td>{{ row.total }}</td><td>{{ row.paid }}</td>
          <td>{{ row.credited }}</td><td>{{ row.balance }}</td><td>{{ t(row.bucket === 'current' ? 'Not overdue' : row.bucket === 'credit' ? 'Supplier credits' : row.bucket) }}</td>
        </tr></tbody></table>
    </section>
  </template>
  <section v-else-if="!invoiceId && !createMode" class="panel table-panel">
    <TableToolbar v-if="!invoiceId" :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table v-if="rows.length"><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Supplier invoice number') }}</th><th>{{ t('Supplier') }}</th>
      <th>{{ t('Purchase order') }}</th><th>{{ t('Status') }}</th><th>{{ t('Total') }}</th></tr></thead>
      <tbody><tr v-for="row in rows" :key="row.id"><td><RouterLink :to="'/payables/invoices/' + row.id">{{ row.number }}</RouterLink></td>
        <td>{{ row.supplier_number }}</td><td>{{ row.supplier }}</td><td>{{ row.order }}</td><td>{{ t(row.status === 'draft' ? 'Draft' : row.status === 'reversed' ? 'Reversed' : 'Posted') }}</td>
        <td>{{ row.declared_total }} AED</td></tr></tbody></table>
    <p v-else class="empty muted">{{ t('No supplier invoices.') }}</p>
  </section>
  <template v-else-if="createMode || current">
    <form v-validated-form class="panel record-form" @submit.prevent="save">
      <div class="field-wrap" v-if="createMode"><label>{{ t('Purchase order') }}<select data-field="order" v-model="order" required @change="run(() => loadReceipts(order))">
        <option v-for="row in candidateOrders" :key="row.id" :value="row.id">{{ row.number }} · {{ row.supplier }} · {{ siteName(row.site_id) }}</option>
      </select></label><FieldHint :label="t('Purchase order')" required /></div>
      <p v-else-if="current">{{ t('Purchase order') }} · {{ current.order }} · {{ current.supplier }} · {{ siteName(current.site_id) }}</p>
      <div class="field-wrap"><label>{{ t('Supplier invoice number') }}<input data-field="supplier_number" :placeholder="t('Enter {field}').replace('{field}', t('Supplier invoice number'))" v-model="form.supplier_number" required maxlength="100" :readonly="!editable"></label><FieldHint :label="t('Supplier invoice number')" required :readonly="!editable" /></div>
      <DateInput data-field="invoice_date" :label="t('Invoice date')" v-model="form.invoice_date" type="date" required :readonly="!editable" />
      <DateInput data-field="due_date" :label="t('Due date')" v-model="form.due_date" type="date" required :readonly="!editable" />
      <div class="field-wrap"><label>{{ t('Supplier invoice total (AED)') }}<input data-field="declared_total" :placeholder="t('e.g., 125.50')" v-model="form.declared_total" inputmode="decimal" required :readonly="!editable"></label><FieldHint :label="t('Supplier invoice total (AED)')" required :readonly="!editable" /></div>
      <div v-if="editable" class="table-panel">
        <table><thead><tr><th>{{ t('Goods receipt') }}</th><th>{{ t('PO line') }}</th><th>{{ t('Available') }}</th>
          <th>{{ t('Billed quantity') }}</th><th>{{ t('Returned quantity') }}</th><th>{{ t('Quantity') }}</th><th>{{ t('Unit price') }}</th></tr></thead>
          <tbody><tr v-for="row in eligible" :key="row.movement_id">
            <td>{{ row.receipt }} · #{{ row.movement_id }}</td><td>{{ row.po_line_id }}</td><td>{{ row.available }}</td><td>{{ row.billed }}</td><td>{{ row.returned }}</td>
            <td><div class="field-wrap table-field"><input :data-field="invoiceLineField(row.movement_id, 'quantity')" :placeholder="t('e.g., 2.500')" v-model="quantities[row.movement_id]!.quantity" inputmode="decimal" :aria-label="t('Quantity') + ' ' + row.movement_id"><FieldHint :label="t('Quantity')" /></div></td>
            <td><div class="field-wrap table-field"><input :data-field="invoiceLineField(row.movement_id, 'unit_price')" :placeholder="t('e.g., 125.50')" v-model="quantities[row.movement_id]!.unit_price" inputmode="decimal" :aria-label="t('Unit price') + ' ' + row.movement_id"><FieldHint :label="t('Unit price')" /></div></td>
          </tr></tbody></table>
      </div>
      <button v-if="editable" class="primary" :disabled="busy">{{ t('Save draft') }}</button>
    </form>
    <section v-if="current?.status === 'draft'" class="panel record-form approval-section">
      <h2>{{ t('Three-way match') }}</h2>
      <div v-if="current.posting_routing">
        <h3>{{ t('Posting routing') }}</h3>
        <p>{{ t('Site') }} · {{ siteName(current.posting_routing.site_id) }} ·
          {{ t('Created by') }} · <bdi>{{ current.posting_routing.created_by }}</bdi></p>
        <p>{{ t('A different user needs both permissions at this site.') }}
          <bdi><code>ap.view</code> + <code>ap.post</code></bdi></p>
        <p v-if="current.posting_routing.eligible_reviewers.length">
          {{ t('Eligible reviewers') }} · <bdi>{{ current.posting_routing.eligible_reviewers.join(', ') }}</bdi></p>
        <p v-else class="alert">{{ t('No eligible supplier invoice reviewer at this site.') }}</p>
        <RouterLink to="/access">{{ t('Access control') }}</RouterLink>
      </div>
      <form v-validated-form class="record-form" @submit.prevent="match(($event as SubmitEvent).submitter?.getAttribute('value') === 'post')">
        <DateInput data-field="posting_date" :label="t('Posting date')" v-model="date" type="date" required />
        <div class="actions">
          <button class="secondary" :disabled="busy">{{ t('Match') }}</button>
          <button v-if="current.can_post" class="primary" value="post" :disabled="busy || !preview?.matched">{{ t('Post supplier invoice') }}</button>
        </div>
      </form>
      <template v-if="preview">
        <p>{{ t('Matched amount') }} · {{ preview.total }} AED</p>
        <p v-for="code in preview.issues" :key="code" class="alert">{{ issue(code) }}</p>
        <ul><li v-for="row in preview.items" :key="row.movement_id">{{ row.receipt }} · #{{ row.movement_id }} · {{ t('PO price') }} {{ row.expected_price }}
          · {{ t('Unit price') }} {{ row.unit_price }} · {{ t('Available') }} {{ row.available }} · {{ row.amount }} AED
          <span v-for="code in row.issues" :key="code" class="alert">{{ issue(code) }}</span></li></ul>
      </template>
    </section>
    <section v-else-if="current" class="panel record-form approval-section">
      <h2>{{ t('Supplier settlements') }}</h2>
      <p>{{ t('Open balance') }} · {{ current.balance }} AED · {{ t('Posting date') }} · {{ current.posting_date }}
        · <RouterLink :to="{path: '/accounting/journals', query: {entry: current.entry_id}}">JE-{{ current.entry_id }}</RouterLink></p>
      <ul><li v-for="line in current.match" :key="line.movement_id">{{ line.receipt }} · #{{ line.movement_id }}
        · {{ line.quantity }} × {{ line.unit_price }} · {{ line.amount }} AED</li></ul>
      <ul><li v-for="row in current.settlements" :key="row.entry_id + ':' + row.return_id">{{ t(row.kind === 'payment' ? 'Payment' : 'Supplier credit note') }}
        · {{ row.reference }} · {{ row.date }} · {{ row.amount }} AED
        · <RouterLink :to="{path: '/accounting/journals', query: {entry: row.entry_id}}">JE-{{ row.entry_id }}</RouterLink></li></ul>
      <p v-if="current.reversal_entry_id"><RouterLink :to="{path: '/accounting/journals', query: {entry: current.reversal_entry_id}}">{{ t('Reversal journal') }}</RouterLink></p>
      <div v-if="current.status === 'posted'" class="actions">
        <button v-if="permits('post', current.site_id) && !current.settlements.length" class="secondary" @click="startSettlement('reverse')">{{ t('Reverse supplier invoice') }}</button>
        <button v-if="permits('pay', current.site_id)" class="secondary" @click="startSettlement('payment')">{{ t('Record supplier payment') }}</button>
        <button v-if="permits('credit', current.site_id) && uncredited.length" class="secondary" @click="startSettlement('credit')">{{ t('Supplier credit note') }}</button>
      </div>
      <form v-validated-form v-if="settlement" class="record-form" @submit.prevent="settle">
        <DateInput data-field="date" :label="t('Posting date')" v-model="date" type="date" required />
        <div class="field-wrap" v-if="settlement !== 'reverse'"><label>{{ t('Reference') }}<input data-field="reference" :placeholder="t('e.g., REF-001')" v-model="reference" required maxlength="100"></label><FieldHint :label="t('Reference')" required /></div>
        <div class="field-wrap"><label>{{ t('Reason') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="reason" :required="settlement !== 'payment'" maxlength="1000"></label><FieldHint :label="t('Reason')" :required="settlement !== 'payment'" /></div>
        <template v-if="settlement === 'credit'">
          <div class="field-wrap"><label>{{ t('Supplier return') }}<select data-field="creditMove" v-model="creditMove" required>
            <option v-for="row in uncredited.filter(item => item.ready)" :key="row.movement_id + ':' + row.amount" :value="row.movement_id">
              {{ row.receipt }} · #{{ row.movement_id }} · {{ row.amount }} AED
            </option></select></label><FieldHint :label="t('Supplier return')" required /></div>
          <div class="field-wrap"><label>{{ t('Credit amount (AED)') }}<input data-field="amount" :placeholder="t('e.g., 125.50')" v-model="amount" required inputmode="decimal"></label><FieldHint :label="t('Credit amount (AED)')" required /></div>
        </template>
        <div class="field-wrap" v-for="row in settlement === 'payment' ? candidates : []" :key="row.id"><label>{{ row.number }} · {{ row.supplier_number }}
          <input :placeholder="t('e.g., 125.50')" v-model="allocations[row.id]" inputmode="decimal"></label><FieldHint :label="row.number" /></div>
        <button class="primary" :disabled="busy">{{ t('Post settlement') }}</button>
      </form>
    </section>
    <DocumentTools v-if="current" :key="current.id" :id="current.id" :revision="current.revision" kind="si"
      :editable="editable" :archived="false" :show-actions="false" />
  </template>
</template>
