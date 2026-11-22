<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPaging,ref as pagingRef} from 'vue'
const pg=pagingRef(1),ps=pagingRef(25),term=pagingRef(''),total=pagingRef(0)
function queryPage(){return new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}).toString()}

import SavedViews from './SavedViews.vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, createRequest, type Workspace } from './api'
import { message, t } from './i18n'
import DocumentTools from './DocumentTools.vue'

type Named = {id: number; name: string; code: string}
type Catalog = {units: Named[]; items: (Named & {active: boolean; base_unit_id: number})[];
  warehouses: (Named & {site_id: number; active:boolean})[]; locations: (Named & {warehouse_id: number})[];
  sites: {id: number; name: string}[]; settings: {site_id: number; opening_closed_at: string | null}[];
  conversions: {id: number; item_id: number | null; unit_id: number; base_unit_id: number; factor: string}[]}
type Line = {order_line_id: number; item_id?: number; unit_id?: number; location_id?: number; quantity: string;
  description?: string; movement_id?: number; base_quantity?: string; amount?: string}
type Receipt = {id: number; number: string; order_id: number; order_number: string; site_id: number; warehouse_id: number;
  lines: Line[]; status: string; revision: number; archived: boolean; supplier_reference: string;
  confirmed_at: string | null; confirmed_by: string}
type Progress = {order_id: number; order_number: string; site_id: number; currency: string; order_status: string;
  receipt_status: string; lines: {id: number; description: string; received: string; remaining: string;
  item_id: number | null; unit_id: number | null}[]; receipts: Pick<Receipt, 'id' | 'number' | 'status'>[]}
type Movement = {id: number; site_id: number; item_id: number; location_id: number; quantity: string; value: string; kind: string;
  receipt_id: number | null; original_id: number | null; business_date: string; actor: string; occurred_at: string}
type Position = {item_id: number; location_id: number; quantity: string; on_hand: string; reserved: string; available: string; site_average: string; warehouse_id: number; site_id: number}
const { workspace } = defineProps<{workspace: Workspace}>()
const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id) || 0)
const screen = computed(() => String(route.name))
const catalog = ref<Catalog>()
const current = ref<Receipt>()
const progress = ref<Progress>()
const rows = ref<Receipt[]>([])
const balances = ref<Position[]>([])
const movements = ref<Movement[]>([])
const cursor = ref<number | null>(null)
const tab = computed(() => String(route.params.section || 'receipts'))
const archived = ref(false)
const warehouse = ref<number>()
const reference = ref('')
const lines = ref<Line[]>([])
const error = ref('')
const notice = ref('')
const busy = ref(false)
const loading = ref(false)
let sequence = 0
let submit = createRequest()
let confirm = createRequest()
let reverse = createRequest()
let postOpening = createRequest()
const scoped = (permission: string, site?: number | null) => allows(workspace, 'inventory.' + permission, site)
const locations = computed(() => catalog.value?.locations.filter(row => row.warehouse_id === warehouse.value) ?? [])
const siteId = computed(() => current.value?.site_id ?? progress.value?.site_id)
const editable = computed(() => siteId.value !== undefined && scoped('receive', siteId.value)
  && (!current.value || current.value.status === 'draft' && !current.value.archived))
const dirty = computed(() => !!current.value && (JSON.stringify(current.value.lines) !== JSON.stringify(lines.value)
  || current.value.warehouse_id !== warehouse.value || current.value.supplier_reference !== reference.value))
