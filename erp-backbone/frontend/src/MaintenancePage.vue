<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, requestKey, type Asset, type Workspace } from './api'
import { message, t } from './i18n'

type Spare = {item_id: number; quantity: string}
type Job = {id: number; title: string; stage: string; completed_at: string | null; due_date: string | null; net: string}
type Plan = {id: number; asset_id: number; asset: string; title: string; first_due: string; next_due: string | null;
  interval: number; frequency: string; active: boolean; due: boolean; overdue: boolean; previous_open: boolean; asset_retired: boolean;
  checklist: string[]; spare_parts: Spare[]; revision: number}
type Profile = Asset & { equipment_type: string; functional_location: string; state: string; parent_id: number | null;
  spare_parts: Spare[]; revision: number; plans: Plan[]; orders: Job[]; general_orders: {id:number;title:string;status:string}[] }
const {workspace} = defineProps<{workspace: Workspace}>()
const route = useRoute()
const router = useRouter()
const assetId = computed(() => Number(route.params.id) || 0)
const assets = ref<Asset[]>([])
const plans = ref<Plan[]>([])
const profile = ref<Profile>()
const items = ref<{id: number; code: string; name: string; active: boolean}[]>([])
const stageLabel: Record<string,string> = {waiting:'Awaiting execution', in_progress:'In progress', completed:'Completed', closed:'Closed', cancelled:'Cancelled'}
const busy = ref(false)
const error = ref('')
const notice = ref('')
const form = ref<Partial<Plan>>()
const checklist = ref('')
const correct = ref('')
const partRows = ref<Spare[]>([])
const acknowledge = ref<Record<number, boolean>>({})
const permissions = (site?: number | null) => allows(workspace, 'maintenance.plan', site)
const activeAssets = computed(() => assets.value.filter(row => row.site_id !== null && permissions(row.site_id)))
const related = computed(() => profile.value?.plans ?? plans.value)
const latest = computed(() => profile.value?.orders.map(row => row.completed_at).filter(Boolean).sort().at(-1))
const next = computed(() => related.value.filter(row => row.active && !row.asset_retired && row.next_due)
  .map(row => row.next_due!).sort()[0])
let replay: {input: string; key: string} | undefined

async function operation(path: string, data: object) {
  const input = JSON.stringify([path, data])
  if (replay?.input !== input) replay = {input, key: requestKey()}
  return api<{id: number}>(path, 'POST', data, replay.key)
}
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try { await action() } catch (failure) { error.value = message(failure) } finally { busy.value = false }
}
async function load() {
  const id = assetId.value
  profile.value = undefined
  form.value = undefined
  correct.value = ''
  try {
    const [assetRows, planRows, result] = await Promise.all([
      api<{items: Asset[]}>('/assets'), api<{items: Plan[]}>('/maintenance/plans'),
      id ? api<Profile>('/maintenance/assets/' + id) : Promise.resolve(undefined),
    ])
    if (id !== assetId.value) return
    assets.value = assetRows.items.filter(row => row.site_id !== null && allows(workspace, 'maintenance.view', row.site_id)
      && allows(workspace, 'workorders.view', row.site_id))
    plans.value = planRows.items
    profile.value = result
    if (allows(workspace, 'inventory.view') && workspace.modules.some(row => row.id === 'inventory' && row.enabled)) {
      items.value = (await api<{items: typeof items.value}>('/inventory/catalog')).items.filter(row => row.active)
    }
  } catch (failure) { error.value = message(failure) }
}
watch([profile, () => route.hash], ([row, hash]) => {
  if (row && hash === '#new-corrective') document.getElementById('new-corrective')?.scrollIntoView()
}, {flush: 'post'})
watch(assetId, () => { void load() }, {immediate: true})
function edit(plan?: Plan) {
  form.value = plan ? {...plan} : {asset_id: assetId.value || undefined, title: '', interval: 1, frequency: 'months',
    active: true, first_due: ''}
  checklist.value = plan?.checklist.join('\n') ?? ''
  partRows.value = (plan?.spare_parts ?? profile.value?.spare_parts ?? []).map(row => ({...row}))
}
function save() {
  return run(async () => {
    const draft = form.value!
    const data = {...draft, checklist: checklist.value.split('\n').map(s => s.trim()).filter(Boolean), spare_parts: partRows.value}
    if (draft.id) await api('/maintenance/plans/' + draft.id, 'PATCH', data)
    else await operation('/maintenance/plans', data)
    await load()
    notice.value = 'Changes saved.'
  })
}
function saveProfile() {
  return run(async () => {
    await api('/maintenance/assets/' + assetId.value, 'PATCH', profile.value!)
    await load()
    notice.value = 'Changes saved.'
  })
}
function generate(row: Plan) {
  return run(async () => {
    const job = await operation('/maintenance/plans/' + row.id + '/generate',
      {due_date: row.next_due, acknowledge_open: Boolean(acknowledge.value[row.id])})
    await router.push('/work-orders/' + job.id)
  })
}
function corrective() {
  return run(async () => {
    const job = await operation('/maintenance/corrective', {asset_id: assetId.value, title: correct.value})
    await router.push('/work-orders/' + job.id)
  })
}
import DateInput from './DateInput.vue'
</script>

