<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, createRequest, type Workspace } from './api'
import { message, t } from './i18n'
import { notify } from './useUnifiedNotice'
import DateInput from './DateInput.vue'

type Entry = {id:number; name:string}
type Catalog = {purchase_orders:{id:number;number:string;site_id:number;lines:{id:number;description:string;quantity:string}[]}[];sites:Entry[]; items:(Entry&{code:string;base_unit_id:number})[]; units:{id:number;code:string}[]; locations:(Entry&{site_id:number})[]}
type Component = {item_id?:number; quantity:string; unit_id?:number; source_id?:number|null}
type Bill = {id:number;site_id:number;item_id:number;item:string;version:number;revision:number;state:string;effective:string;quantity:string;unit:string;components:Component[]}
type Material = {id:number; item:string; source_id:number;required:string;reserved:string;consumed:string;shortage:string}
type Activity = {id:number;material:string;source_id:number;action:string;quantity:string;value:string;entry_id:number|null}
type Batch = {id:number;quantity:string;held:boolean;movement_id:number|null}
type Exception = {id:number;material:string;kind:string;state:string;quantity:string;reporter:string;reason:string;decision_reason:string}
type Order = {id:number;number:string;site_id:number;item_id:number;item:string;due:string;quantity:string;state:string;revision:number;source_id:number;destination_id:number;bill_id:number|null;
  materials?:Material[];activities?:Activity[];batches?:Batch[];exceptions?:Exception[];actions?:string[];cost?:string}
type Proposal = {id:number;item:string;quantity:string;method:string;due:string;start:string;target:string|null;facts:{demand:string;late:boolean;next_supply:string|null;source_id:number|null;destination_id:number|null}}
type Plan = {id:number;site_id:number;start:string;end:string;inputs:{excluded?:{purchase_lines:number;unmapped_purchase_lines:number;shipments:number}};proposals?:Proposal[]}
type Policy = {id:number;site_id:number;item_id:number;item:string;method:string;lead_days:number;source_id:number|null;destination_id:number|null}
type List = {count:number;page:number;next:string|null;previous:string|null;results:(Bill|Order|Plan|Policy)[]}
const props = defineProps<{workspace:Workspace}>()
const route = useRoute()
const router = useRouter()
const request = createRequest()
const root = '/manufacturing/'
const tabs:Record<string,string> = {orders:'Manufacturing orders',boms:'Bills of materials',plans:'MRP runs',policies:'Planning policies'}
const section = computed(() => String(route.params.section || 'orders'))
const sectionName = computed(() => tabs[section.value])
const entry = computed(() => String(route.params.entry || ''))
const fresh = computed(() => entry.value==='new')
const catalog = ref<Catalog>({purchase_orders:[],sites:[],items:[],units:[],locations:[]})
const list = ref<List>()
const bill = ref<Bill>()
const order = ref<Order>()
const plan = ref<Plan>()
const proposal = ref<Proposal>()
const siteId = ref<number>()
const product = ref<number>()
const source = ref<number>()
const materialSource = ref<number>()
const destination = ref<number>()
const day = ref('')
const end = ref('')
const quantity = ref('')
const components = ref<Component[]>([])
const demands = ref<{item_id?:number; quantity:string;date:string}[]>([])
const purchaseDates = ref<{id:string;date:string}[]>([])
const purchaseMappings = ref<{order_id?:number;line_id?:number;item_id?:number;unit_id?:number}[]>([])
const method = ref('buy')
const lead = ref(0)
const estimate = ref('0')
const mode = ref('')
const reason = ref('')
const decision = ref('approve')
const target = ref<number>()
const busy = ref(false)
const error = ref('')
const success = ref(false)
const form = computed(() => fresh.value || mode.value==='edit')
const labels:Record<string,string> = {
  draft:'Draft',released:'Released',retired:'Retired',cancelled:'Cancelled',closed:'Closed',pending:'Pending',approved:'Approved',dismissed:'Dismissed',
  issue:'Issue material',return:'Return material',reserve:'Reserve available materials',receive:'Receive finished goods',close:'Close order',
  'change-source':'Change source location',release:'Release',retire:'Retire',edit:'Edit',cancel:'Cancel order','undo-receipt':'Reverse held receipt',decide:'Review exception',
  extra:'Extra material issue',scrap:'Production scrap',make:'Make',buy:'Buy',transfer:'Transfer',
}
const label = (value:string) => t(labels[value] || value)
const permission = (name:string, site?:number) => allows(props.workspace,'manufacturing.'+name,site)
const siteName = (site:number) => catalog.value.sites.find(row=>row.id===site)?.name || String(site)
const itemName = (id?:number) => catalog.value.items.find(row=>row.id===id)?.name || ''
const locationName = (id:number) => catalog.value.locations.find(row=>row.id===id)?.name || '#'+id
const places = (site?:number) => catalog.value.locations.filter(row => row.site_id===site)
const canStart = computed(() => catalog.value.sites.some(site=>permission(section.value==='orders'?'prepare':section.value==='boms'?'bom':'plan',site.id)))
const keys = Object.keys(tabs)
let loadVersion = 0

