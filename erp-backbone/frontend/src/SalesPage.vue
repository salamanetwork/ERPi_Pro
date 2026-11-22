<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPaging,ref as pagingRef} from 'vue'
const pg=pagingRef(1),ps=pagingRef(25),term=pagingRef(''),total=pagingRef(0)
function queryPage(){return new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}).toString()}

import CsvExport from './CsvExport.vue'
import SavedViews from './SavedViews.vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, createRequest, type Workspace } from './api'
import { message, t } from './i18n'

type Customer = {id?: number; code: string; name: string; legal_name: string; contact: string; billing: string; shipping: string; tax_id: string; terms: number; active: boolean; revision?: number}
type Line = {id: number; item_id: number; name: string; quantity: string; price: string; amount: string; cost?: string; location_id?: number; invoice_id?: number}
type Doc = {id: number; kind: string; number: string; status: string; site_id: number; customer_id: number; customer: string; date: string; due?: string; expires?: string;
  business_date: string; source_id?: number; source_number?: string; order_id?: number; revision: number; amount: string; outstanding?: string; bucket?: string; reason: string;
  lines: Line[]; posting?: {state: string; error: string; entry_id?: number}; children?: {id: number; number: string; kind: string}[];
  snapshot?: {cost_center_id?:number|null}; cost_center_editable?:boolean; cost_centers?:{id:number;name:string}[];
  progress?: {line_id: number; delivered: string; invoiced: string; remaining: string}[]}
type Catalog = {items: {id: number; code: string; name: string; base_unit_id: number; active: boolean}[];
  units: {id: number; code: string}[]; locations: {id: number; code: string; name: string; warehouse_id: number}[];
  warehouses: {id: number; site_id: number; name: string}[]}
type Input = {item_id?: number; line_id?: number; quantity?: string; price?: string; location_id?: number; invoice_id?: number; amount?: string}
type Form = {kind: string; date: string; expires?: string; site_id?: number; customer_id?: number; source_id?: number; lines: Input[]; reason?: string}

const {workspace} = defineProps<{workspace: Workspace}>()
const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id) || 0)
const scope = (action: string, site?: number | null) => allows(workspace, 'sales.' + action, site)
const names: Record<string, string> = {quote: 'Quotation', order: 'Sales order', delivery: 'Delivery', invoice: 'Sales invoice',
  receipt: 'Customer receipt', return: 'Customer return', credit: 'Credit note'}
const label = (key: string) => t(names[key] ?? key)
function localToday() {
  const now = new Date()
  return [now.getFullYear(), now.getMonth() + 1, now.getDate()].map(value => String(value).padStart(2, '0')).join('-')
}
const tabs = ['Sales documents', 'Customers', 'AR aging']
const tab = computed(() => route.params.section === 'aging' ? 'AR aging' : 'Sales documents')
const filter = ref('')
const rows = ref<Doc[]>([])
const current = ref<Doc>()
const next = ref<number | null>(null)
const customerRows = ref<Customer[]>([])
const catalog = ref<Catalog>()
const draft = ref<Form>()
const amend = ref(false)
const cancelReason = ref('')
const cancel = ref(false)
const invoiceChoices = ref<Doc[]>([])
const asOf = ref('')
const error = ref('')
const notice = ref('')
const busy = ref(false)
let create = createRequest()
const siteName = (key: number) => workspace.sites.find(s => s.id === key)?.name ?? '#' + key
const warehouses = computed(() => new Map(catalog.value?.warehouses.map(w => [w.id, w])))
const locations = computed(() => catalog.value?.locations.filter(loc => warehouses.value.get(loc.warehouse_id)?.site_id === current.value?.site_id) ?? [])