const companySetup = computed(() => scoped('setup', null))
const mode = ref('warehouse')
const setupOptions = computed(() => [
  ...(companySetup.value ? [
    {id: 'conversion', label: 'Unit conversion'},
  ] : []),
  {id: 'warehouse', label: 'Warehouse'}, {id: 'location', label: 'Location'}, {id: 'site', label: 'Business day'},
])
const showUnitForm = ref(false)
const unitForm = ref({code: '', name: ''})
const setup = ref<Record<string, string | number | boolean | null | undefined>>({})
const openingSite = ref<number>()
const openingLines = ref<{item_id?: number; location_id?: number; quantity: string; unit_cost: string}[]>([])
const openingClosed = computed(() => catalog.value?.settings.some(row => row.site_id === openingSite.value && row.opening_closed_at))
const reversal = ref<{id: number; kind: string; quantity: string; reason: string}>()
const name = (list: Named[] | undefined, key?: number) => list?.find(row => row.id === key)?.name ?? '—'
function locationName(key?: number) {
  const row = catalog.value?.locations.find(item => item.id === key)
  return row ? name(catalog.value?.warehouses, row.warehouse_id) + ' / ' + row.name : '—'
}
function addLine() { lines.value.push({order_line_id: 0, quantity: ''}) }
function bind(line: Line) {
  const source = progress.value?.lines.find(row => row.id === line.order_line_id)
  line.item_id = source?.item_id ?? undefined
  line.unit_id = source?.unit_id ?? undefined
}
function chooseItem(line: Line) {
  line.unit_id = catalog.value?.items.find(row => row.id === line.item_id)?.base_unit_id
}
function apply(row: Receipt) {
  current.value = row
  warehouse.value = row.warehouse_id
  reference.value = row.supplier_reference
  lines.value = row.lines.map(line => ({...line}))
}
async function load() {
  const token = ++sequence
  loading.value = true
  error.value = ''
  notice.value = ''
  current.value = undefined
  progress.value = undefined
  reversal.value = undefined
  cursor.value = null
  submit = createRequest()
  confirm = createRequest()
  reverse = createRequest()
  try {
    const data = await api<Catalog>('/inventory/catalog')
    if (token !== sequence) return
    catalog.value = data
    if (screen.value === 'receipt') {
      const row = await api<Receipt>('/inventory/receipts/' + id.value)
      if (token !== sequence) return
      apply(row)
      if (row.status === 'draft' && editable.value) {
        const result = await api<Progress>('/inventory/orders/' + row.order_id)
        if (token === sequence) progress.value = result
      }
    } else if (screen.value === 'receive') {
      const result = await api<Progress>('/inventory/orders/' + id.value)
      if (token !== sequence) return
      progress.value = result
      warehouse.value = undefined
      reference.value = ''
      lines.value = []
      addLine()
    } else if (tab.value === 'receipts') {
      const result=await api<{items:Receipt[];count:number}>('/inventory/receipts?'+queryPage()+ (archived.value?'&archived=1':''));
      if(token===sequence){rows.value=result.items;total.value=result.count;cursor.value=null}
    } else if (tab.value === 'balances') {
      const result=await api<{items:Position[];count:number}>('/inventory/balances?'+queryPage());
      if(token===sequence){balances.value=result.items;total.value=result.count;cursor.value=null}
    } else if (tab.value === 'movements') {
      const result=await api<{items:Movement[];count:number}>('/inventory/movements?'+queryPage());
      if(token===sequence){movements.value=result.items;total.value=result.count;cursor.value=null}
    }
  } catch (failure) { if (token === sequence) error.value = message(failure) }
  finally { if (token === sequence) loading.value = false }
}
watchPaging([pg,ps,term],load)
watch([screen, id, tab, archived], load, {immediate: true})
watch(mode, () => { setup.value = {} })
watch(openingSite, () => { openingLines.value = []; postOpening = createRequest() })

async function run(task: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try { await task() } catch (failure) { error.value = message(failure) } finally { busy.value = false }
}
function save() {
  void run(async () => {
    const payload = { warehouse_id: warehouse.value, supplier_reference: reference.value, lines: lines.value }
    if (current.value) {
      apply(await api<Receipt>('/inventory/receipts/' + current.value.id, 'PATCH', {...payload, revision: current.value.revision}))
      notice.value = 'Changes saved.'
    } else if (progress.value) {
      const row = await submit<Receipt>('/inventory/receipts', {...payload, order_id: progress.value.order_id})
      await router.push('/inventory/receipts/' + row.id)
    }
  })
}
function confirmReceipt() {
  void run(async () => {
    if (!current.value) return
    apply(await confirm<Receipt>('/inventory/receipts/' + current.value.id + '/confirm', {revision: current.value.revision}))
    notice.value = 'Receipt confirmed.'
  })
}
function saveUnit() {
  void run(async () => {
    const row = await api<{id: number; code: string}>('/inventory/setup/unit', 'POST', unitForm.value)
    catalog.value?.units.push({...row, name: unitForm.value.name.trim()})
    catalog.value?.units.sort((a, b) => a.code.localeCompare(b.code))
    if (mode.value === 'item' && !setup.value.base_unit_id) setup.value.base_unit_id = row.id
    showUnitForm.value = false
    unitForm.value = {code: '', name: ''}
    notice.value = 'Unit of measure added.'
  })
}
function saveSetup() {
  void run(async () => {
    const payload = {...setup.value}
    if (mode.value === 'item-status') payload.active = payload.active ?? false
    await api('/inventory/setup/' + mode.value, 'POST', payload)
    setup.value = {}
    await load()
    notice.value = 'Changes saved.'
  })
}
function opening(close: boolean) {
  void run(async () => {
    await postOpening('/inventory/opening', {site_id: openingSite.value, lines: openingLines.value, close})
    openingLines.value = []
    await load()
    notice.value = close ? 'Opening balances closed.' : 'Opening balances saved.'
  })
}
function saveReverse() {
  void run(async () => {
    if (!reversal.value) return
    const {id: key, ...payload} = reversal.value
    await reverse('/inventory/movements/' + key + '/reverse', payload)
    await load()
    notice.value = 'Stock movement recorded.'
  })
}
function more() {
  void run(async () => {
    const result = await api<{items: Movement[]; next_before: number | null}>('/inventory/movements?before=' + cursor.value)
    movements.value.push(...result.items)
    cursor.value = result.next_before
  })
}
const openingLocations = computed(() => catalog.value?.locations.filter(row =>
  catalog.value?.warehouses.some(w => w.id === row.warehouse_id && w.active && w.site_id === openingSite.value)) ?? [])