function reset() {
  const capability = section.value==='orders'?'prepare':section.value==='boms'?'bom':'plan'
  siteId.value = catalog.value.sites.find(row=>permission(capability,row.id))?.id
  product.value = source.value = destination.value = undefined
  day.value=end.value=quantity.value=''
  components.value=[{quantity:'',source_id:null}]
  demands.value=[]
  purchaseDates.value=[]
  purchaseMappings.value=[]
  method.value='buy'
  lead.value=0
}
async function load() {
  const version = ++loadVersion
  error.value=''
  try {
    if (!catalog.value.sites.length) catalog.value=await api<Catalog>(root+'catalog')
    if (version!==loadVersion) return
    if (fresh.value) { reset(); return }
    if (!entry.value) {
      const query = new URLSearchParams()
      for (const key of ['site','page','page_size']) if (typeof route.query[key]==='string') query.set(key,route.query[key] as string)
      const result = await api<List>(root+section.value+'?'+query)
      if (version===loadVersion) list.value=result
      return
    }
    const result = await api<Bill|Order|Plan>(root+section.value+'/'+entry.value)
    if (version!==loadVersion) return
    if (section.value==='orders') order.value=result as Order
    if (section.value==='boms') bill.value=result as Bill
    if (section.value==='plans') plan.value=result as Plan
  } catch (failure) { if (version===loadVersion) error.value=message(failure) }
}
watch(() => route.fullPath, () => {
  bill.value=order.value=plan.value=list.value=undefined
  mode.value=''
  success.value=false
  void load()
}, {immediate:true})

