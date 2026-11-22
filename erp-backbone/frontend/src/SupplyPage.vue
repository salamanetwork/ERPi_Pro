<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, createRequest, type Workspace } from './api'
import { message, t } from './i18n'

type DraftLine = { item_id?:number; source_id?:number; destination_id?:number; quantity:string }
type Line = Required<DraftLine> & { id:number; item:string; source:string; destination:string; reserved:string }
type Difference = { id:number; quantity:string; kind:string; state:string; reason:string; decision_reason:string; reporter:string; reviewer:string|null }
type ShipmentLine = { id:number; item:string; quantity:string; remaining:string; remaining_value:string; discrepancies:Difference[] }
type Shipment = { id:number; number:string; carrier:string; reference:string; eta:string|null; dispatched_at:string; lines:ShipmentLine[] }
type Header = { id:number; number:string; state:string; site_id:number; destination_id:number; revision:number; note:string }
type Transfer = Header & { lines:Line[]; shipments:Shipment[]; actions:string[]; events:{id:number; kind:string; at:string; quantity:string; value:string; entry_id:number|null}[] }
type Catalog = { items:{id:number; code:string; name:string}[]; locations:{id:number; site_id:number; site_name:string; name:string}[] }
type Hold = { id:number; site_id:number; item:string; location:string; quantity:string; order_id:number|null; order:string|null; transfer_id:number|null; transfer:string|null }
type Page<T> = {count:number; next:string|null; previous:string|null; results:T[]}
type ShipmentRow = Omit<Shipment, "lines"> & {transfer_id:number;state:string}
type Transit = {operational_value:string; posted_value:string|null; difference:string|null; pending_count:number; items:{transfer_id:number; shipment:string; item:string; quantity:string; value:string}[]}
type Order = {id:number; number:string; site_id:number; status:string; lines:{id:number; name:string}[]}
const props = defineProps<{workspace:Workspace}>()
const route = useRoute()
const router = useRouter()
const request = createRequest()
const section = computed(() => String(route.params.section || 'transfers'))
const entry = computed(() => String(route.params.entry || ''))
const draftOpen = computed(() => entry.value === 'new' || action.value === 'edit')
const catalog = ref<Catalog>({items:[], locations:[]})
const transfer = ref<Transfer>()
const transfers = ref<Page<Header>>()
const holds = ref<Page<Hold>>()
const shipmentPage = ref<Page<ShipmentRow>>()
const transit = ref<Transit>()
const order = ref<Order>()
const source = ref<number>()
const destination = ref<number>()
const note = ref('')
const lines = ref<DraftLine[]>([])
const action = ref('')
const activeShipment = ref<Shipment>()
const target = ref<number>()
const reason = ref('')
const decision = ref('approve')
const kind = ref('shortage')
const amount = ref('')
const quantities = ref<Record<number,string>>({})
const carrier = ref('')
const reference = ref('')
const eta = ref('')
const orderLine = ref<number>()
const stockLocation = ref<number>()
const releaseHold = ref<number>()
const error = ref('')
const notice = ref('')
const busy = ref(false)
const canFinance = computed(() => allows(props.workspace, 'accounting.view', null))
const sourceOptions = computed(() => props.workspace.sites.filter(site => site.active && allows(props.workspace,'supply.prepare',site.id) && allows(props.workspace,'supply.view',site.id)))
const destinations = computed(() => [...new Map(catalog.value.locations.map(loc => [loc.site_id,loc.site_name]))])
const locations = (site?:number) => catalog.value.locations.filter(loc => loc.site_id === site)
const siteName = (site:number) => props.workspace.sites.find(row => row.id === site)?.name || destinations.value.find(row => row[0] === site)?.[1] || String(site)
const stockState:Record<string,string> = {draft:'Draft', confirmed:'Confirmed', in_transit:'In transit', partial:'Partially settled', received:'Completed', cancelled:'Cancelled',
  pending:'Pending', approved:'Approved', dismissed:'Dismissed', damage:'Damage', shortage:'Shortage', transfer_in:'Stock received', transfer_back:'Returned to source', transfer_loss:'Discrepancy settled'}