async function run(task: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try { await task() } catch (failure) { error.value = message(failure) } finally { busy.value = false }
}
async function loadDocs(before?: number) {
  const query = new URLSearchParams()
  if (filter.value) query.set('kind', filter.value)
  new URLSearchParams(queryPage()).forEach((v,k)=>query.set(k,v))
  if (before) query.set('before', String(before))
  const data = await api<{items: Doc[]; count:number; next_before: number | null}>('/sales/documents?' + query)
  rows.value = before ? [...rows.value, ...data.items] : data.items
  next.value = data.next_before;total.value=data.count
}
async function loadCustomers() {
  customerRows.value = (await api<{items: Customer[]}>('/sales/customers')).items
}
watchPaging([pg,ps,term],()=>load().catch(e=>error.value=message(e)))
async function load() {
  current.value = undefined
  draft.value = undefined
  cancel.value = false
  if (id.value) {
    current.value = await api<Doc>('/sales/documents/' + id.value)
  } else if (tab.value === 'AR aging') {
    const result = await api<{items: Doc[];count:number; as_of: string}>('/sales/aging?'+queryPage())
    rows.value = result.items
    asOf.value = result.as_of;total.value=result.count
  } else {await loadDocs();if(route.query.compose==='1' && scope('quote')){await router.replace({query:{...route.query,compose:undefined}});start('quote')}}
}
watch([id, () => route.params.section, () => route.query.kind], () => {
  filter.value = route.params.section === 'quotations' ? 'quote' :
    typeof route.query.kind === 'string' && Object.prototype.hasOwnProperty.call(names, route.query.kind) ? route.query.kind : ''
  error.value = ''
  // Navigation can happen while a create request is still unwinding.
  void load().catch(failure => { error.value = message(failure) })
}, {immediate: true})
function go(page: string) {
  void router.push(page === 'Customers' ? '/sales/customers' : page === 'AR aging' ? '/sales/aging' : '/sales')
}
function changeFilter() {
  if (filter.value === 'quote') void router.push('/sales/quotations')
  else if (route.params.section === 'quotations') {
    const selected = filter.value
    void router.push({path: '/sales', query: selected ? {kind: selected} : {}})
  } else void router.replace({query: filter.value ? {kind: filter.value} : {}})
}
async function loadCatalog() { catalog.value = await api<Catalog>('/sales/catalog') }
function start(kind: string, edit = false) {
  void run(async () => {
    const source = current.value
    if (kind === 'quote') { await Promise.all([loadCatalog(), loadCustomers()]) }
    if (kind === 'delivery') await loadCatalog()
    if (kind === 'receipt' && source) {
      const data = await api<{items: Doc[]}>('/sales/aging')
      invoiceChoices.value = data.items.filter(row => row.customer_id === source.customer_id
        && row.site_id === source.site_id && Number(row.outstanding) > 0)
    }
    create = createRequest()
    amend.value = edit
    draft.value = {kind, date: edit ? source!.date : source?.business_date || localToday(),
      expires: edit ? source!.expires : undefined, source_id: source?.id,
      lines: kind === 'order' ? [] : kind === 'receipt' ? [{invoice_id: source?.id, amount: ''}]
        : edit ? source!.lines.map(line => ({item_id: line.item_id, quantity: line.quantity, price: line.price}))
          : [{quantity: '', price: ''}]}
  })
}
function save() {
  void run(async () => {
    const data = draft.value!
    if (amend.value) {
      current.value = await api<Doc>('/sales/documents/' + current.value!.id, 'PATCH',
        {action: 'edit', revision: current.value!.revision, lines: data.lines})
    } else {
      const result = await create<Doc>('/sales/documents', data)
      await router.push('/sales/' + result.id)
    }
    draft.value = undefined
    notice.value = 'Changes saved.'
  })
}
const salesCenter = ref<number|null>(null)
watch(current, row => { salesCenter.value = row?.snapshot?.cost_center_id ?? null })
function transition(action: string) {
  void run(async () => {
    current.value = await api<Doc>('/sales/documents/' + current.value!.id, 'PATCH',
      {action, revision: current.value!.revision, reason: cancelReason.value, cost_center_id: salesCenter.value})
    cancel.value = false
    cancelReason.value = ''
    notice.value = 'Changes saved.'
  })
}
function actions(row: Doc) {
  if (row.status === 'cancelled') return []
  if (row.kind === 'quote') return row.status === 'accepted' && !row.children?.length && scope('order', row.site_id) ? ['order'] : []
  if (row.kind === 'order') return row.status === 'confirmed' && scope('deliver', row.site_id) ? ['delivery'] : []
  if (row.kind === 'delivery') return [scope('invoice', row.site_id) ? 'invoice' : '', scope('return', row.site_id) ? 'return' : ''].filter(Boolean)
  if (row.kind === 'invoice') return [scope('collect', row.site_id) && Number(row.outstanding) > 0 ? 'receipt' : '', scope('credit', row.site_id) ? 'credit' : ''].filter(Boolean)
  if (row.kind === 'credit' && scope('return', row.site_id)) return ['return']
  return []
}
import DateInput from './DateInput.vue'
</script>