async function send<T>(path:string, payload:object, saved:(data:T)=>void) {
  if (busy.value) return
  busy.value=true
  error.value=''
  success.value=false
  try {
    saved(await request<T>(root+path,payload))
    success.value=true
    const isWarn = ['cancel', 'scrap', 'return'].includes(mode.value)
    notify(isWarn ? 'warning' : 'confirmation', 'Manufacturing Action Completed', 'Manufacturing order or BOM update has been saved.')
    mode.value=''
  } catch (failure) { error.value=message(failure) }
  finally { busy.value=false }
}
function open(operation:string, id?:number) {
  mode.value=operation
  target.value=id
  quantity.value=''
  reason.value=''
  decision.value='approve'
  if (operation==='change-source') materialSource.value=order.value?.materials?.find(row=>row.id===id)?.source_id
  if (operation==='edit') {
    if (bill.value) {
      const value=bill.value
      siteId.value=value.site_id; product.value=value.item_id; day.value=value.effective; quantity.value=value.quantity
      components.value=value.components.map(row=>({...row,source_id:row.source_id??null}))
    }
    if (order.value) {
      const value=order.value
      siteId.value=value.site_id; product.value=value.item_id; day.value=value.due; quantity.value=value.quantity
      source.value=value.source_id; destination.value=value.destination_id
    }
  }
}
function saveForm() {
  const data = {site_id:siteId.value,item_id:product.value,source_id:source.value,destination_id:destination.value,quantity:quantity.value,
    effective:day.value, due:day.value, components:components.value, method:method.value, lead_days:lead.value,
    start:day.value||undefined,end:end.value||undefined,requirements:demands.value,purchase_dates:Object.fromEntries(purchaseDates.value.map(row=>[row.id,row.date])), purchase_mappings:purchaseMappings.value}
  if (mode.value==='edit' && bill.value)
    return send<Bill>('boms/'+bill.value.id,{...data,action:'edit',revision:bill.value.revision},value=>{bill.value=value})
  if (mode.value==='edit' && order.value)
    return send<Order>('orders/'+order.value.id+'/edit',{...data,revision:order.value.revision},value=>{order.value=value})
  return send<{id:number}>(section.value,data,value=>{
    void router.push(root+section.value+(section.value==='policies'?'':'/'+value.id))
  })
}
function act() {
  if (bill.value) return send<Bill>('boms/'+bill.value.id,{action:mode.value,revision:bill.value.revision},value=>{bill.value=value})
  if (!order.value) return
  const report = ['scrap','extra'].includes(mode.value)
  const action = report ? 'report' : mode.value
  return send<Order>('orders/'+order.value.id+'/'+action,{
    revision:order.value.revision,material_id:target.value,source_id:action==='change-source'?materialSource.value:undefined,original_id:target.value,batch_id:target.value,
    exception_id:target.value,quantity:quantity.value,reason:reason.value,kind:mode.value,decision:decision.value,
  },value=>{order.value=value})
}
function selectProposal(row:Proposal) {
  proposal.value=row
  source.value=row.facts.source_id??undefined
  destination.value=row.facts.destination_id??undefined
  estimate.value='0'
  mode.value='convert'
}
function convert() {
  if (proposal.value) return send<Proposal>('proposals/'+proposal.value.id+'/convert',
    {source_id:source.value,destination_id:destination.value,unit_price:estimate.value},
    value=>{if (plan.value) plan.value.proposals=plan.value.proposals?.map(row=>row.id===value.id?value:row)})
}
const newTitle = computed(()=>section.value==='orders'?'New manufacturing order':section.value==='boms'?'New bill of materials':section.value==='plans'?'Run MRP':'Set planning policy')
const isQuantityAction = computed(()=>['issue','return','receive','extra','scrap'].includes(mode.value))
const needReason = computed(()=>['return','cancel','undo-receipt','extra','scrap','decide'].includes(mode.value))
</script>

