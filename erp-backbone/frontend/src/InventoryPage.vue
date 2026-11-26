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
import { notify } from './useUnifiedNotice'
import DocumentTools from './DocumentTools.vue'

type Named = {id: number; name: string; code: string}
type Catalog = {units: Named[]; items: (Named & {active: boolean; base_unit_id: number})[];
  warehouses: (Named & {site_id: number; active:boolean})[]; locations: (Named & {warehouse_id: number; active?: boolean})[];
  sites: {id: number; name: string}[]; settings: {site_id: number; timezone: string; day_start: string; opening_closed_at: string | null}[];
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
    {id: 'conversion', label: t('Unit conversion')},
  ] : []),
  {id: 'warehouse', label: t('Warehouse')}, {id: 'location', label: t('Location')}, {id: 'site', label: t('Business day')},
])
const showUnitForm = ref(false)
const showSetupForm = ref(false)
const editingUnitId = ref<number | null>(null)
const setupTab = ref<'warehouses' | 'units' | 'warehouse' | 'location' | 'conversion' | 'site'>('warehouses')
const overviewSubTab = ref<'warehouses' | 'locations'>('warehouses')
const locationStatus = ref<'all' | 'active' | 'inactive'>('all')
const setupViewMode = ref<'list' | 'grid'>('list')
const setupSearch = ref('')
const setupStatus = ref<'all' | 'active' | 'inactive'>('all')
const setupWarehouseFilter = ref<number | null>(null)
const setupPage = ref(1)
const setupSize = ref(25)

function closeSetupForm(target = setupTab.value) {
  setup.value = {}
  showSetupForm.value = false
  if (['warehouse', 'location', 'conversion', 'site'].includes(target)) {
    mode.value = target
  }
}

function openNewSetupForm(target: 'warehouse' | 'location' | 'conversion' | 'site') {
  setupTab.value = target
  mode.value = target
  setup.value = {}
  if (target === 'site') {
    setup.value.timezone = 'Asia/Dubai'
    setup.value.day_start = '00:00'
  }
  showSetupForm.value = true
}

function openSetupTab(target: 'warehouses' | 'units' | 'warehouse' | 'location' | 'conversion' | 'site') {
  setupTab.value = target
  setupSearch.value = ''
  setupStatus.value = 'all'
  locationStatus.value = 'all'
  setupWarehouseFilter.value = null
  setupPage.value = 1
  showUnitForm.value = false
  editingUnitId.value = null
  closeSetupForm(target)
}

function editWarehouse(row: {id: number; code: string; name: string; site_id: number}) {
  setupTab.value = 'warehouse'
  mode.value = 'warehouse'
  setup.value = { id: row.id, code: row.code, name: row.name, site_id: row.site_id }
  showSetupForm.value = true
}

function editLocation(row: {id: number; code: string; name: string; warehouse_id: number}) {
  setupTab.value = 'location'
  mode.value = 'location'
  setup.value = { id: row.id, code: row.code, name: row.name, warehouse_id: row.warehouse_id }
  showSetupForm.value = true
}

function editUnit(row: {id: number; code: string; name: string}) {
  setupTab.value = 'units'
  editingUnitId.value = row.id
  unitForm.value = { code: row.code, name: row.name }
  showUnitForm.value = true
}

function editConversion(row: {id: number; item_id: number | null; unit_id: number; base_unit_id: number; factor: string}) {
  setupTab.value = 'conversion'
  mode.value = 'conversion'
  setup.value = { id: row.id, item_id: row.item_id, unit_id: row.unit_id, base_unit_id: row.base_unit_id, factor: row.factor }
  showSetupForm.value = true
}

function editSiteSetting(row: {site_id: number; timezone: string; day_start: string}) {
  setupTab.value = 'site'
  mode.value = 'site'
  setup.value = { site_id: row.site_id, timezone: row.timezone || 'Asia/Dubai', day_start: (row.day_start || '00:00').slice(0, 5) }
  showSetupForm.value = true
}

const allowedWarehouses = computed(() =>
  (catalog.value?.warehouses || []).filter(w => scoped('setup', w.site_id))
)

const filteredWarehouses = computed(() => {
  const q = setupSearch.value.trim().toLowerCase()
  return allowedWarehouses.value.filter(w => {
    if (setupStatus.value === 'active' && !w.active) return false
    if (setupStatus.value === 'inactive' && w.active) return false
    if (!q) return true
    const sName = name(catalog.value?.sites as any, w.site_id)
    return `${w.code || ''} ${w.name} ${sName}`.toLowerCase().includes(q)
  })
})
const pagedWarehouses = computed(() => {
  const start = (setupPage.value - 1) * setupSize.value
  return filteredWarehouses.value.slice(start, start + setupSize.value)
})