<template>
  <h1>{{ t('Sales') }}</h1>
  <nav class="section-tabs" :aria-label="t('Sales')">
    <button v-for="page in tabs" :key="page" class="tab-link" :aria-current="tab === page ? 'page' : undefined" :disabled="busy" @click="go(page)">{{ t(page) }}</button>
  </nav>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
    <template v-if="tab === 'Sales documents'">
      <SavedViews v-if="!id && !draft" :workspace="workspace" screen="sales-documents" :filters="{kind: filter}"
        :skip-default="Boolean(route.params.section || route.query.kind)" @apply="filter = $event.kind ?? ''; changeFilter()" />
      <button v-if="!id && scope('quote') && !draft" class="primary" :disabled="busy" @click="start('quote')">{{ t('New quotation') }}</button>
      <form v-validated-form v-if="draft" class="panel record-form purchase-form" @submit.prevent="save">
        <h2>{{ label(draft.kind) }}</h2>
        <DateInput data-field="date" :label="t('Date')" v-model="draft.date" type="date" required :readonly="amend || ['delivery','return'].includes(draft.kind)" />
        <template v-if="draft.kind === 'quote' && !amend">
          <DateInput data-field="expires" :label="t('Valid through')" v-model="draft.expires" type="date" required />
          <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site_id" v-model="draft.site_id" required><option v-for="site in workspace.sites.filter(s => s.active && scope('quote', s.id) && scope('view', s.id))" :key="site.id" :value="site.id">{{ site.name }}</option></select></label><FieldHint :label="t('Site')" required /></div>
          <EntityAutocomplete
            data-field="customer_id"
            :label="t('Customer')"
            lookup="customers"
            :choices="customerRows.filter(c => c.active)"
            :placeholder="t('Select customer')"
            :empty-value="undefined"
            required
            v-model="draft.customer_id"
          />
        </template>
        <fieldset v-for="(line, index) in draft.lines" :key="index" class="purchase-line">
          <legend>{{ t('Line item') }} {{ index + 1 }}</legend>
          <template v-if="draft.kind === 'receipt'">
            <div class="field-wrap"><label>{{ t('Sales invoice') }}<select :data-field="'lines.' + index + '.invoice_id'" v-model="line.invoice_id" required><option v-for="row in invoiceChoices" :key="row.id" :value="row.id">{{ row.number }} · {{ row.outstanding }} AED</option></select></label><FieldHint :label="t('Sales invoice')" required /></div>
            <div class="field-wrap"><label>{{ t('Amount') }}<input :data-field="'lines.' + index + '.amount'" :placeholder="t('e.g., 125.50')" v-model="line.amount" inputmode="decimal" required></label><FieldHint :label="t('Amount')" required /></div>
          </template>
          <template v-else>
            <EntityAutocomplete
              v-if="draft.kind === 'quote'"
              :data-field="'lines.' + index + '.item_id'"
              :label="t('Item')"
              lookup="items"
              :choices="(catalog?.items ?? []).filter(i => i.active).map(i => ({ ...i, subtitle: catalog?.units.find(u => u.id === i.base_unit_id)?.code }))"
              :placeholder="t('Select item')"
              :empty-value="undefined"
              required
              v-model="line.item_id"
            />
            <div class="field-wrap" v-else><label>{{ t('Source line') }}<select :data-field="'lines.' + index + '.line_id'" v-model="line.line_id" required><option v-for="item in current?.lines" :key="item.id" :value="item.id">{{ item.id }} · {{ item.name }} · {{ item.quantity }}</option></select></label><FieldHint :label="t('Source line')" required /></div>
            <div class="field-wrap" v-if="draft.kind === 'delivery'"><label>{{ t('Location') }}<EntityAutocomplete v-model="line.location_id" lookup="locations" :site-id="draft.site_id" :local-items="locations.map(loc => ({ id: loc.id, label: (warehouses.get(loc.warehouse_id)?.name ?? '') + ' / ' + loc.name }))" :data-field="'lines.' + index + '.location_id'" required /></label><FieldHint :label="t('Location')" required /></div>
            <div class="field-wrap"><label>{{ t('Quantity') }}<input :data-field="'lines.' + index + '.quantity'" :placeholder="t('e.g., 2.500')" v-model="line.quantity" inputmode="decimal" required></label><FieldHint :label="t('Quantity')" required /></div>
            <div class="field-wrap" v-if="draft.kind === 'quote'"><label>{{ t('Unit price') }}<input :data-field="'lines.' + index + '.price'" :placeholder="t('e.g., 125.50')" v-model="line.price" inputmode="decimal" required></label><FieldHint :label="t('Unit price')" required /></div>
          </template>
          <button v-if="draft.lines.length > 1" type="button" class="secondary" @click="draft.lines.splice(index, 1)">{{ t('Remove item') }}</button>
        </fieldset>
        <div class="field-wrap" v-if="['credit','return','receipt'].includes(draft.kind)"><label>{{ t('Reason') }}<textarea data-field="reason" :placeholder="t('Explain the reason')" v-model="draft.reason" maxlength="1000" :required="draft.kind !== 'receipt'"></textarea></label><FieldHint :label="t('Reason')" :required="draft.kind !== 'receipt'" /></div>
        <div class="actions">
          <button v-if="draft.kind !== 'order'" type="button" class="secondary" :disabled="draft.lines.length >= 100" @click="draft.lines.push({quantity: '', price: '', amount: ''})">{{ t('Add item') }}</button>
          <button class="primary" :disabled="busy">{{ t(draft.kind === 'delivery' ? 'Confirm delivery' : draft.kind === 'invoice' ? 'Issue invoice' : draft.kind === 'return' ? 'Confirm return' : 'Save') }}</button>
          <button type="button" class="secondary" @click="draft = undefined">{{ t('Cancel') }}</button>
        </div>
      </form>
      <section v-if="current && !draft" class="panel record-form purchase-form">
        <div v-if="current.kind === 'order' && current.cost_center_editable && scope('order',current.site_id)" class="actions">
          <div class="field-wrap"><label>{{ t('Cost center') }}<EntityAutocomplete v-model="salesCenter" lookup="cost_centers" :site-id="current.site_id" :local-items="(current.cost_centers ?? []).map(row => ({ id: row.id, label: row.name }))" :empty-label="t('Unassigned')" data-field="salesCenter" /></label><FieldHint :label="t('Cost center')" /></div>
          <button class="secondary" :disabled="busy" @click="transition('cost_center')">{{ t('Save cost center') }}</button>
        </div>
        <p v-else-if="current.snapshot?.cost_center_id">{{ t('Cost center') }} · {{ current.cost_centers?.find(row=>row.id===current?.snapshot?.cost_center_id)?.name ?? '#' + current.snapshot.cost_center_id }}</p>
        <h2>{{ current.number }} · {{ label(current.kind) }} · {{ t(current.status) }}</h2>
        <p>{{ current.customer }} · {{ siteName(current.site_id) }} · {{ current.date }}</p>
        <p v-if="current.expires">{{ t('Valid through') }} · {{ current.expires }}</p>
        <p v-if="current.due">{{ t('Due date') }} · {{ current.due }} · {{ t('Outstanding') }} {{ current.outstanding }} AED</p>
        <p v-if="current.reason">{{ t('Reason') }} · {{ current.reason }}</p>
        <p v-if="current.posting">{{ t('Posting status') }} · {{ t(current.posting.state) }}<span v-if="current.posting.error"> · {{ t(current.posting.error) }}</span></p>
        <RouterLink v-if="current.posting?.entry_id && allows(workspace, 'accounting.view', null)"
          :to="{path: '/accounting', query: {entry: current.posting.entry_id}}">{{ t('Journal entry') }}</RouterLink>
        <RouterLink v-if="current.source_id" :to="'/sales/' + current.source_id">{{ t('Source document') }} · {{ current.source_number }}</RouterLink>
        <div class="table-panel"><table><thead><tr><th>{{ t('Line item') }}</th><th>{{ t('Quantity') }}</th><th>{{ t('Amount') }} AED</th>
          <template v-if="current.progress"><th>{{ t('Delivered') }}</th><th>{{ t('Invoiced') }}</th><th>{{ t('Remaining') }}</th></template></tr></thead>
          <tbody><tr v-for="line in current.lines" :key="line.id || line.invoice_id"><td>{{ line.name || (t('Sales invoice') + ' #' + line.invoice_id) }}</td>
            <td>{{ line.quantity ?? '—' }}</td><td>{{ line.amount }}</td>
            <template v-if="current.progress"><td>{{ current.progress.find(p => p.line_id === line.id)?.delivered }}</td><td>{{ current.progress.find(p => p.line_id === line.id)?.invoiced }}</td><td>{{ current.progress.find(p => p.line_id === line.id)?.remaining }}</td></template>
          </tr></tbody></table></div>
        <p>{{ t('Total') }} · {{ current.amount }} AED</p>
        <div class="actions">
          <a v-if="['quote','invoice','credit'].includes(current.kind)" class="button secondary" :href="'/sales/print/' + current.id" target="_blank" rel="noopener">{{ t('Print or save as PDF') }}</a>
          <template v-if="current.kind === 'quote' && scope('quote', current.site_id)">
            <button v-if="current.status === 'draft'" class="secondary" :disabled="busy" @click="start('quote', true)">{{ t('Edit') }}</button>
            <button v-if="current.status === 'draft'" class="primary" :disabled="busy" @click="transition('send')">{{ t('Mark as sent') }}</button>
            <button v-if="current.status === 'sent'" class="primary" :disabled="busy" @click="transition('accept')">{{ t('Accept quotation') }}</button>
          </template>
          <button v-if="current.kind === 'order' && current.status === 'draft' && scope('order', current.site_id)" class="primary" :disabled="busy" @click="transition('confirm')">{{ t('Confirm order') }}</button>
          <RouterLink v-if="current.kind==='order'&&current.status==='confirmed'&&workspace.modules.some(module=>module.id==='supply'&&module.enabled)&&allows(workspace,'supply.view',current.site_id)"
                      class="secondary" :to="{path:'/supply/reservations',query:{order:current.id}}">{{ t('Reservations') }}</RouterLink>
          <button v-for="kind in actions(current)" :key="kind" class="primary" :disabled="busy" @click="start(kind)">{{ label(kind) }}</button>
          <button v-if="!cancel && ((current.kind === 'quote' && scope('quote', current.site_id) && !current.children?.length && current.status !== 'cancelled') ||
            (current.kind === 'order' && scope('order', current.site_id) && current.status !== 'cancelled'))"
            class="secondary" @click="cancel = true">{{ t('Cancel remaining') }}</button>
        </div>
        <form v-validated-form v-if="cancel" @submit.prevent="transition('cancel')">
          <div class="field-wrap"><label>{{ t('Reason') }}<textarea data-field="cancelReason" :placeholder="t('Enter details')" v-model="cancelReason" maxlength="1000" required></textarea></label><FieldHint :label="t('Reason')" required /></div>
          <button class="secondary" :disabled="busy">{{ t('Confirm cancellation') }}</button>
        </form>
        <ul v-if="current.children?.length"><li v-for="row in current.children" :key="row.id"><RouterLink :to="'/sales/' + row.id">{{ row.number }} · {{ label(row.kind) }}</RouterLink></li></ul>
      </section>
      <div class="field-wrap" v-if="!id"><label>{{ t('Document type') }}<select data-field="filter" v-model="filter" @change="changeFilter"><option value="">{{ t('all') }}</option>
        <option v-for="(title, key) in names" :key="key" :value="key">{{ t(title) }}</option></select></label><FieldHint :label="t('Document type')" /></div>
    </template>
    <p v-if="tab === 'AR aging'">{{ t('As of') }} · {{ asOf }} · AED</p>
    <section v-if="!id" class="panel table-panel approval-section">
      <TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table v-if="rows.length"><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Customer') }}</th><th>{{ t('Site') }}</th>
        <template v-if="tab === 'AR aging'"><th>{{ t('Due date') }}</th><th>{{ t('Aging bucket') }}</th><th>{{ t('Outstanding') }} AED</th></template>
        <template v-else><th>{{ t('Status') }}</th><th>{{ t('Amount') }} AED</th></template></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.id"><td><RouterLink :to="'/sales/' + row.id">{{ row.number }} · {{ label(row.kind) }}</RouterLink></td>
          <td>{{ row.customer }}</td><td>{{ siteName(row.site_id) }}</td>
          <template v-if="tab === 'AR aging'"><td>{{ row.due }}</td><td>{{ t(row.bucket ?? '') }}</td><td>{{ row.outstanding }}</td></template>
          <template v-else><td>{{ t(row.status) }}</td><td>{{ row.amount }}</td></template></tr></tbody></table>
      <p v-else class="empty">{{ t('No sales records in this view.') }}</p>
    </section>
    <button v-if="!id && tab === 'Sales documents' && next" class="secondary" :disabled="busy" @click="run(() => loadDocs(next!))">{{ t('Load more') }}</button>

</template>