<template>
  <section class="mfg-page">
    <h1>{{ t('Manufacturing') }}</h1>
    <nav class="actions mfg-tabs" :aria-label="t('Manufacturing')">
      <RouterLink v-for="key in keys" :key="key" :to="root+key" :aria-current="key===section?'page':undefined">{{ t(tabs[key]) }}</RouterLink>
    </nav>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-if="success" class="success" role="status">{{ t('Saved successfully.') }}</p>
    <RouterLink v-if="entry" :to="root+section">{{ t(sectionName) }}</RouterLink>
    <template v-if="!entry && list">
      <div class="actions">
        <RouterLink v-if="canStart" :to="root+section+'/new'" class="button primary">{{ t(newTitle) }}</RouterLink>
        <label>{{ t('Site') }}<select :value="route.query.site??''" @change="router.push({query:{site:($event.target as HTMLSelectElement).value||undefined}})">
          <option value="">{{ t('All') }}</option><option v-for="site in catalog.sites" :key="site.id" :value="site.id">{{ site.name }}</option>
        </select></label>
      </div>
      <section class="panel table-panel">
        <table><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Site') }}</th><th>{{ t('Item') }}</th><th>{{ t('Status') }}</th></tr></thead>
          <tbody><tr v-for="row in list.results" :key="row.id">
            <td><RouterLink v-if="section!=='policies'" :to="root+section+'/'+row.id">{{ 'number' in row ? row.number : '#'+row.id }}</RouterLink><span v-else>#{{ row.id }}</span></td>
            <td>{{ siteName(row.site_id) }}</td><td>{{ 'item' in row ? row.item : row.start+' → '+row.end }}</td>
            <td>{{ 'state' in row ? label(row.state) : 'method' in row ? label(row.method) : '—' }}</td>
          </tr></tbody>
        </table>
        <p v-if="!list.results.length">{{ t('No records found.') }}</p>
        <TableToolbar :page="list.page" :count="list.count" :size="Number(route.query.page_size)||25" @page="router.replace({query:{...route.query,page:String($event)}})" @size="router.replace({query:{...route.query,page_size:String($event),page:'1'}})"/>
      </section>
    </template>

    <form v-if="form" v-validated-form class="panel record-form" @submit.prevent="saveForm">
      <h2>{{ mode==='edit'?t('Edit'):t(newTitle) }}</h2>
      <div class="mfg-fields">
        <label>{{ t('Site') }}<select v-model.number="siteId" data-field="site_id" required :disabled="mode==='edit'" @change="source=destination=undefined; components.forEach(component=>component.source_id=null)">
          <option v-for="site in catalog.sites" :key="site.id" :value="site.id" :disabled="!permission(section==='boms'?'bom':section==='orders'?'prepare':'plan',site.id)">{{ site.name }}</option>
        </select></label>
        <EntityAutocomplete v-if="section!=='plans'" :label="t('Item')" lookup="items" :choices="catalog.items" data-field="item_id" :disabled="mode==='edit'&&section==='boms'" :empty-value="undefined" required v-model="product" />
        <DateInput v-if="section!=='policies'" :label="t(section==='boms'?'Effective date':section==='orders'?'Due date':'Start date')" v-model="day" type="date"
          :required="section!=='plans'" :data-field="section==='boms'?'effective':section==='orders'?'due':'start'" />
        <DateInput v-if="section==='plans'" :label="t('End date')" v-model="end" type="date" data-field="end" />
        <label v-if="['boms','orders'].includes(section)">{{ t('Base quantity') }}<input v-model="quantity" data-field="quantity" inputmode="decimal" required :placeholder="t('e.g., 2.500')"></label>
        <label v-if="section==='policies'">{{ t('Replenishment method') }}<select v-model="method" data-field="method"><option v-for="value in ['make','buy','transfer']" :key="value" :value="value">{{ label(value) }}</option></select></label>
        <label v-if="section==='policies'">{{ t('Lead time in days') }}<input v-model.number="lead" data-field="lead_days" type="number" min="0" max="365" required></label>
        <template v-if="section==='orders'||(section==='policies'&&method!=='buy')">
          <label>{{ t('Source location') }}<select v-model.number="source" data-field="source_id" required><option v-for="loc in (method==='transfer'&&section==='policies'?catalog.locations.filter(value=>value.site_id!==siteId):places(siteId))"
            :key="loc.id" :value="loc.id">{{ siteName(loc.site_id) }} · {{ loc.name }}</option></select></label>
          <label>{{ t('Destination location') }}<select v-model.number="destination" data-field="destination_id" required><option v-for="loc in places(siteId)" :key="loc.id" :value="loc.id">{{ loc.name }}</option></select></label>
        </template>
      </div>
      <fieldset v-for="(row,index) in section==='boms'?components:[]" :key="index" class="mfg-fields">
        <legend>{{ t('Component') }} {{ index+1 }}</legend>
        <EntityAutocomplete :label="t('Item')" lookup="items" :choices="catalog.items.filter(value=>value.id!==product)" :data-field="'components.'+index+'.item_id'" :empty-value="undefined" required v-model="row.item_id" />
        <label>{{ t('Unit') }}<select v-model.number="row.unit_id" :data-field="'components.'+index+'.unit_id'"><option :value="undefined">{{ t('Base unit') }}</option><option v-for="unit in catalog.units" :key="unit.id" :value="unit.id">{{ unit.code }}</option></select></label>
        <label>{{ t('Quantity') }}<input v-model="row.quantity" :data-field="'components.'+index+'.quantity'" inputmode="decimal" required :placeholder="t('e.g., 2.500')"></label>
        <label>{{ t('Source location') }}<select v-model.number="row.source_id" :data-field="'components.'+index+'.source_id'">
          <option :value="null">{{ t('Use order source location') }}</option>
          <option v-for="loc in places(siteId)" :key="loc.id" :value="loc.id">{{ loc.name }}</option>
        </select></label>
        <button v-if="components.length>1" type="button" class="secondary" @click="components.splice(index,1)">{{ t('Remove') }}</button>
      </fieldset>
      <button v-if="section==='boms'" type="button" class="secondary" :disabled="components.length>=100" @click="components.push({quantity:'',source_id:null})">{{ t('Add component') }}</button>
      <template v-if="section==='plans'">
        <fieldset v-for="(row,index) in demands" :key="index" class="mfg-fields">
          <legend>{{ t('Manual requirement') }} {{ index+1 }}</legend>
          <label>{{ t('Item') }}<select v-model.number="row.item_id" required><option v-for="item in catalog.items" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
          <label>{{ t('Base quantity') }}<input v-model="row.quantity" required inputmode="decimal" :placeholder="t('e.g., 2.500')"></label>
          <DateInput :label="t('Due date')" v-model="row.date" type="date" required />
          <button type="button" class="secondary" @click="demands.splice(index,1)">{{ t('Remove') }}</button>
        </fieldset>
        <fieldset v-for="(row,index) in purchaseDates" :key="index" class="mfg-fields">
          <legend>{{ t('Purchase order arrival') }} {{ index+1 }}</legend>
          <label>{{ t('Purchase order') }}<select v-model="row.id" required><option v-for="purchase in catalog.purchase_orders.filter(value=>value.site_id===siteId)"
            :key="purchase.id" :value="String(purchase.id)">{{ purchase.number }}</option></select></label>
          <DateInput :label="t('Expected arrival date')" v-model="row.date" type="date" required />
          <button type="button" class="secondary" @click="purchaseDates.splice(index,1)">{{ t('Remove') }}</button>
        </fieldset>
        <fieldset v-for="(row,index) in purchaseMappings" :key="index" class="mfg-fields">
          <legend>{{ t('Purchase line mapping') }} {{ index+1 }}</legend>
          <label>{{ t('Purchase order') }}<select v-model.number="row.order_id" required @change="row.line_id=undefined"><option v-for="po in catalog.purchase_orders.filter(value=>value.site_id===siteId)" :key="po.id" :value="po.id">{{ po.number }}</option></select></label>
          <label>{{ t('Line item') }}<select v-model.number="row.line_id" required><option v-for="line in catalog.purchase_orders.find(po=>po.id===row.order_id)?.lines" :key="line.id" :value="line.id">{{ line.description }} · {{ line.quantity }}</option></select></label>
          <label>{{ t('Item') }}<select v-model.number="row.item_id" required><option v-for="item in catalog.items" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
          <label>{{ t('Unit') }}<select v-model.number="row.unit_id"><option :value="undefined">{{ t('Base unit') }}</option><option v-for="unit in catalog.units" :key="unit.id" :value="unit.id">{{ unit.code }}</option></select></label>
          <button type="button" class="secondary" @click="purchaseMappings.splice(index,1)">{{ t('Remove') }}</button>
        </fieldset>
        <div class="actions"><button type="button" class="secondary" @click="demands.push({quantity:'',date:''})">{{ t('Add requirement') }}</button>
          <button type="button" class="secondary" @click="purchaseDates.push({id:'',date:''})">{{ t('Add purchase arrival') }}</button><button type="button" class="secondary" @click="purchaseMappings.push({})">{{ t('Add purchase line mapping') }}</button></div>
      </template>
      <div class="actions"><button class="primary" :disabled="busy">{{ t(section==='plans'?'Run MRP':'Save') }}</button>
        <button v-if="mode==='edit'" type="button" class="secondary" @click="mode=''">{{ t('Cancel') }}</button>
        <RouterLink v-else :to="root+section">{{ t('Cancel') }}</RouterLink></div>
    </form>

    <section v-if="!form&&bill" class="panel">
      <h2>{{ bill.item }} · {{ t('Version') }} {{ bill.version }} · {{ label(bill.state) }}</h2>
      <p>{{ siteName(bill.site_id) }} · {{ bill.effective }} · {{ bill.quantity }} {{ bill.unit }}</p>
      <ul><li v-for="row in bill.components" :key="row.item_id">{{ itemName(row.item_id) }} · {{ row.quantity }} {{ catalog.units.find(unit=>unit.id===row.unit_id)?.code }} · {{ row.source_id ? locationName(row.source_id) : t('Use order source location') }}</li></ul>
      <div v-if="permission('bom',bill.site_id)" class="actions">
        <button v-if="bill.state==='draft'" class="secondary" @click="open('edit')">{{ t('Edit') }}</button>
        <button v-if="bill.state==='draft'" class="primary" @click="open('release')">{{ label('release') }}</button>
        <button v-if="bill.state==='released'" class="secondary" @click="open('retire')">{{ label('retire') }}</button>
      </div>
    </section>

    <template v-if="!form&&order">
      <section class="panel">
        <h2>{{ order.number }} · {{ label(order.state) }}</h2>
        <p>{{ order.item }} · {{ order.quantity }} · {{ siteName(order.site_id) }} · {{ order.due }}</p>
        <p v-if="order.bill_id"><RouterLink :to="root+'boms/'+order.bill_id">{{ t('Bills of materials') }} #{{ order.bill_id }}</RouterLink> · {{ t('Net material cost') }} {{ order.cost }} AED</p>
        <div class="actions">
          <template v-if="order.actions?.includes('prepare')">
            <button v-if="order.state==='draft'" class="secondary" @click="open('edit')">{{ t('Edit') }}</button>
            <button v-if="order.state==='draft'" class="primary" @click="open('release')">{{ label('release') }}</button>
            <button v-if="order.state==='released'" class="secondary" @click="open('reserve')">{{ label('reserve') }}</button>
            <button v-if="order.state==='released'" class="primary" @click="open('close')">{{ label('close') }}</button>
            <button v-if="['released','draft'].includes(order.state)" class="secondary" @click="open('cancel')">{{ label('cancel') }}</button>
          </template>
          <button v-if="order.state==='released'&&order.actions?.includes('execute')" class="primary" @click="open('receive')">{{ label('receive') }}</button>
        </div>
      </section>
      <section v-if="order.materials?.length" class="panel table-panel">
        <h3>{{ t('Production materials') }}</h3>
        <table><thead><tr><th>{{ t('Item') }}</th><th>{{ t('Source location') }}</th><th>{{ t('Required') }}</th><th>{{ t('Reserved') }}</th><th>{{ t('Consumed') }}</th><th>{{ t('Shortage') }}</th><th>{{ t('Actions') }}</th></tr></thead>
          <tbody><tr v-for="row in order.materials" :key="row.id">
            <td>{{ row.item }}</td><td>{{ locationName(row.source_id) }}</td><td>{{ row.required }}</td><td>{{ row.reserved }}</td><td>{{ row.consumed }}</td><td>{{ row.shortage }}</td>
            <td><div v-if="order.state==='released'" class="actions">
              <button v-if="order.actions?.includes('prepare')" class="secondary" @click="open('change-source',row.id)">{{ label('change-source') }}</button>
              <template v-if="order.actions?.includes('execute')">
                <button class="secondary" @click="open('issue',row.id)">{{ label('issue') }}</button>
                <button class="secondary" @click="open('extra',row.id)">{{ label('extra') }}</button>
              </template>
            </div></td>
          </tr></tbody></table>
      </section>
      <section v-if="order.batches?.length" class="panel">
        <h3>{{ t('Production batches') }}</h3>
        <ul><li v-for="row in order.batches" :key="row.id">{{ t('Batch') }} #{{ row.id }} · {{ row.quantity }} · {{ t(row.held?'Held until close':'In stock') }}
          <button v-if="row.held&&order.actions?.includes('execute')" class="secondary" @click="open('undo-receipt',row.id)">{{ label('undo-receipt') }}</button></li></ul>
      </section>
      <section v-if="order.activities?.length" class="panel">
        <h3>{{ t('Material movements') }}</h3>
        <ul><li v-for="row in order.activities" :key="row.id">#{{ row.id }} · {{ row.material }} · {{ label(row.action) }} · {{ locationName(row.source_id) }} · {{ row.quantity }} · {{ row.value }} AED
          <span v-if="row.entry_id"> · {{ t('Journal entry') }} #{{ row.entry_id }}</span>
          <span v-if="row.action==='issue'&&order.state==='released'&&order.actions?.includes('execute')" class="actions">
            <button class="secondary" @click="open('return',row.id)">{{ label('return') }}</button>
            <button class="secondary" @click="open('scrap',row.id)">{{ label('scrap') }}</button>
          </span></li></ul>
      </section>
      <section v-if="order.exceptions?.length" class="panel">
        <h3>{{ t('Production exceptions') }}</h3>
        <ul><li v-for="row in order.exceptions" :key="row.id">{{ row.material }} · {{ label(row.kind) }} · {{ row.quantity }} · {{ label(row.state) }} · {{ row.reason }}
          <span v-if="row.decision_reason"> · {{ row.decision_reason }}</span>
          <button v-if="row.state==='pending'&&row.reporter!==workspace.username&&order.actions?.includes('approve')" class="secondary" @click="open('decide',row.id)">{{ label('decide') }}</button></li></ul>
      </section>
    </template>

    <form v-if="(order||bill)&&mode&&mode!=='edit'" v-validated-form class="panel record-form" @submit.prevent="act">
      <h3>{{ label(mode) }}</h3>
      <template v-if="order && ['change-source','issue','extra'].includes(mode)">
        <p>{{ order.materials?.find(row=>row.id===target)?.item }}</p>
        <label v-if="mode==='change-source'">{{ t('Source location') }}<select v-model.number="materialSource" data-field="source_id" required>
          <option v-for="loc in places(order.site_id)" :key="loc.id" :value="loc.id">{{ loc.name }}</option>
        </select></label>
        <p v-else>{{ t('Source location') }} · {{ locationName(order.materials?.find(row=>row.id===target)?.source_id ?? order.source_id) }}</p>
      </template>
      <label v-if="isQuantityAction">{{ t('Base quantity') }}<input v-model="quantity" required data-field="quantity" inputmode="decimal" :placeholder="t('e.g., 2.500')"></label>
      <label v-if="mode==='decide'">{{ t('Decision') }}<select v-model="decision"><option value="approve">{{ t('Approve') }}</option><option value="dismiss">{{ t('Dismiss report') }}</option></select></label>
      <label v-if="needReason||mode==='close'">{{ t('Reason') }}<textarea v-model="reason" :required="needReason" data-field="reason" maxlength="500" /></label>
      <div class="actions"><button class="primary" :disabled="busy">{{ label(mode) }}</button><button type="button" class="secondary" @click="mode=''">{{ t('Cancel') }}</button></div>
    </form>

    <template v-if="plan">
      <section class="panel table-panel">
        <h2>{{ t('MRP runs') }} #{{ plan.id }}</h2><p>{{ siteName(plan.site_id) }} · {{ plan.start }} → {{ plan.end }}</p>
        <p v-if="plan.inputs.excluded">{{ t('Purchase lines without an arrival date') }} {{ plan.inputs.excluded.purchase_lines }} · {{ t('Unmapped purchase lines') }} {{ plan.inputs.excluded.unmapped_purchase_lines }} · {{ t('Shipments without an arrival date') }} {{ plan.inputs.excluded.shipments }}</p>
        <table><thead><tr><th>{{ t('Item') }}</th><th>{{ t('Quantity') }}</th><th>{{ t('Replenishment method') }}</th><th>{{ t('Due date') }}</th><th>{{ t('Start date') }}</th><th>{{ t('Next expected supply') }}</th><th>{{ t('Demand source') }}</th><th>{{ t('Actions') }}</th></tr></thead>
          <tbody><tr v-for="row in plan.proposals" :key="row.id">
            <td>{{ row.item }}</td><td>{{ row.quantity }}</td><td>{{ label(row.method) }}</td><td>{{ row.due }}</td><td>{{ row.start }} <span v-if="row.facts.late">{{ t('Late') }}</span></td>
            <td>{{ row.facts.next_supply || '—' }}</td><td>{{ row.facts.demand }}</td>
            <td><RouterLink v-if="row.target" :to="row.target">{{ t('Open draft') }}</RouterLink><button v-else-if="permission('plan',plan.site_id)" class="secondary" @click="selectProposal(row)">{{ t('Create draft') }}</button></td>
          </tr></tbody></table>
        <p v-if="!plan.proposals?.length">{{ t('No shortages in this plan.') }}</p>
      </section>
      <form v-if="mode==='convert'&&proposal" v-validated-form class="panel record-form" @submit.prevent="convert">
        <h3>{{ t('Create draft') }} · {{ proposal.item }} · {{ label(proposal.method) }}</h3>
        <label v-if="proposal.method==='buy'">{{ t('Estimated unit price') }}<input v-model="estimate" data-field="unit_price" required inputmode="decimal"></label>
        <div v-else class="mfg-fields">
          <label>{{ t('Source location') }}<select v-model.number="source" data-field="source_id" required><option v-for="loc in proposal.method==='transfer'?catalog.locations.filter(value=>value.site_id!==plan?.site_id):places(plan.site_id)" :key="loc.id" :value="loc.id">{{ siteName(loc.site_id) }} · {{ loc.name }}</option></select></label>
          <label>{{ t('Destination location') }}<select v-model.number="destination" data-field="destination_id" required><option v-for="loc in places(plan.site_id)" :key="loc.id" :value="loc.id">{{ loc.name }}</option></select></label>
        </div>
        <div class="actions"><button class="primary" :disabled="busy">{{ t('Create draft') }}</button><button type="button" class="secondary" @click="mode=''">{{ t('Cancel') }}</button></div>
      </form>
    </template>
  </section>
</template>

<style scoped>
.mfg-page { display:grid; gap:1rem; min-inline-size:0 }
.mfg-tabs { border-block-end:1px solid var(--line); padding-block-end:.25rem }
.mfg-tabs a { padding:.55rem .8rem; text-decoration:none }
.mfg-tabs a[aria-current] { border-block-end:2px solid currentColor; font-weight:700 }
.mfg-fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,12rem),1fr)); align-items:end; gap:1rem }
</style>