const actionLabels:Record<string,string> = {edit:'Edit', confirm:'Confirm reservation', dispatch:'Dispatch shipment', receive:'Receive shipment', return:'Return to source', report:'Report discrepancy', decide:'Review discrepancy', cancel:'Cancel unshipped stock'}
const status = (state:string) => t(stockState[state] || state)
const showAction = (permission:string) => transfer.value?.actions.includes(permission)
const canHold = computed(() => order.value?.status === 'confirmed' && allows(props.workspace,'supply.prepare',order.value.site_id))
const currentPage = computed(() => Math.max(1, Number(route.query.page) || 1))
const pageInfo = computed(() => section.value === 'transfers' ? transfers.value : section.value==='shipments' ? shipmentPage.value : holds.value)
const pages = ['transfers','reservations','shipments','transit']
const labels:Record<string,string> = {transfers:'Stock transfers', reservations:'Reservations', shipments:'Shipment tracking', transit:'In transit'}
const blank = () => ({quantity:''})
let loadRevision = 0
async function load() {
  const revision = ++loadRevision
  const query = new URLSearchParams()
  for (const key of ['page_size','page','site','item','location','order','state','q','discrepancy']) {
    const value = route.query[key]
    if (typeof value === 'string' && value) query.set(key,value)
  }
  try {
    if (!catalog.value.locations.length) catalog.value = await api<Catalog>('/supply/catalog')
    if (revision !== loadRevision) return
    if (section.value === 'transfers') {
      if (entry.value === 'new') {
        source.value = sourceOptions.value[0]?.id
        destination.value = source.value
        note.value = ''
        lines.value = [blank()]
      } else if (entry.value) {
        const data = await api<Transfer>('/supply/transfers/' + entry.value)
        if (revision === loadRevision) transfer.value = data
      } else {
        const data = await api<Page<Header>>('/supply/transfers?' + query)
        if (revision === loadRevision) transfers.value = data
      }
    } else if (section.value === 'reservations') {
      const data = await api<Page<Hold>>('/supply/reservations?' + query)
      const selected = query.get('order')
      const linked = selected ? await api<Order>('/sales/documents/' + selected) : undefined
      if (revision === loadRevision) { holds.value = data; order.value = linked }
    } else if (section.value === 'shipments') {
      const data = await api<Page<ShipmentRow>>('/supply/shipments?' + query)
      if (revision === loadRevision) shipmentPage.value = data
    } else if (canFinance.value) {
      const data = await api<Transit>('/supply/reconciliation?' + query)
      if (revision === loadRevision) transit.value = data
    }
  } catch (failure) { if (revision === loadRevision) error.value = message(failure) }
}
watch(() => route.fullPath, () => {
  transfer.value = undefined
  action.value = ''
  error.value = ''
  notice.value = ''
  order.value = undefined
  void load()
}, {immediate:true})
function open(operation:string, shipment?:Shipment, key?:number) {
  action.value = operation
  activeShipment.value = shipment
  target.value = key
  quantities.value = {}
  amount.value = ''
  reason.value = ''
  reference.value = ''
  carrier.value = ''
  eta.value = ''
  decision.value = 'approve'
  if (operation === 'edit' && transfer.value) {
    source.value = transfer.value.site_id
    destination.value = transfer.value.destination_id
    note.value = transfer.value.note
    lines.value = transfer.value.lines.map(({item_id,source_id,destination_id,quantity}) => ({item_id,source_id,destination_id,quantity}))
  }
}
function submit(payload:object, url:string, callback:(data:Transfer)=>void) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  return request<Transfer>(url,payload).then(data => {
    callback(data)
    notice.value = t('Saved successfully.')
    action.value = ''
  }).catch(failure => { error.value = message(failure) }).finally(() => {busy.value = false})
}
function saveDraft() {
  const payload = {site_id:source.value,destination_id:destination.value,note:note.value,lines:lines.value,revision:transfer.value?.revision}
  const url = action.value === 'edit' ? '/supply/transfers/' + transfer.value?.id + '/edit' : '/supply/transfers'
  return submit(payload,url,data => {
    transfer.value = data
    if (entry.value === 'new') void router.replace('/supply/transfers/' + data.id)
  })
}
const actionLines = computed(() => action.value === 'dispatch' ? transfer.value?.lines.filter(line => Number(line.reserved)>0).map(line => ({id:line.id,item:line.item,maximum:line.reserved})) :
  activeShipment.value?.lines.filter(line => Number(line.remaining)>0).map(line => ({id:line.id,item:line.item,maximum:line.remaining})))