const filteredLocations = computed(() => {
  const q = setupSearch.value.trim().toLowerCase()
  const whIds = new Set(allowedWarehouses.value.map(w => w.id))
  return (catalog.value?.locations || []).filter(loc => {
    if (!whIds.has(loc.warehouse_id)) return false
    if (locationStatus.value === 'active' && loc.active === false) return false
    if (locationStatus.value === 'inactive' && loc.active !== false) return false
    if (setupWarehouseFilter.value && loc.warehouse_id !== setupWarehouseFilter.value) return false
    if (!q) return true
    const wName = name(catalog.value?.warehouses, loc.warehouse_id)
    return `${loc.code || ''} ${loc.name} ${wName}`.toLowerCase().includes(q)
  })
})
const pagedLocations = computed(() => {
  const start = (setupPage.value - 1) * setupSize.value
  return filteredLocations.value.slice(start, start + setupSize.value)
})

const filteredUnits = computed(() => {
  const q = setupSearch.value.trim().toLowerCase()
  return (catalog.value?.units || []).filter(u => {
    if (!q) return true
    return `${u.code} ${u.name} ${t(u.name)}`.toLowerCase().includes(q)
  })
})
const pagedUnits = computed(() => {
  const start = (setupPage.value - 1) * setupSize.value
  return filteredUnits.value.slice(start, start + setupSize.value)
})

const filteredConversions = computed(() => {
  const q = setupSearch.value.trim().toLowerCase()
  return (catalog.value?.conversions || []).filter(c => {
    if (!q) return true
    const itemObj = (catalog.value?.items || []).find(i => i.id === c.item_id)
    const itemLabel = itemObj ? `${itemObj.code} ${itemObj.name}` : t('General conversion')
    const baseLabel = name(catalog.value?.units, c.base_unit_id)
    const unitLabel = name(catalog.value?.units, c.unit_id)
    return `${itemLabel} ${baseLabel} ${unitLabel} ${c.factor}`.toLowerCase().includes(q)
  })
})
const pagedConversions = computed(() => {
  const start = (setupPage.value - 1) * setupSize.value
  return filteredConversions.value.slice(start, start + setupSize.value)
})

const filteredSiteSettings = computed(() => {
  const q = setupSearch.value.trim().toLowerCase()
  return visibleSetup.value.map(s => {
    const st = (catalog.value?.settings || []).find(x => x.site_id === s.id)
    return {
      site_id: s.id,
      site_name: s.name,
      timezone: st?.timezone || 'Asia/Dubai',
      day_start: (st?.day_start || '00:00').slice(0, 5),
    }
  }).filter(row => {
    if (!q) return true
    return `${row.site_name} ${row.timezone} ${row.day_start}`.toLowerCase().includes(q)
  })
})
const pagedSiteSettings = computed(() => {
  const start = (setupPage.value - 1) * setupSize.value
  return filteredSiteSettings.value.slice(start, start + setupSize.value)
})