const visibleSetup = computed(() => catalog.value?.sites.filter(row => scoped('setup', row.id)) ?? [])
const tabs = computed(() => [
  {id: 'receipts', label: 'Goods receipts'}, {id: 'items', label: 'Inventory items'}, {id: 'balances', label: 'Stock balances'}, {id: 'movements', label: 'Stock movements'},
  ...(scoped('opening') ? [{id: 'opening', label: 'Opening balance'}] : []),
  ...(scoped('setup') ? [{id: 'setup', label: 'Inventory setup'}] : []),
])
async function warehouseState(id:number, active:boolean){
  if(busy.value)return
  busy.value=true;error.value=''
  try{
    await api('/inventory/warehouses/'+id+'/state','PATCH',{active})
    const item=catalog.value?.warehouses.find(row=>row.id===id)
    if(item)item.active=active
  }catch(failure){error.value=message(failure)}finally{busy.value=false}
}
</script>

<template>
  <RouterLink v-if="id" class="back-link" to="/inventory">{{ t('Back to inventory') }}</RouterLink>
  <h1>{{ current?.number ?? t(id ? 'Create goods receipt' : 'Inventory') }}</h1>
  <nav v-if="!id" class="section-tabs" :aria-label="t('Inventory')">
    <button v-for="page in tabs" :key="page.id" class="tab-link" :aria-current="tab === page.id ? 'page' : undefined" @click="router.push('/inventory/' + page.id)">{{ t(page.label) }}</button>
  </nav>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <p v-if="loading">{{ t('Loading') }}…</p>
  <template v-else-if="id && (current || progress)">
    <p><RouterLink :to="'/purchase-orders/' + (current?.order_id ?? progress?.order_id)">{{ current?.order_number ?? progress?.order_number }}</RouterLink>
      <span v-if="current"> · {{ t(current.archived ? 'Archived' : current.status === 'confirmed' ? 'Confirmed' : 'Draft') }}</span>
    </p>
    <form v-validated-form v-if="editable && progress" class="panel record-form purchase-form" @submit.prevent="save">
      <div class="field-wrap"><label>{{ t('Warehouse') }}<select data-field="warehouse_id" v-model="warehouse" required @change="lines.forEach(line => line.location_id = undefined)">
        <option v-for="row in catalog?.warehouses.filter(w => w.active && w.site_id === siteId)" :key="row.id" :value="row.id">{{ row.name }}</option>
      </select></label><FieldHint :label="t('Warehouse')" required /></div>
      <div class="field-wrap"><label>{{ t('Supplier reference') }}<input data-field="supplier_reference" :placeholder="t('e.g., REF-001')" v-model="reference" maxlength="120"></label><FieldHint :label="t('Supplier reference')" /></div>
      <fieldset v-for="(line, index) in lines" :key="index" class="purchase-line">
        <legend>{{ t('Line item') }} {{ index + 1 }}</legend>
        <div class="field-wrap"><label>{{ t('Purchase order line') }}<select :data-field="'lines.' + index + '.order_line_id'" v-model="line.order_line_id" required @change="bind(line)">
          <option disabled :value="0">{{ t('Select line') }}</option>
          <option v-for="row in progress.lines" :key="row.id" :value="row.id">{{ row.id }} · {{ row.description }} · {{ t('Remaining') }} {{ row.remaining }}</option>
        </select></label><FieldHint :label="t('Purchase order line')" required /></div>
        <EntityAutocomplete :data-field="'lines.' + index + '.item_id'" :label="t('Item')" lookup="items" :choices="(catalog?.items ?? []).filter(item => item.active || item.id === line.item_id)" :disabled="!!progress.lines.find(row => row.id === line.order_line_id)?.item_id" :empty-value="undefined" required v-model="line.item_id" @select="chooseItem(line)" />
        <div class="field-wrap"><label>{{ t('Purchase unit') }}<select :data-field="'lines.' + index + '.unit_id'" v-model="line.unit_id" required :disabled="!!progress.lines.find(row => row.id === line.order_line_id)?.unit_id">
          <option v-for="row in catalog?.units" :key="row.id" :value="row.id">{{ row.code }} · {{ row.name }}</option>
        </select></label><FieldHint :label="t('Purchase unit')" required /></div>
        <EntityAutocomplete :data-field="'lines.' + index + '.location_id'" :label="t('Location')" lookup="locations" :params="{ warehouse_id: warehouse }" :choices="locations" :empty-value="undefined" required v-model="line.location_id" />
        <div class="field-wrap"><label>{{ t('Quantity') }}<input :data-field="'lines.' + index + '.quantity'" :placeholder="t('e.g., 2.500')" v-model="line.quantity" inputmode="decimal" required></label><FieldHint :label="t('Quantity')" required /></div>
        <button v-if="lines.length > 1" type="button" class="secondary" @click="lines.splice(index, 1)">{{ t('Remove item') }}</button>
      </fieldset>
      <div class="actions">
        <button type="button" class="secondary" :disabled="lines.length >= 100" @click="addLine">{{ t('Add item') }}</button>
        <button class="primary" :disabled="busy">{{ t('Save draft') }}</button>
      </div>
    </form>
    <section v-else-if="current" class="panel table-panel">
      <p class="empty">{{ t('Warehouse') }} · {{ name(catalog?.warehouses, current.warehouse_id) }} <span v-if="current.supplier_reference"> · {{ current.supplier_reference }}</span></p>
      <table><thead><tr><th>{{ t('Item') }}</th><th>{{ t('Location') }}</th><th>{{ t('Quantity') }}</th><th>{{ t('Base quantity') }}</th><th>{{ t('Amount') }}</th></tr></thead>
        <tbody><tr v-for="line in current.lines" :key="line.order_line_id + '-' + line.location_id">
          <td>{{ name(catalog?.items, line.item_id) }}</td><td>{{ locationName(line.location_id) }}</td>
          <td>{{ line.quantity }} {{ name(catalog?.units, line.unit_id) }}</td><td>{{ line.base_quantity ?? '—' }}</td><td>{{ line.amount ?? '—' }}</td>
        </tr></tbody></table>
      <p v-if="current.confirmed_at" class="empty">{{ t('Confirmed') }} · {{ current.confirmed_by }} · {{ new Date(current.confirmed_at).toLocaleString() }}</p>
    </section>
    <button v-if="current && current.status === 'draft' && !current.archived && scoped('confirm', current.site_id)" class="primary"
      :disabled="busy || dirty" @click="confirmReceipt">{{ t('Confirm receipt') }}</button>
    <DocumentTools v-if="current" :key="current.id" kind="grn" :id="current.id" :revision="current.revision" :archived="current.archived"
      :editable="scoped('receive', current.site_id) && current.status === 'draft' && !dirty && !busy" @changed="load" />
  </template>
  <template v-else-if="!id && catalog">
    <RouterLink v-if="tab === 'setup'" class="button secondary" to="/inventory/items">{{ t('Inventory items') }}</RouterLink>
    <template v-if="tab === 'receipts'">
      <SavedViews :workspace="workspace" screen="goods-receipts" :filters="{archived: archived ? '1' : ''}"
        @apply="archived = $event.archived === '1'" />
      <button class="secondary" :aria-pressed="archived" @click="archived = !archived">{{ t(archived ? 'Goods receipts' : 'Archived drafts') }}</button>
      <section class="panel table-panel">
        <TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table v-if="rows.length"><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Purchase order') }}</th><th>{{ t('Warehouse') }}</th><th>{{ t('Status') }}</th></tr></thead>
          <tbody><tr v-for="row in rows" :key="row.id"><td><RouterLink :to="'/inventory/receipts/' + row.id">{{ row.number }}</RouterLink></td>
            <td>{{ row.order_number }}</td><td>{{ name(catalog.warehouses, row.warehouse_id) }}</td>
            <td>{{ t(row.archived ? 'Archived' : row.status === 'confirmed' ? 'Confirmed' : 'Draft') }}</td></tr></tbody></table>
        <p v-else class="empty">{{ t('No goods receipts yet.') }}</p>
      </section>
    </template>
    <section v-else-if="tab === 'balances'" class="panel table-panel">
      <TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table v-if="balances.length"><thead><tr><th>{{ t('Site') }}</th><th>{{ t('Item') }}</th><th>{{ t('Location') }}</th><th>{{ t('On hand') }}</th><th>{{ t('Reserved') }}</th><th>{{ t('Available') }}</th><th>{{ t('Site average cost (AED)') }}</th></tr></thead>
        <tbody><tr v-for="row in balances" :key="row.item_id + '-' + row.location_id">
          <td>{{ catalog.sites.find(site => site.id === row.site_id)?.name }}</td><td>{{ name(catalog.items, row.item_id) }}</td><td>{{ locationName(row.location_id) }}</td>
          <td>{{ row.on_hand }} {{ name(catalog.units, catalog.items.find(item => item.id === row.item_id)?.base_unit_id) }}</td>
          <td><RouterLink v-if="Number(row.reserved)>0 && workspace.modules.some(module=>module.id==='supply'&&module.enabled) && allows(workspace,'supply.view',row.site_id)"
                          :to="{path:'/supply/reservations',query:{site:row.site_id,item:row.item_id,location:row.location_id}}">{{ row.reserved }}</RouterLink><span v-else>{{ row.reserved }}</span></td>
          <td>{{ row.available }}</td><td>{{ row.site_average }}</td>
        </tr></tbody></table>
      <p v-else class="empty">{{ t('No stock balances yet.') }}</p>
    </section>
    <template v-else-if="tab === 'movements'">
      <form v-validated-form v-if="reversal" class="panel record-form" @submit.prevent="saveReverse">
        <h2>{{ t(reversal.kind === 'return' ? 'Return to supplier' : 'Correct receipt') }} #{{ reversal.id }}</h2>
        <div class="field-wrap"><label>{{ t('Quantity in purchase unit') }}<input data-field="quantity" :placeholder="t('e.g., 2.500')" v-model="reversal.quantity" required inputmode="decimal"></label><FieldHint :label="t('Quantity in purchase unit')" required /></div>
        <div class="field-wrap"><label>{{ t('Reason') }}<textarea data-field="reason" :placeholder="t('Explain the reason')" v-model="reversal.reason" required maxlength="1000"></textarea></label><FieldHint :label="t('Reason')" required /></div>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Save') }}</button><button type="button" class="secondary" @click="reversal = undefined">{{ t('Cancel') }}</button></div>
      </form>
      <section class="panel table-panel">
        <TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><table v-if="movements.length"><thead><tr><th>{{ t('Movement') }}</th><th>{{ t('Item') }}</th><th>{{ t('Location') }}</th><th>{{ t('Base quantity') }}</th>
          <th>{{ t('Amount') }} AED</th><th>{{ t('Business date') }}</th><th>{{ t('Actor') }}</th><th>{{ t('Actions') }}</th></tr></thead>
          <tbody><tr v-for="row in movements" :key="row.id"><td>#{{ row.id }} · {{ t(row.kind) }}
            <RouterLink v-if="row.receipt_id" :to="'/inventory/receipts/' + row.receipt_id">GRN</RouterLink><span v-if="row.original_id"> · #{{ row.original_id }}</span></td>
            <td>{{ name(catalog.items, row.item_id) }}</td><td>{{ locationName(row.location_id) }}</td><td>{{ row.quantity }}</td><td>{{ row.value }}</td>
            <td>{{ row.business_date }}<br>{{ new Date(row.occurred_at).toLocaleString() }}</td><td>{{ row.actor }}</td>
            <td v-if="row.kind === 'receipt'"><button v-if="scoped('correct', row.site_id)" class="secondary" @click="reversal = {id: row.id, kind: 'correction', quantity: '', reason: ''}">{{ t('Correct receipt') }}</button>
              <button v-if="scoped('return', row.site_id)" class="secondary" @click="reversal = {id: row.id, kind: 'return', quantity: '', reason: ''}">{{ t('Return to supplier') }}</button></td><td v-else></td>
          </tr></tbody></table>
        <p v-else class="empty">{{ t('No stock movements yet.') }}</p>
      </section>
      <button v-if="cursor" class="secondary" :disabled="busy" @click="more">{{ t('Load more') }}</button>
    </template>
    <form v-validated-form v-else-if="tab === 'opening' && scoped('opening')" class="panel record-form purchase-form" @submit.prevent="opening(false)">
      <h2>{{ t('Opening balance') }}</h2>
      <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site_id" v-model="openingSite" required><option v-for="row in catalog.sites.filter(site => scoped('opening', site.id))" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><FieldHint :label="t('Site')" required /></div>
      <p v-if="openingClosed">{{ t('Opening balances are closed for this site.') }}</p>
      <template v-else-if="openingSite">
        <fieldset v-for="(line, index) in openingLines" :key="index" class="purchase-line">
          <legend>{{ t('Line item') }} {{ index + 1 }}</legend>
          <EntityAutocomplete :data-field="'lines.' + index + '.item_id'" :label="t('Item')" lookup="items" :choices="(catalog?.items ?? []).filter(item => item.active).map(row => ({ ...row, subtitle: name(catalog?.units ?? [], row.base_unit_id) }))" :empty-value="undefined" required v-model="line.item_id" />
          <EntityAutocomplete :data-field="'lines.' + index + '.location_id'" :label="t('Location')" lookup="locations" :params="{ site_id: openingSite }" :choices="openingLocations.map(row => ({ id: row.id, name: locationName(row.id) }))" :empty-value="undefined" required v-model="line.location_id" />
          <div class="field-wrap"><label>{{ t('Base quantity') }}<input :data-field="'lines.' + index + '.quantity'" :placeholder="t('e.g., 2.500')" v-model="line.quantity" required inputmode="decimal"></label><FieldHint :label="t('Base quantity')" required /></div>
          <div class="field-wrap"><label>{{ t('Unit cost (AED)') }}<input :data-field="'lines.' + index + '.unit_cost'" :placeholder="t('e.g., 125.50')" v-model="line.unit_cost" required inputmode="decimal"></label><FieldHint :label="t('Unit cost (AED)')" required /></div>
          <button type="button" class="secondary" @click="openingLines.splice(index, 1)">{{ t('Remove item') }}</button>
        </fieldset>
        <div class="actions">
          <button type="button" class="secondary" :disabled="busy || openingLines.length >= 100" @click="openingLines.push({quantity: '', unit_cost: ''})">{{ t('Add item') }}</button>
          <button v-if="openingLines.length" class="primary" :disabled="busy">{{ t('Save opening balances') }}</button>
          <button v-else type="button" class="secondary" :disabled="busy" @click="opening(true)">{{ t('Close opening balances') }}</button>
        </div>
      </template>
    </form>
    <template v-else-if="tab === 'setup' && scoped('setup')">
      <div class="actions">
        <span>{{ t('Units') }}</span><CsvExport :workspace="workspace" screen="unit" />
        <span>{{ t('Warehouses') }}</span><CsvExport :workspace="workspace" screen="warehouse" />
      </div>
      <div class="actions approval-section">
        <div v-for="row in catalog.warehouses.filter(w => scoped('setup', w.site_id))" :key="row.id" class="actions">
          <span>{{row.name}} · {{t(row.active?'Active':'Inactive')}}</span>
          <button type="button" class="secondary" :disabled="busy" @click="warehouseState(row.id,!row.active)">{{t(row.active?'Deactivate warehouse':'Reactivate warehouse')}}</button>
        </div>
        <span>{{ t('Locations') }}</span><CsvExport :workspace="workspace" screen="location" />
      </div>
      <section v-if="companySetup" class="panel record-form">
        <div class="page-heading">
          <h2>{{ t('Units of measure') }}</h2>
          <button v-if="!showUnitForm" type="button" class="secondary" @click="showUnitForm = true">{{ t('Add unit of measure') }}</button>
        </div>
        <form v-validated-form v-if="showUnitForm" @submit.prevent="saveUnit">
          <div class="field-wrap"><label>{{ t('Unit code') }}<input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="unitForm.code" required maxlength="24"></label><FieldHint :label="t('Unit code')" required /></div>
          <div class="field-wrap"><label>{{ t('Unit name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="unitForm.name" required maxlength="80"></label><FieldHint :label="t('Unit name')" required /></div>
          <div class="actions">
            <button class="primary" :disabled="busy">{{ t('Save unit') }}</button>
            <button type="button" class="secondary" :disabled="busy" @click="showUnitForm = false">{{ t('Cancel') }}</button>
          </div>
        </form>
        <ul v-if="catalog.units.length">
          <li v-for="unit in catalog.units" :key="unit.id"><bdi>{{ unit.code }}</bdi> · {{ t(unit.name) }}</li>
        </ul>
        <p v-else>{{ t('Add a unit of measure before creating an item.') }}</p>
      </section>
      <form v-validated-form class="panel record-form" @submit.prevent="saveSetup">
        <h2>{{ t('Inventory setup') }}</h2>
        <div class="field-wrap"><label>{{ t('Setup type') }}<select data-field="mode" v-model="mode">
          <option v-for="option in setupOptions" :key="option.id" :value="option.id">{{ t(option.label) }}</option>
        </select></label><FieldHint :label="t('Setup type')" /></div>
        <template v-if="['item', 'warehouse', 'location'].includes(mode)">
          <div class="field-wrap"><label>{{ t('Code') }}<input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="setup.code" required maxlength="32"></label><FieldHint :label="t('Code')" required /></div>
          <div class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="setup.name" required maxlength="120"></label><FieldHint :label="t('Name')" required /></div>
        </template>
        <div class="field-wrap" v-if="mode === 'warehouse' || mode === 'site'"><label>{{ t('Site') }}<select data-field="site_id" v-model="setup.site_id" required>
          <option v-for="row in visibleSetup" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><FieldHint :label="t('Site')" required /></div>
        <div class="field-wrap" v-if="mode === 'location'"><label>{{ t('Warehouse') }}<select data-field="warehouse_id" v-model="setup.warehouse_id" required>
          <option v-for="row in catalog.warehouses.filter(w => w.active && scoped('setup', w.site_id))" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><FieldHint :label="t('Warehouse')" required /></div>
        <div class="field-wrap" v-if="mode === 'conversion'"><label>{{ t('Item') }}<select data-field="item_id" v-model="setup.item_id">
          <option v-if="mode === 'conversion'" :value="null">{{ t('General conversion') }}</option>
          <option v-for="row in catalog.items" :key="row.id" :value="row.id">{{ row.code }} · {{ row.name }}</option></select></label><FieldHint :label="t('Item')" /></div>
        <div class="field-wrap" v-if="mode === 'conversion' && !setup.item_id"><label>{{ t('Base unit') }}<select data-field="base_unit_id" v-model="setup.base_unit_id" required>
          <option v-for="row in catalog.units" :key="row.id" :value="row.id">{{ row.code }} · {{ t(row.name) }}</option></select></label><FieldHint :label="t('Base unit')" required /></div>
        <template v-if="mode === 'conversion'">
          <div class="field-wrap"><label>{{ t('From unit') }}<select data-field="unit_id" v-model="setup.unit_id" required><option v-for="row in catalog.units" :key="row.id" :value="row.id">{{ row.name }}</option></select></label><FieldHint :label="t('From unit')" required /></div>
          <div class="field-wrap"><label>{{ t('Conversion factor') }}<input data-field="factor" :placeholder="t('e.g., 125.50')" v-model="setup.factor" required inputmode="decimal"></label><FieldHint :label="t('Conversion factor')" required /></div>
        </template>
        <template v-if="mode === 'site'">
          <div class="field-wrap"><label>{{ t('Time zone') }}<input data-field="timezone" v-model="setup.timezone" placeholder="Asia/Dubai" required></label><FieldHint :label="t('Time zone')" required /></div>
          <div class="field-wrap"><label>{{ t('Business day starts at') }}<input data-field="day_start" v-model="setup.day_start" type="time" required></label><FieldHint :label="t('Business day starts at')" required /></div>
        </template>
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
      </form>
    </template>
  </template>
</template>