function run() {
  const payload = {revision:transfer.value?.revision, reason:reason.value, carrier:carrier.value, reference:reference.value, eta:eta.value || undefined,
    shipment_id:activeShipment.value?.id, shipment_line_id:target.value, discrepancy_id:target.value, decision:decision.value, kind:kind.value, quantity:amount.value,
    lines:Object.entries(quantities.value).filter(([,value]) => value.trim()).map(([line_id,quantity]) => ({line_id:Number(line_id),quantity}))}
  return submit(payload,'/supply/transfers/' + transfer.value?.id + '/' + action.value,data => {transfer.value = data})
}
function saveHold(release = false) {
  const payload = {quantity:amount.value,line_id:orderLine.value,location_id:stockLocation.value,reservation_id:releaseHold.value}
  return submit(payload,'/supply/sales/' + order.value?.id + (release ? '/release' : '/reserve'),() => { amount.value=''; releaseHold.value=undefined; void load() })
}
function changeFilter(key:string, value:string) {
  void router.push({query:{...route.query,page:undefined,[key]:value || undefined}})
}
function page(offset:number) {
  void router.push({query:{...route.query,page:currentPage.value+offset}})
}
</script>

<template>
  <section class="supply-page">
    <h1>{{ t('Supply Chain') }}</h1>
    <nav class="actions supply-tabs" :aria-label="t('Supply Chain')">
      <RouterLink v-for="part in pages.filter(part=>part!=='transit'||canFinance)" :key="part" :to="'/supply/'+part" :class="{active:part===section}">{{ t(labels[part]) }}</RouterLink>
    </nav>
    <p v-if="error" class="alert error" role="alert">{{ error }}</p>
    <p v-if="notice" class="alert success" role="status">{{ notice }}</p>

    <template v-if="section==='transfers'">
      <div v-if="!entry" class="actions">
        <RouterLink v-if="sourceOptions.length" to="/supply/transfers/new" class="primary">{{ t('New transfer') }}</RouterLink>
        <label>{{ t('Status') }}<select :value="route.query.state || ''" @change="changeFilter('state',($event.target as HTMLSelectElement).value)">
          <option value="">{{ t('All') }}</option><option v-for="key in ['draft','confirmed','in_transit','partial','received','cancelled']" :key="key" :value="key">{{ status(key) }}</option>
        </select></label>
        <label><input type="checkbox" :checked="route.query.discrepancy==='pending'" @change="changeFilter('discrepancy',($event.target as HTMLInputElement).checked?'pending':'')">{{ t('Pending discrepancies') }}</label>
      </div>
      <section v-if="!entry && transfers" class="panel table-panel">
        <table><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Source site') }}</th><th>{{ t('Destination site') }}</th><th>{{ t('Status') }}</th></tr></thead>
          <tbody><tr v-for="row in transfers.results" :key="row.id"><td><RouterLink :to="'/supply/transfers/'+row.id">{{ row.number }}</RouterLink></td><td>{{ siteName(row.site_id) }}</td><td>{{ siteName(row.destination_id) }}</td><td>{{ status(row.state) }}</td></tr></tbody></table>
        <p v-if="!transfers.count">{{ t('No records found.') }}</p>
      </section>
      <RouterLink v-if="entry" to="/supply/transfers">{{ t('Stock transfers') }}</RouterLink>
      <form v-if="draftOpen" v-validated-form class="panel record-form" @submit.prevent="saveDraft">
        <h2>{{ t(entry==='new'?'New transfer':'Edit') }}</h2>
        <div class="supply-fields">
          <label>{{ t('Source site') }}<select v-model="source" data-field="site_id" required :disabled="action==='edit'" @change="lines.forEach(line=>line.source_id=undefined)"><option v-for="site in sourceOptions" :key="site.id" :value="site.id">{{ site.name }}</option></select></label>
          <label>{{ t('Destination site') }}<select v-model="destination" data-field="destination_id" required :disabled="action==='edit'" @change="lines.forEach(line=>line.destination_id=undefined)"><option v-for="[id,name] in destinations" :key="id" :value="id">{{ name }}</option></select></label>
          <label>{{ t('Note') }}<input v-model="note" data-field="note" maxlength="500"></label>
        </div>
        <fieldset v-for="(line,index) in lines" :key="index" class="supply-fields">
          <legend>{{ t('Line item') }} {{ index + 1 }}</legend>
          <EntityAutocomplete :label="t('Item')" lookup="items" :choices="catalog.items" :data-field="'lines.'+index+'.item_id'" :empty-value="undefined" required v-model="line.item_id" />
          <EntityAutocomplete :label="t('Source location')" lookup="locations" :params="{ site_id: source }" :choices="locations(source)" :data-field="'lines.'+index+'.source_id'" :empty-value="undefined" required v-model="line.source_id" />
          <EntityAutocomplete :label="t('Destination location')" lookup="locations" :params="{ site_id: destination }" :choices="locations(destination)" :data-field="'lines.'+index+'.destination_id'" :empty-value="undefined" required v-model="line.destination_id" />
          <label>{{ t('Base quantity') }}<input v-model="line.quantity" :data-field="'lines.'+index+'.quantity'" inputmode="decimal" :placeholder="t('e.g., 2.500')" required></label>
          <button v-if="lines.length>1" type="button" class="secondary" @click="lines.splice(index,1)">{{ t('Remove') }}</button>
        </fieldset>
        <div class="actions"><button type="button" class="secondary" :disabled="lines.length>=100" @click="lines.push(blank())">{{ t('Add item') }}</button>
          <button class="primary" :disabled="busy">{{ t('Save draft') }}</button>
          <button v-if="action==='edit'" class="secondary" type="button" @click="action=''">{{ t('Cancel') }}</button></div>
      </form>
      <template v-else-if="transfer">
        <section class="panel">
          <h2>{{ transfer.number }} · {{ status(transfer.state) }}</h2>
          <p>{{ t('Source site') }} {{ siteName(transfer.site_id) }} · {{ t('Destination site') }} {{ siteName(transfer.destination_id) }}</p>
          <p v-if="transfer.note">{{ transfer.note }}</p>
          <ul><li v-for="line in transfer.lines" :key="line.id">{{ line.item }} · {{ line.source }} → {{ line.destination }} · {{ line.quantity }} · {{ t('Reserved') }} {{ line.reserved }}</li></ul>
          <div class="actions">
            <template v-if="showAction('prepare') && transfer.state==='draft'">
              <button class="secondary" @click="open('edit')">{{ t('Edit') }}</button>
              <button class="primary" @click="open('confirm')">{{ t(actionLabels.confirm) }}</button>
            </template>
            <button v-if="showAction('dispatch') && transfer.lines.some(line=>Number(line.reserved)>0)" class="primary" @click="open('dispatch')">{{ t(actionLabels.dispatch) }}</button>
            <button v-if="showAction('prepare') && (transfer.state==='draft'||transfer.lines.some(line=>Number(line.reserved)>0))" class="secondary" @click="open('cancel')">{{ t(actionLabels.cancel) }}</button>
          </div>
        </section>
        <section v-for="shipment in transfer.shipments" :key="shipment.id" class="panel">
          <h3>{{ shipment.number }}</h3>
          <p>{{ new Date(shipment.dispatched_at).toLocaleString() }} · {{ shipment.carrier || '—' }} · {{ shipment.reference || '—' }} · {{ t('Expected arrival date') }} {{ shipment.eta || '—' }}</p>
          <div v-for="line in shipment.lines" :key="line.id" class="supply-line">
            <p>{{ line.item }} · {{ line.quantity }} · {{ t('In transit') }} {{ line.remaining }} / {{ line.remaining_value }} AED</p>
            <button v-if="showAction('receive')&&Number(line.remaining)>0" class="secondary" @click="open('report',shipment,line.id)">{{ t(actionLabels.report) }}</button>
            <p v-for="difference in line.discrepancies" :key="difference.id">
              {{ status(difference.kind) }} · {{ difference.quantity }} · {{ status(difference.state) }} · {{ difference.reporter }} · {{ difference.reason }}
              <span v-if="difference.reviewer"> · {{ difference.reviewer }} · {{ difference.decision_reason }}</span>
              <button v-if="showAction('settle')&&difference.state==='pending'&&difference.reporter!==workspace.username" class="secondary" @click="open('decide',shipment,difference.id)">{{ t(actionLabels.decide) }}</button>
            </p>
          </div>
          <div v-if="shipment.lines.some(line=>Number(line.remaining)>0)" class="actions">
            <button v-if="showAction('receive')" class="primary" @click="open('receive',shipment)">{{ t(actionLabels.receive) }}</button>
            <button v-if="showAction('dispatch')" class="secondary" @click="open('return',shipment)">{{ t(actionLabels.return) }}</button>
          </div>
        </section>
        <form v-if="action" v-validated-form class="panel record-form" @submit.prevent="run">
          <h3>{{ t(actionLabels[action]) }} <span v-if="activeShipment">· {{ activeShipment.number }}</span></h3>
          <div v-if="action==='dispatch'" class="supply-fields">
            <label>{{ t('Carrier') }}<input v-model="carrier" data-field="carrier" maxlength="120"></label>
            <label>{{ t('Tracking reference') }}<input v-model="reference" data-field="reference" maxlength="120"></label>
            <label>{{ t('Expected arrival date') }}<input v-model="eta" data-field="eta" type="date"></label>
          </div>
          <div v-if="['dispatch','receive','return'].includes(action)" class="supply-fields">
            <label v-for="line in actionLines" :key="line.id">{{ line.item }} ({{ line.maximum }})<input v-model="quantities[line.id]" inputmode="decimal" :placeholder="t('Base quantity')"></label>
          </div>
          <div v-if="action==='report'" class="supply-fields">
            <label>{{ t('Type') }}<select v-model="kind" data-field="kind"><option value="shortage">{{ t('Shortage') }}</option><option value="damage">{{ t('Damage') }}</option></select></label>
            <label>{{ t('Base quantity') }}<input v-model="amount" data-field="quantity" inputmode="decimal" required></label>
          </div>
          <label v-if="action==='decide'">{{ t('Decision') }}<select v-model="decision" data-field="decision"><option value="approve">{{ t('Approve settlement') }}</option><option value="dismiss">{{ t('Dismiss report') }}</option></select></label>
          <label v-if="['report','return','decide','cancel'].includes(action)">{{ t('Reason') }}<textarea v-model="reason" data-field="reason" maxlength="500" required /></label>
          <div class="actions"><button class="primary" :disabled="busy">{{ t(actionLabels[action]) }}</button><button type="button" class="secondary" @click="action=''">{{ t('Cancel') }}</button></div>
        </form>
        <section v-if="transfer.events.length" class="panel">
          <h3>{{ t('Shipment tracking') }}</h3>
          <ul><li v-for="event in transfer.events" :key="event.id">{{ new Date(event.at).toLocaleString() }} · {{ status(event.kind) }} · {{ event.quantity }} · {{ event.value }} AED <span v-if="event.entry_id"> · {{ t('Journal entry') }} #{{ event.entry_id }}</span></li></ul>
        </section>
      </template>
    </template>

    <template v-if="section==='reservations'">
      <form v-if="canHold && order && releaseHold===undefined" v-validated-form class="panel record-form" @submit.prevent="saveHold()">
        <h2>{{ t('Reserve stock') }} · {{ order.number }}</h2>
        <div class="supply-fields">
          <label>{{ t('Line item') }}<select v-model="orderLine" data-field="line_id" required><option v-for="line in order.lines" :key="line.id" :value="line.id">{{ line.name }}</option></select></label>
          <label>{{ t('Location') }}<select v-model="stockLocation" data-field="location_id" required><option v-for="loc in locations(order.site_id)" :key="loc.id" :value="loc.id">{{ loc.name }}</option></select></label>
          <label>{{ t('Base quantity') }}<input v-model="amount" data-field="quantity" inputmode="decimal" required></label>
        </div>
        <button class="primary" :disabled="busy">{{ t('Reserve stock') }}</button>
      </form>
      <form v-else-if="releaseHold!==undefined" v-validated-form class="panel record-form" @submit.prevent="saveHold(true)">
        <label>{{ t('Base quantity') }}<input v-model="amount" data-field="quantity" inputmode="decimal" required></label>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Release reservation') }}</button><button type="button" class="secondary" @click="releaseHold=undefined; amount=''">{{ t('Cancel') }}</button></div>
      </form>
      <section v-if="holds" class="panel table-panel">
        <table><thead><tr><th>{{ t('Item') }}</th><th>{{ t('Location') }}</th><th>{{ t('Reserved') }}</th><th>{{ t('Record number') }}</th><th></th></tr></thead>
          <tbody><tr v-for="row in holds.results" :key="row.id"><td>{{ row.item }}</td><td>{{ row.location }}</td><td>{{ row.quantity }}</td>
            <td><RouterLink v-if="row.transfer_id" :to="'/supply/transfers/'+row.transfer_id">{{ row.transfer }}</RouterLink><RouterLink v-else-if="row.order_id" :to="'/sales/'+row.order_id">{{ row.order }}</RouterLink><span v-else>—</span></td>
            <td><button v-if="canHold" class="secondary" @click="releaseHold=row.id; amount=row.quantity">{{ t('Release reservation') }}</button></td></tr></tbody></table>
        <p v-if="!holds.count">{{ t('No records found.') }}</p>
      </section>
    </template>

    <template v-if="section==='shipments'">
      <div class="actions">
        <label>{{ t('Search') }}<input :value="route.query.q || ''" :placeholder="t('Search by carrier or reference')" @change="changeFilter('q',($event.target as HTMLInputElement).value)"></label>
        <label>{{ t('Status') }}<select :value="route.query.state || ''" @change="changeFilter('state',($event.target as HTMLSelectElement).value)">
          <option value="">{{ t('All') }}</option><option value="in_transit">{{ status('in_transit') }}</option><option value="received">{{ status('received') }}</option>
        </select></label>
      </div>
      <section v-if="shipmentPage" class="panel table-panel">
        <table><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Carrier') }}</th><th>{{ t('Tracking reference') }}</th><th>{{ t('Expected arrival date') }}</th><th>{{ t('Status') }}</th></tr></thead>
          <tbody><tr v-for="row in shipmentPage.results" :key="row.id"><td><RouterLink :to="'/supply/transfers/'+row.transfer_id">{{ row.number }}</RouterLink></td>
            <td>{{ row.carrier || '—' }}</td><td>{{ row.reference || '—' }}</td><td>{{ row.eta || '—' }}</td><td>{{ status(row.state) }}</td></tr></tbody></table>
        <p v-if="!shipmentPage.count">{{ t('No records found.') }}</p>
      </section>
    </template>
    <template v-if="section==='transit' && transit">
      <section class="panel">
        <label>{{ t('Source site') }}<select :value="route.query.site || ''" @change="changeFilter('site',($event.target as HTMLSelectElement).value)"><option value="">{{ t('All') }}</option><option v-for="site in workspace.sites" :key="site.id" :value="site.id">{{ site.name }}</option></select></label>
        <p>{{ t('Operational in-transit value') }} {{ transit.operational_value }} AED · {{ t('Posted in-transit value') }} {{ transit.posted_value ?? '—' }} · {{ t('Difference') }} {{ transit.difference ?? '—' }}</p>
        <p><RouterLink to="/accounting/postings">{{ t('Posting queue') }}</RouterLink> · {{ transit.pending_count }}</p>
        <ul><li v-for="(row,index) in transit.items" :key="index"><RouterLink :to="'/supply/transfers/'+row.transfer_id">{{ row.shipment }}</RouterLink> · {{ row.item }} · {{ row.quantity }} · {{ row.value }} AED</li></ul>
      </section>
    </template>
    <TableToolbar v-if="!entry && section!=='transit' && pageInfo" :page="currentPage" :count="pageInfo.count" :size="Number(route.query.page_size)||25" @page="router.replace({query:{...route.query,page:String($event)}})" @size="router.replace({query:{...route.query,page_size:String($event),page:'1'}})"/>
  </section>
</template>

<style scoped>
.supply-page { display:grid; gap:1rem; min-width:0 }
.supply-tabs { border-block-end:1px solid var(--line); margin-block-end:.5rem }
.supply-tabs a { padding:.6rem .8rem; text-decoration:none }
.supply-tabs .active { border-block-end:2px solid currentColor; font-weight:700 }
.supply-fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,12rem),1fr)); gap:1rem; align-items:end }
.supply-line { margin-block:.7rem }
.panel { min-width:0 }
</style>