const siteChoices = computed(() =>
  visibleSetup.value.map(s => ({ id: s.id, name: s.name }))
)
const activeWarehouseChoices = computed(() =>
  allowedWarehouses.value.filter(w => w.active).map(w => ({ id: w.id, code: w.code, name: w.name }))
)
const warehouseFilterChoices = computed(() => [
  { id: null, code: 'ALL', name: t('All') },
  ...allowedWarehouses.value.map(w => ({ id: w.id, code: w.code, name: w.name })),
])
const unitChoices = computed(() =>
  (catalog.value?.units || []).map(u => ({ id: u.id, code: u.code, name: t(u.name) }))
)
const itemChoicesWithGeneral = computed(() => [
  { id: null, code: 'ALL', name: t('General conversion') },
  ...(catalog.value?.items || []).map(i => ({ id: i.id, code: i.code, name: i.name })),
])
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
      notify('saved', 'Goods receipts', 'Changes saved.')
    } else if (progress.value) {
      const row = await submit<Receipt>('/inventory/receipts', {...payload, order_id: progress.value.order_id})
      notify('saved', 'Goods receipts', 'Changes saved.')
      await router.push('/inventory/receipts/' + row.id)
    }
  })
}
function confirmReceipt() {
  void run(async () => {
    if (!current.value) return
    apply(await confirm<Receipt>('/inventory/receipts/' + current.value.id + '/confirm', {revision: current.value.revision}))
    notice.value = 'Receipt confirmed.'
    notify('confirmation', 'Goods receipts', 'Receipt confirmed.')
  })
}
function saveUnit() {
  void run(async () => {
    const payload = editingUnitId.value ? { id: editingUnitId.value, ...unitForm.value } : unitForm.value
    await api<{id: number; code: string}>('/inventory/setup/unit', 'POST', payload)
    await load()
    showUnitForm.value = false
    editingUnitId.value = null
    unitForm.value = {code: '', name: ''}
    notice.value = 'Changes saved.'
    notify('saved', 'Inventory setup', 'Changes saved.')
  })
}
function saveSetup() {
  void run(async () => {
    const payload = {...setup.value}
    if (mode.value === 'item-status') payload.active = payload.active ?? false
    await api('/inventory/setup/' + mode.value, 'POST', payload)
    await load()
    closeSetupForm()
    notice.value = 'Changes saved.'
    notify('saved', 'Inventory setup', 'Changes saved.')
  })
}
function deleteSetupRecord(kind: 'warehouse' | 'location' | 'unit' | 'conversion' | 'site', recordId: number, label: string) {
  const isPermanent = workspace.company?.deletion_policy === 'permanent'
  const promptText = isPermanent
    ? `${t('Permanently delete')} "${label}"? ${t('This action cannot be undone.')}`
    : `${t('Delete')} "${label}"? ${t('The record will be archived according to system deletion policy.')}`
  if (!window.confirm(promptText)) return
  void run(async () => {
    await api(`/inventory/setup/${kind}/${recordId}`, 'DELETE')
    await load()
    notice.value = 'Record deleted.'
    notify('saved', 'Inventory setup', 'Record deleted.')
  })
}
function opening(close: boolean) {
  void run(async () => {
    await postOpening('/inventory/opening', {site_id: openingSite.value, lines: openingLines.value, close})
    openingLines.value = []
    await load()
    const msg = close ? 'Opening balances closed.' : 'Opening balances saved.'
    notice.value = msg
    notify(close ? 'confirmation' : 'saved', 'Opening balance', msg)
  })
}
function saveReverse() {
  void run(async () => {
    if (!reversal.value) return
    const {id: key, ...payload} = reversal.value
    await reverse('/inventory/movements/' + key + '/reverse', payload)
    await load()
    notice.value = 'Stock movement recorded.'
    notify('confirmation', 'Stock movements', 'Stock movement recorded.')
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
  {id: 'receipts', label: t('Goods receipts')}, {id: 'items', label: t('Inventory items')}, {id: 'balances', label: t('Stock balances')}, {id: 'movements', label: t('Stock movements')},
  ...(scoped('opening') ? [{id: 'opening', label: t('Opening balance')}] : []),
  ...(scoped('setup') ? [{id: 'setup', label: t('Inventory setup')}] : []),
])
async function locationState(id: number, active: boolean) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api('/inventory/locations/' + id + '/state', 'PATCH', { active })
    const item = catalog.value?.locations.find(row => row.id === id)
    if (item) item.active = active
    const msg = active ? 'Location reactivated.' : 'Location deactivated.'
    notice.value = msg
    notify('confirmation', 'Inventory setup', msg)
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Request failed.'
    notify('fault', 'Inventory setup', error.value)
  } finally {
    busy.value = false
  }
}
async function warehouseState(id:number, active:boolean){
  if(busy.value)return
  busy.value=true;error.value=''
  try{
    await api('/inventory/warehouses/'+id+'/state','PATCH',{active})
    const item=catalog.value?.warehouses.find(row=>row.id===id)
    if(item)item.active=active
    notify('saved', 'Inventory setup', 'Changes saved.')
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
      <nav class="section-tabs" style="margin-block-start: 6px;">
        <button type="button" class="tab-link" :aria-current="setupTab === 'warehouses' ? 'page' : undefined" @click="openSetupTab('warehouses')">
          {{ t('Warehouses') }} &amp; {{ t('Locations') }} ({{ filteredWarehouses.length }})
        </button>
        <button type="button" class="tab-link" :aria-current="setupTab === 'units' ? 'page' : undefined" @click="openSetupTab('units')">
          {{ t('Units of measure') }} ({{ filteredUnits.length }})
        </button>
        <button type="button" class="tab-link" :aria-current="setupTab === 'conversion' ? 'page' : undefined" @click="openSetupTab('conversion')">
          {{ t('Unit conversion') }} ({{ filteredConversions.length }})
        </button>
        <button type="button" class="tab-link" :aria-current="setupTab === 'warehouse' ? 'page' : undefined" @click="openSetupTab('warehouse')">
          {{ t('Warehouse') }} ({{ filteredWarehouses.length }})
        </button>
        <button type="button" class="tab-link" :aria-current="setupTab === 'location' ? 'page' : undefined" @click="openSetupTab('location')">
          {{ t('Location') }} ({{ filteredLocations.length }})
        </button>
        <button type="button" class="tab-link" :aria-current="setupTab === 'site' ? 'page' : undefined" @click="openSetupTab('site')">
          {{ t('Business day') }} ({{ filteredSiteSettings.length }})
        </button>
      </nav>

      <!-- TAB 1: Warehouses & Locations Overview -->
      <template v-if="setupTab === 'warehouses'">
        <nav class="section-tabs" style="margin-block-start: 10px; margin-block-end: 6px;">
          <button type="button" class="tab-link" :aria-current="overviewSubTab === 'warehouses' ? 'page' : undefined" @click="overviewSubTab = 'warehouses'; setupSearch = ''; setupPage = 1">
            {{ t('Warehouses') }} ({{ filteredWarehouses.length }})
          </button>
          <button type="button" class="tab-link" :aria-current="overviewSubTab === 'locations' ? 'page' : undefined" @click="overviewSubTab = 'locations'; setupSearch = ''; setupPage = 1">
            {{ t('Locations') }} ({{ filteredLocations.length }})
          </button>
        </nav>

        <section v-if="overviewSubTab === 'warehouses'" class="panel table-panel" style="padding: 18px; display: grid; gap: 14px;">
          <div class="page-heading">
            <h2>{{ t('Warehouses') }} ({{ filteredWarehouses.length }})</h2>
            <div class="actions">
              <button type="button" class="secondary" :aria-pressed="setupViewMode === 'list'" @click="setupViewMode = 'list'">{{ t('List view') }}</button>
              <button type="button" class="secondary" :aria-pressed="setupViewMode === 'grid'" @click="setupViewMode = 'grid'">{{ t('Grid view') }}</button>
              <CsvExport :workspace="workspace" screen="warehouse" />
            </div>
          </div>
          <div class="actions">
            <label>{{ t('Status') }}
              <select v-model="setupStatus" @change="setupPage = 1">
                <option value="all">{{ t('All') }}</option>
                <option value="active">{{ t('Active') }}</option>
                <option value="inactive">{{ t('Inactive') }}</option>
              </select>
            </label>
          </div>
          <TableToolbar :page="setupPage" :size="setupSize" :count="filteredWarehouses.length" :query="setupSearch" @search="setupSearch = $event; setupPage = 1" @page="setupPage = $event" @size="setupSize = $event; setupPage = 1" />
          <div v-if="setupViewMode === 'list'">
            <table>
              <thead>
                <tr>
                  <th>{{ t('Code') }}</th>
                  <th>{{ t('Name') }}</th>
                  <th>{{ t('Site') }}</th>
                  <th>{{ t('Status') }}</th>
                  <th>{{ t('Actions') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in pagedWarehouses" :key="row.id">
                  <td><bdi>{{ row.code || '#' + row.id }}</bdi></td>
                  <td>{{ row.name }}</td>
                  <td>{{ name(catalog?.sites as any, row.site_id) }}</td>
                  <td><span :class="['badge', row.active ? 'on' : '']">{{ t(row.active ? 'Active' : 'Inactive') }}</span></td>
                  <td class="actions">
                    <button type="button" class="secondary" :disabled="busy" @click="editWarehouse(row)">{{ t('Edit') }}</button>
                    <button type="button" class="secondary" :disabled="busy" @click="warehouseState(row.id, !row.active)">
                      {{ t(row.active ? 'Deactivate warehouse' : 'Reactivate warehouse') }}
                    </button>
                  <button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('warehouse', row.id, row.code + ' - ' + row.name)">{{ t('Delete') }}</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-if="!pagedWarehouses.length" style="padding: 14px;">{{ t('No records found.') }}</p>
          </div>
          <div v-else class="card-grid" style="margin-block-end: 0;">
            <div v-for="row in pagedWarehouses" :key="row.id" class="panel" style="padding: 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px;">
              <div>
                <small class="muted"><bdi>{{ row.code || '#' + row.id }}</bdi> · {{ name(catalog?.sites as any, row.site_id) }}</small>
                <div><strong>{{ row.name }}</strong></div>
                <div style="margin-block-start: 4px;"><span :class="['badge', row.active ? 'on' : '']">{{ t(row.active ? 'Active' : 'Inactive') }}</span></div>
              </div>
              <div class="actions">
                <button type="button" class="secondary" :disabled="busy" @click="editWarehouse(row)">{{ t('Edit') }}</button>
                <button type="button" class="secondary" :disabled="busy" @click="warehouseState(row.id, !row.active)">
                  {{ t(row.active ? 'Deactivate warehouse' : 'Reactivate warehouse') }}
                </button>
                  <button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('warehouse', row.id, row.code + ' - ' + row.name)">{{ t('Delete') }}</button>
              </div>
            </div>
            <p v-if="!pagedWarehouses.length" style="padding: 14px;">{{ t('No records found.') }}</p>
          </div>
        </section>

        <section v-else class="panel table-panel" style="padding: 18px; display: grid; gap: 14px;">
          <div class="page-heading">
            <h2>{{ t('Locations') }} ({{ filteredLocations.length }})</h2>
            <div class="actions">
              <button type="button" class="secondary" :aria-pressed="setupViewMode === 'list'" @click="setupViewMode = 'list'">{{ t('List view') }}</button>
              <button type="button" class="secondary" :aria-pressed="setupViewMode === 'grid'" @click="setupViewMode = 'grid'">{{ t('Grid view') }}</button>
              <CsvExport :workspace="workspace" screen="location" />
            </div>
          </div>
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; align-items: end; max-inline-size: 46rem;">
            <label>{{ t('Status') }}
              <select v-model="locationStatus" @change="setupPage = 1">
                <option value="all">{{ t('All') }}</option>
                <option value="active">{{ t('Active') }}</option>
                <option value="inactive">{{ t('Inactive') }}</option>
              </select>
            </label>
            <EntityAutocomplete
              v-model="setupWarehouseFilter"
              :label="t('Warehouse')"
              :choices="warehouseFilterChoices"
              :placeholder="t('Search by code or name')"
              @select="setupPage = 1"
            />
          </div>
          <TableToolbar :page="setupPage" :size="setupSize" :count="filteredLocations.length" :query="setupSearch" @search="setupSearch = $event; setupPage = 1" @page="setupPage = $event" @size="setupSize = $event; setupPage = 1" />
          <div v-if="setupViewMode === 'list'">
            <table>
              <thead>
                <tr>
                  <th>{{ t('Code') }}</th>
                  <th>{{ t('Name') }}</th>
                  <th>{{ t('Warehouse') }}</th>
                  <th>{{ t('Status') }}</th>
                  <th>{{ t('Actions') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="loc in pagedLocations" :key="loc.id">
                  <td><bdi>{{ loc.code || '#' + loc.id }}</bdi></td>
                  <td>{{ loc.name }}</td>
                  <td>{{ name(catalog?.warehouses, loc.warehouse_id) }}</td>
                  <td><span :class="['badge', loc.active !== false ? 'on' : '']">{{ t(loc.active !== false ? 'Active' : 'Inactive') }}</span></td>
                  <td class="actions">
                    <button type="button" class="secondary" :disabled="busy" @click="editLocation(loc)">{{ t('Edit') }}</button>
                    <button type="button" class="secondary" :disabled="busy" @click="locationState(loc.id, loc.active === false)">
                      {{ t(loc.active !== false ? 'Deactivate location' : 'Reactivate location') }}
                    </button>
                  <button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('location', loc.id, loc.code + ' - ' + loc.name)">{{ t('Delete') }}</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-if="!pagedLocations.length" style="padding: 14px;">{{ t('No records found.') }}</p>
          </div>
          <div v-else class="card-grid" style="margin-block-end: 0;">
            <div v-for="loc in pagedLocations" :key="loc.id" class="panel" style="padding: 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px;">
              <div>
                <small class="muted"><bdi>{{ loc.code || '#' + loc.id }}</bdi> · {{ name(catalog?.warehouses, loc.warehouse_id) }}</small>
                <div><strong>{{ loc.name }}</strong></div>
                <div style="margin-block-start: 4px;"><span :class="['badge', loc.active !== false ? 'on' : '']">{{ t(loc.active !== false ? 'Active' : 'Inactive') }}</span></div>
              </div>
              <div class="actions">
                <button type="button" class="secondary" :disabled="busy" @click="editLocation(loc)">{{ t('Edit') }}</button>
                <button type="button" class="secondary" :disabled="busy" @click="locationState(loc.id, loc.active === false)">
                  {{ t(loc.active !== false ? 'Deactivate location' : 'Reactivate location') }}
                </button>
                  <button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('location', loc.id, loc.code + ' - ' + loc.name)">{{ t('Delete') }}</button>
              </div>
            </div>
            <p v-if="!pagedLocations.length" style="padding: 14px;">{{ t('No records found.') }}</p>
          </div>
        </section>
      </template>

      <!-- TAB 2: Units of Measure -->
      <section v-else-if="setupTab === 'units' && companySetup" class="panel table-panel" style="padding: 18px; display: grid; gap: 14px;">
        <div class="page-heading">
          <h2>{{ t('Units of measure') }}</h2>
          <div class="actions">
            <CsvExport :workspace="workspace" screen="unit" />
            <button v-if="!showUnitForm" type="button" class="primary" @click="editingUnitId = null; unitForm = {code: '', name: ''}; showUnitForm = true">+ {{ t('Add unit of measure') }}</button>
          </div>
        </div>
        <form v-validated-form v-if="showUnitForm" class="panel" style="padding: 16px; display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; align-items: end;" @submit.prevent="saveUnit">
          <div class="field-wrap"><label>{{ t('Unit code') }}<input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="unitForm.code" required maxlength="24"></label><FieldHint :label="t('Unit code')" required /></div>
          <div class="field-wrap"><label>{{ t('Unit name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="unitForm.name" required maxlength="80"></label><FieldHint :label="t('Unit name')" required /></div>
          <div class="actions">
            <button class="primary" :disabled="busy">{{ t('Save') }}</button>
            <button type="button" class="secondary" :disabled="busy" @click="showUnitForm = false; editingUnitId = null; unitForm = {code: '', name: ''}">{{ t('Cancel') }}</button>
          </div>
        </form>
        <TableToolbar :page="setupPage" :size="setupSize" :count="filteredUnits.length" :query="setupSearch" @search="setupSearch = $event; setupPage = 1" @page="setupPage = $event" @size="setupSize = $event; setupPage = 1" />
        <div v-if="pagedUnits.length">
          <table>
            <thead>
              <tr>
                <th>{{ t('Unit code') }}</th>
                <th>{{ t('Unit name') }}</th>
                <th>{{ t('Actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="unit in pagedUnits" :key="unit.id">
                <td><bdi>{{ unit.code }}</bdi></td>
                <td>{{ t(unit.name) }}</td>
                <td><div class="actions"><button type="button" class="secondary" :disabled="busy" @click="editUnit(unit)">{{ t('Edit') }}</button><button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('unit', unit.id, unit.code + ' - ' + unit.name)">{{ t('Delete') }}</button></div></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else style="padding: 14px;">{{ t('No records found.') }}</p>
      </section>

      <!-- TAB 3: Unit Conversion -->
      <section v-else-if="setupTab === 'conversion'" class="panel table-panel" style="padding: 18px; display: grid; gap: 14px;">
        <div class="page-heading">
          <h2>{{ t('Unit conversion') }} ({{ filteredConversions.length }})</h2>
          <div class="actions">
            <button v-if="!showSetupForm" type="button" class="primary" @click="openNewSetupForm('conversion')">+ {{ t('Unit conversion') }}</button>
          </div>
        </div>
        <form v-validated-form v-if="showSetupForm" class="panel" style="padding: 16px; display: grid; gap: 16px;" @submit.prevent="saveSetup">
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
            <EntityAutocomplete
              v-model="setup.item_id"
              :label="t('Item')"
              :choices="itemChoicesWithGeneral"
              :placeholder="t('Search by code or name')"
              data-field="item_id"
            />
            <EntityAutocomplete
              v-if="!setup.item_id"
              v-model="setup.base_unit_id"
              :label="t('Base unit')"
              :choices="unitChoices"
              :placeholder="t('Search by code or name')"
              data-field="base_unit_id"
              required
            />
            <EntityAutocomplete
              v-model="setup.unit_id"
              :label="t('From unit')"
              :choices="unitChoices"
              :placeholder="t('Search by code or name')"
              data-field="unit_id"
              required
            />
            <div class="field-wrap">
              <label>{{ t('Conversion factor') }}<input data-field="factor" :placeholder="t('e.g., 125.50')" v-model="setup.factor" required inputmode="decimal"></label>
              <FieldHint :label="t('Conversion factor')" required />
            </div>
          </div>
          <div class="actions">
            <button class="primary" :disabled="busy">{{ t('Save') }}</button>
            <button type="button" class="secondary" :disabled="busy" @click="closeSetupForm('conversion')">{{ t('Cancel') }}</button>
          </div>
        </form>
        <TableToolbar :page="setupPage" :size="setupSize" :count="filteredConversions.length" :query="setupSearch" @search="setupSearch = $event; setupPage = 1" @page="setupPage = $event" @size="setupSize = $event; setupPage = 1" />
        <div v-if="pagedConversions.length">
          <table>
            <thead>
              <tr>
                <th>{{ t('Item') }}</th>
                <th>{{ t('From unit') }}</th>
                <th>{{ t('Base unit') }}</th>
                <th>{{ t('Conversion factor') }}</th>
                <th>{{ t('Actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="conv in pagedConversions" :key="conv.id">
                <td>{{ conv.item_id ? name(catalog?.items, conv.item_id) : t('General conversion') }}</td>
                <td>{{ name(catalog?.units, conv.unit_id) }}</td>
                <td>{{ name(catalog?.units, conv.base_unit_id) }}</td>
                <td><bdi>{{ conv.factor }}</bdi></td>
                <td><div class="actions"><button type="button" class="secondary" :disabled="busy" @click="editConversion(conv)">{{ t('Edit') }}</button><button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('conversion', conv.id, name(catalog?.units, conv.unit_id) + ' → ' + name(catalog?.units, conv.base_unit_id))">{{ t('Delete') }}</button></div></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else style="padding: 14px;">{{ t('No records found.') }}</p>
      </section>

      <!-- TAB 4: Warehouse -->
      <section v-else-if="setupTab === 'warehouse'" class="panel table-panel" style="padding: 18px; display: grid; gap: 14px;">
        <div class="page-heading">
          <h2>{{ t('Warehouses') }} ({{ filteredWarehouses.length }})</h2>
          <div class="actions">
            <button type="button" class="secondary" :aria-pressed="setupViewMode === 'list'" @click="setupViewMode = 'list'">{{ t('List view') }}</button>
            <button type="button" class="secondary" :aria-pressed="setupViewMode === 'grid'" @click="setupViewMode = 'grid'">{{ t('Grid view') }}</button>
            <button v-if="!showSetupForm" type="button" class="primary" @click="openNewSetupForm('warehouse')">+ {{ t('Warehouse') }}</button>
          </div>
        </div>
        <form v-validated-form v-if="showSetupForm" class="panel" style="padding: 16px; display: grid; gap: 16px;" @submit.prevent="saveSetup">
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
            <div class="field-wrap"><label>{{ t('Code') }}<input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="setup.code" required maxlength="32"></label><FieldHint :label="t('Code')" required /></div>
            <div class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="setup.name" required maxlength="120"></label><FieldHint :label="t('Name')" required /></div>
            <EntityAutocomplete
              v-model="setup.site_id"
              :label="t('Site')"
              :choices="siteChoices"
              :placeholder="t('Search by code or name')"
              data-field="site_id"
              required
            />
          </div>
          <div class="actions">
            <button class="primary" :disabled="busy">{{ t('Save') }}</button>
            <button type="button" class="secondary" :disabled="busy" @click="closeSetupForm('warehouse')">{{ t('Cancel') }}</button>
          </div>
        </form>
        <div class="actions">
          <label>{{ t('Status') }}
            <select v-model="setupStatus" @change="setupPage = 1">
              <option value="all">{{ t('All') }}</option>
              <option value="active">{{ t('Active') }}</option>
              <option value="inactive">{{ t('Inactive') }}</option>
            </select>
          </label>
        </div>
        <TableToolbar :page="setupPage" :size="setupSize" :count="filteredWarehouses.length" :query="setupSearch" @search="setupSearch = $event; setupPage = 1" @page="setupPage = $event" @size="setupSize = $event; setupPage = 1" />
        <div v-if="setupViewMode === 'list'">
          <table>
            <thead>
              <tr>
                <th>{{ t('Code') }}</th>
                <th>{{ t('Name') }}</th>
                <th>{{ t('Site') }}</th>
                <th>{{ t('Status') }}</th>
                <th>{{ t('Actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in pagedWarehouses" :key="row.id">
                <td><bdi>{{ row.code || '#' + row.id }}</bdi></td>
                <td>{{ row.name }}</td>
                <td>{{ name(catalog?.sites as any, row.site_id) }}</td>
                <td><span :class="['badge', row.active ? 'on' : '']">{{ t(row.active ? 'Active' : 'Inactive') }}</span></td>
                <td class="actions">
                  <button type="button" class="secondary" :disabled="busy" @click="editWarehouse(row)">{{ t('Edit') }}</button>
                  <button type="button" class="secondary" :disabled="busy" @click="warehouseState(row.id, !row.active)">
                    {{ t(row.active ? 'Deactivate warehouse' : 'Reactivate warehouse') }}
                  </button>
                  <button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('warehouse', row.id, row.code + ' - ' + row.name)">{{ t('Delete') }}</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!pagedWarehouses.length" style="padding: 14px;">{{ t('No records found.') }}</p>
        </div>
        <div v-else class="card-grid" style="margin-block-end: 0;">
          <div v-for="row in pagedWarehouses" :key="row.id" class="panel" style="padding: 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px;">
            <div>
              <small class="muted"><bdi>{{ row.code || '#' + row.id }}</bdi> · {{ name(catalog?.sites as any, row.site_id) }}</small>
              <div><strong>{{ row.name }}</strong></div>
              <div style="margin-block-start: 4px;"><span :class="['badge', row.active ? 'on' : '']">{{ t(row.active ? 'Active' : 'Inactive') }}</span></div>
            </div>
            <div class="actions">
              <button type="button" class="secondary" :disabled="busy" @click="editWarehouse(row)">{{ t('Edit') }}</button>
              <button type="button" class="secondary" :disabled="busy" @click="warehouseState(row.id, !row.active)">
                {{ t(row.active ? 'Deactivate warehouse' : 'Reactivate warehouse') }}
              </button>
                  <button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('warehouse', row.id, row.code + ' - ' + row.name)">{{ t('Delete') }}</button>
            </div>
          </div>
          <p v-if="!pagedWarehouses.length" style="padding: 14px;">{{ t('No records found.') }}</p>
        </div>
      </section>

      <!-- TAB 5: Location -->
      <section v-else-if="setupTab === 'location'" class="panel table-panel" style="padding: 18px; display: grid; gap: 14px;">
        <div class="page-heading">
          <h2>{{ t('Locations') }} ({{ filteredLocations.length }})</h2>
          <div class="actions">
            <button type="button" class="secondary" :aria-pressed="setupViewMode === 'list'" @click="setupViewMode = 'list'">{{ t('List view') }}</button>
            <button type="button" class="secondary" :aria-pressed="setupViewMode === 'grid'" @click="setupViewMode = 'grid'">{{ t('Grid view') }}</button>
            <button v-if="!showSetupForm" type="button" class="primary" @click="openNewSetupForm('location')">+ {{ t('Location') }}</button>
          </div>
        </div>
        <form v-validated-form v-if="showSetupForm" class="panel" style="padding: 16px; display: grid; gap: 16px;" @submit.prevent="saveSetup">
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
            <div class="field-wrap"><label>{{ t('Code') }}<input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="setup.code" required maxlength="32"></label><FieldHint :label="t('Code')" required /></div>
            <div class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="setup.name" required maxlength="120"></label><FieldHint :label="t('Name')" required /></div>
            <EntityAutocomplete
              v-model="setup.warehouse_id"
              :label="t('Warehouse')"
              :choices="activeWarehouseChoices"
              :placeholder="t('Search by code or name')"
              data-field="warehouse_id"
              required
            />
          </div>
          <label class="checkbox-inline" style="display: inline-flex; align-items: center; gap: 8px; cursor: pointer;">
            <input type="checkbox" v-model="setup.active" :true-value="true" :false-value="false">
            <span>{{ t('Active') }}</span>
          </label>
          <div class="actions">
            <button class="primary" :disabled="busy">{{ t('Save') }}</button>
            <button type="button" class="secondary" :disabled="busy" @click="closeSetupForm('location')">{{ t('Cancel') }}</button>
          </div>
        </form>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; align-items: end; max-inline-size: 46rem;">
          <label>{{ t('Status') }}
            <select v-model="locationStatus" @change="setupPage = 1">
              <option value="all">{{ t('All') }}</option>
              <option value="active">{{ t('Active') }}</option>
              <option value="inactive">{{ t('Inactive') }}</option>
            </select>
          </label>
          <EntityAutocomplete
            v-model="setupWarehouseFilter"
            :label="t('Warehouse')"
            :choices="warehouseFilterChoices"
            :placeholder="t('Search by code or name')"
            @select="setupPage = 1"
          />
        </div>
        <TableToolbar :page="setupPage" :size="setupSize" :count="filteredLocations.length" :query="setupSearch" @search="setupSearch = $event; setupPage = 1" @page="setupPage = $event" @size="setupSize = $event; setupPage = 1" />
        <div v-if="setupViewMode === 'list'">
          <table>
            <thead>
              <tr>
                <th>{{ t('Code') }}</th>
                <th>{{ t('Name') }}</th>
                <th>{{ t('Warehouse') }}</th>
                <th>{{ t('Status') }}</th>
                <th>{{ t('Actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="loc in pagedLocations" :key="loc.id">
                <td><bdi>{{ loc.code || '#' + loc.id }}</bdi></td>
                <td>{{ loc.name }}</td>
                <td>{{ name(catalog?.warehouses, loc.warehouse_id) }}</td>
                <td><span :class="['badge', loc.active !== false ? 'on' : '']">{{ t(loc.active !== false ? 'Active' : 'Inactive') }}</span></td>
                <td class="actions">
                  <button type="button" class="secondary" :disabled="busy" @click="editLocation(loc)">{{ t('Edit') }}</button>
                  <button type="button" class="secondary" :disabled="busy" @click="locationState(loc.id, loc.active === false)">
                    {{ t(loc.active !== false ? 'Deactivate location' : 'Reactivate location') }}
                  </button>
                  <button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('location', loc.id, loc.code + ' - ' + loc.name)">{{ t('Delete') }}</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!pagedLocations.length" style="padding: 14px;">{{ t('No records found.') }}</p>
        </div>
        <div v-else class="card-grid" style="margin-block-end: 0;">
          <div v-for="loc in pagedLocations" :key="loc.id" class="panel" style="padding: 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px;">
            <div>
              <small class="muted"><bdi>{{ loc.code || '#' + loc.id }}</bdi> · {{ name(catalog?.warehouses, loc.warehouse_id) }}</small>
              <div><strong>{{ loc.name }}</strong></div>
              <div style="margin-block-start: 4px;"><span :class="['badge', loc.active !== false ? 'on' : '']">{{ t(loc.active !== false ? 'Active' : 'Inactive') }}</span></div>
            </div>
            <div class="actions">
              <button type="button" class="secondary" :disabled="busy" @click="editLocation(loc)">{{ t('Edit') }}</button>
              <button type="button" class="secondary" :disabled="busy" @click="locationState(loc.id, loc.active === false)">
                {{ t(loc.active !== false ? 'Deactivate location' : 'Reactivate location') }}
              </button>
                  <button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('location', loc.id, loc.code + ' - ' + loc.name)">{{ t('Delete') }}</button>
            </div>
          </div>
          <p v-if="!pagedLocations.length" style="padding: 14px;">{{ t('No records found.') }}</p>
        </div>
      </section>

      <!-- TAB 6: Business Day -->
      <section v-else-if="setupTab === 'site'" class="panel table-panel" style="padding: 18px; display: grid; gap: 14px;">
        <div class="page-heading">
          <h2>{{ t('Business day') }} ({{ filteredSiteSettings.length }})</h2>
          <div class="actions">
            <button v-if="!showSetupForm" type="button" class="primary" @click="openNewSetupForm('site')">+ {{ t('Business day') }}</button>
          </div>
        </div>
        <form v-validated-form v-if="showSetupForm" class="panel" style="padding: 16px; display: grid; gap: 16px;" @submit.prevent="saveSetup">
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
            <EntityAutocomplete
              v-model="setup.site_id"
              :label="t('Site')"
              :choices="siteChoices"
              :placeholder="t('Search by code or name')"
              data-field="site_id"
              required
            />
            <div class="field-wrap"><label>{{ t('Time zone') }}<input data-field="timezone" v-model="setup.timezone" placeholder="Asia/Dubai" required></label><FieldHint :label="t('Time zone')" required /></div>
            <div class="field-wrap"><label>{{ t('Business day starts at') }}<input data-field="day_start" v-model="setup.day_start" type="time" required></label><FieldHint :label="t('Business day starts at')" required /></div>
          </div>
          <div class="actions">
            <button class="primary" :disabled="busy">{{ t('Save') }}</button>
            <button type="button" class="secondary" :disabled="busy" @click="closeSetupForm('site')">{{ t('Cancel') }}</button>
          </div>
        </form>
        <TableToolbar :page="setupPage" :size="setupSize" :count="filteredSiteSettings.length" :query="setupSearch" @search="setupSearch = $event; setupPage = 1" @page="setupPage = $event" @size="setupSize = $event; setupPage = 1" />
        <div v-if="pagedSiteSettings.length">
          <table>
            <thead>
              <tr>
                <th>{{ t('Site') }}</th>
                <th>{{ t('Time zone') }}</th>
                <th>{{ t('Business day starts at') }}</th>
                <th>{{ t('Actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="st in pagedSiteSettings" :key="st.site_id">
                <td>{{ st.site_name }}</td>
                <td><bdi>{{ st.timezone }}</bdi></td>
                <td><bdi>{{ st.day_start }}</bdi></td>
                <td><div class="actions"><button type="button" class="secondary" :disabled="busy" @click="editSiteSetting(st)">{{ t('Edit') }}</button><button type="button" class="secondary" :disabled="busy" @click="deleteSetupRecord('site', st.site_id, st.site_name)">{{ t('Delete') }}</button></div></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else style="padding: 14px;">{{ t('No records found.') }}</p>
      </section>
    </template>
  </template>
</template>