<template>
  <RouterLink v-if="assetId" class="back-link" to="/maintenance">{{ t('Maintenance') }}</RouterLink>
  <h1>{{ profile ? profile.tag + ' · ' + profile.name : t(route.query.tab === 'plans' ? 'Preventive maintenance' : 'Maintenance') }}</h1>
  <p v-if="error" role="alert" class="alert">{{ t(error) }}</p>
  <p v-if="notice" role="status" class="success">{{ t(notice) }}</p>
  <section v-if="!assetId && route.query.tab !== 'plans'" class="panel record-form">
    <h2>{{ t('Assets') }}</h2>
    <ul><li v-for="asset in assets" :key="asset.id"><RouterLink :to="'/maintenance/assets/' + asset.id">{{ asset.tag }} · {{ asset.name }}</RouterLink></li></ul>
  </section>
  <template v-if="profile">
    <form v-validated-form class="panel record-form" @submit.prevent="saveProfile">
      <h2>{{ t('Asset details') }}</h2>
      <div class="field-wrap"><label>{{ t('Equipment type') }}<input data-field="equipment_type" :placeholder="t('Enter {field}').replace('{field}', t('Equipment type'))" v-model="profile.equipment_type" maxlength="100" :readonly="!permissions(profile.site_id)"></label><FieldHint :label="t('Equipment type')" :readonly="!permissions(profile.site_id)" /></div>
      <div class="field-wrap"><label>{{ t('Functional location') }}<input data-field="functional_location" :placeholder="t('Enter {field}').replace('{field}', t('Functional location'))" v-model="profile.functional_location" maxlength="120" :readonly="!permissions(profile.site_id)"></label><FieldHint :label="t('Functional location')" :readonly="!permissions(profile.site_id)" /></div>
      <div class="field-wrap"><label>{{ t('Status') }}<select data-field="state" v-model="profile.state" :disabled="!permissions(profile.site_id)">
        <option v-for="key in ['active','out_of_service','retired']" :key="key" :value="key">{{ t(key) }}</option></select></label><FieldHint :label="t('Status')" /></div>
      <div class="field-wrap"><label>{{ t('Parent asset') }}<select data-field="parent_id" v-model="profile.parent_id" :disabled="!permissions(profile.site_id)"><option :value="null">—</option>
        <option v-for="asset in assets.filter(a => a.site_id === profile?.site_id && a.id !== assetId)" :key="asset.id" :value="asset.id">{{ asset.tag }} · {{ asset.name }}</option>
      </select></label><FieldHint :label="t('Parent asset')" /></div>
      <h3>{{ t('Suggested spare parts') }}</h3>
      <div v-for="(part, index) in profile.spare_parts" :key="index" class="actions">
        <div class="field-wrap"><label>{{ t('Item') }}<select :data-field="'profile.spare_parts.' + index + '.item_id'" v-model="part.item_id" required :disabled="!permissions(profile.site_id)">
          <option v-if="!items.some(i => i.id === part.item_id)" :value="part.item_id">#{{ part.item_id }}</option>
          <option v-for="item in items" :key="item.id" :value="item.id">{{ item.code }} · {{ item.name }}</option></select></label><FieldHint :label="t('Item')" required /></div>
        <div class="field-wrap"><label>{{ t('Quantity') }}<input :data-field="'profile.spare_parts.' + index + '.quantity'" :placeholder="t('e.g., 2.500')" v-model="part.quantity" required :readonly="!permissions(profile.site_id)"></label><FieldHint :label="t('Quantity')" required :readonly="!permissions(profile.site_id)" /></div>
        <button v-if="permissions(profile.site_id)" type="button" class="secondary" @click="profile.spare_parts.splice(index, 1)">{{ t('Remove') }}</button>
      </div>
      <div v-if="permissions(profile.site_id)" class="actions">
        <button v-if="items.length" type="button" class="secondary" @click="profile.spare_parts.push({item_id: items[0]!.id, quantity: '1'})">{{ t('Add spare part') }}</button>
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
      </div>
    </form>
    <section class="panel record-form approval-section">
      <h2>{{ t('Maintenance history') }}</h2>
      <p>{{ t('Last completed') }} · {{ latest?.slice(0,10) ?? '—' }} · {{ t('Next due') }} · {{ next ?? '—' }}</p>
      <ul><li v-for="job in profile.orders" :key="job.id"><RouterLink :to="'/work-orders/' + job.id">WO-{{ job.id }}</RouterLink>
        · {{ job.title }} · {{ t(stageLabel[job.stage] ?? job.stage) }} · {{ job.net }} AED</li>
        <li v-for="job in profile.general_orders" :key="job.id"><RouterLink :to="'/work-orders/' + job.id">WO-{{ job.id }}</RouterLink>
          · {{ job.title }} · {{ t(job.status) }}</li></ul>
      <form v-validated-form v-if="permissions(profile.site_id) && allows(workspace, 'workorders.create', profile.site_id) && profile.state !== 'retired'" id="new-corrective" class="actions" @submit.prevent="corrective">
        <div class="field-wrap"><label>{{ t('Corrective work order title') }}<input data-field="correct" :placeholder="t('Enter {field}').replace('{field}', t('Corrective work order title'))" v-model="correct" required maxlength="120"></label><FieldHint :label="t('Corrective work order title')" required /></div>
        <button class="secondary" :disabled="busy">{{ t('New corrective work order') }}</button>
      </form>
    </section>
  </template>
  <section class="panel record-form approval-section">
    <h2>{{ t('Preventive maintenance') }}</h2>
    <button v-if="activeAssets.length && !form" class="secondary" @click="edit()">{{ t('New PM plan') }}</button>
    <form v-validated-form v-if="form" class="record-form" @submit.prevent="save">
      <EntityAutocomplete data-field="asset_id" :label="t('Asset')" lookup="assets" :choices="activeAssets" :disabled="Boolean(form.id)" :empty-value="undefined" required v-model="form.asset_id" />
      <div class="field-wrap"><label>{{ t('Title') }}<input data-field="title" :placeholder="t('Enter a short, descriptive title')" v-model="form.title" required maxlength="120"></label><FieldHint :label="t('Title')" required /></div>
      <DateInput data-field="first_due" :label="t('First due')" v-model="form.first_due" type="date" required />
      <div class="field-wrap"><label>{{ t('Interval') }}<input data-field="interval" :placeholder="t('Enter {field}').replace('{field}', t('Interval'))" v-model.number="form.interval" type="number" min="1" max="366" required></label><FieldHint :label="t('Interval')" required /></div>
      <div class="field-wrap"><label>{{ t('Frequency') }}<select data-field="frequency" v-model="form.frequency"><option v-for="freq in ['days','weeks','months']" :key="freq" :value="freq">{{ t(freq) }}</option></select></label><FieldHint :label="t('Frequency')" /></div>
      <div class="field-wrap"><label>{{ t('Checklist (one step per line)') }}<textarea data-field="checklist" :placeholder="t('Enter details')" v-model="checklist" rows="4" required></textarea></label><FieldHint :label="t('Checklist (one step per line)')" required /></div>
      <div class="field-wrap"><label class="delegation-check"><input data-field="active" v-model="form.active" type="checkbox">{{ t('Active') }}</label><FieldHint :label="t('Active')" /></div>
      <h3>{{ t('Suggested spare parts') }}</h3>
      <div v-for="(part,index) in partRows" :key="index" class="actions">
        <EntityAutocomplete :data-field="'partRows.' + index + '.item_id'" :label="t('Item')" lookup="items" :choices="items" :empty-value="undefined" required v-model="part.item_id" />
        <div class="field-wrap"><label>{{ t('Quantity') }}<input :data-field="'partRows.' + index + '.quantity'" :placeholder="t('e.g., 2.500')" v-model="part.quantity" required></label><FieldHint :label="t('Quantity')" required /></div>
        <button class="secondary" type="button" @click="partRows.splice(index,1)">{{ t('Remove') }}</button>
      </div>
      <div class="actions">
        <button v-if="items.length" class="secondary" type="button" @click="partRows.push({item_id: items[0]!.id, quantity:'1'})">{{ t('Add spare part') }}</button>
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
        <button class="secondary" type="button" @click="form = undefined">{{ t('Cancel') }}</button>
      </div>
    </form>
    <ul>
      <li v-for="row in related" :key="row.id">
        <strong>{{ row.title }}</strong> · {{ row.asset }} · {{ t('Next due') }} · {{ row.next_due ?? '—' }}
        <span v-if="!row.active"> · {{ t('Paused') }}</span>
        <span v-else-if="row.due && !row.asset_retired"> · {{ t(row.overdue ? 'Overdue' : 'Due') }}</span>
        <div v-if="assets.some(a => a.id === row.asset_id && permissions(a.site_id))" class="actions">
          <button class="secondary" :disabled="busy" @click="edit(row)">{{ t('Edit') }}</button>
          <div class="field-wrap" v-if="row.previous_open && row.due && !row.asset_retired"><label class="delegation-check"><input v-model="acknowledge[row.id]" type="checkbox">{{ t('Previous work order is still open') }}</label><FieldHint :label="t('Previous work order is still open')" /></div>
          <button v-if="row.due && !row.asset_retired" class="primary" :disabled="busy || (row.previous_open && !acknowledge[row.id])" @click="generate(row)">{{ t('Generate due work orders') }}</button>
        </div>
      </li>
    </ul>
  </section>
</template>
