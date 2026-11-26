<script setup lang="ts">
import HistoryTable from "./HistoryTable.vue"
import { computed, ref, watch } from 'vue'
import { allows, api, requestKey, type Workspace } from './api'
import { message, t } from './i18n'
import { notify } from './useUnifiedNotice'
import WorkOrderTime from './WorkOrderTime.vue'

type Job = {asset_id: number; asset: string; status: string; stage: string; revision: number; plan_id: number | null; due_date: string | null;
  cost_center_id:number|null; center_required:boolean; centers:{id:number;name:string}[];
  assignee_id: number | null; assignee: string; is_assignee: boolean; technicians: {id: number; name: string}[];
  checklist: string[]; results: string[]; findings: string; close_note: string; started_at: string | null; completed_at: string | null; closed_at: string | null;
  spare_parts: {item_id: number; quantity: string; item?: string}[]; costs: {issued: string; returned: string; net: string};
  movements: {id: number; kind: string; quantity: string; value: string; item: string; location: string; original_id: number | null;
    posting: string; entry_id: number | null}[]}
type Catalog = {items: {id:number; code:string; name:string; active:boolean; base_unit_id:number}[];
  units: {id:number;code:string}[]; warehouses: {id:number;site_id:number;code:string}[];
  locations: {id:number;warehouse_id:number;code:string}[]}
const props = defineProps<{id: number; site: number; orderRevision: number; workspace: Workspace}>()
const emit = defineEmits<{changed:[]}>()
const job = ref<Job>()
const inventory = ref<Catalog>()
const stageLabel: Record<string,string> = {waiting:'Awaiting execution', in_progress:'In progress', completed:'Completed', closed:'Closed', cancelled:'Cancelled'}
const busy = ref(false)
const error = ref('')
const notice = ref('')
const assignee = ref<number>()
const center = ref<number|null>(null)
const findings = ref('')
const results = ref<string[]>([])
const reason = ref('')
const item = ref<number>()
const location = ref<number>()
const original = ref<number>()
const issueQty = ref('')
const returnQty = ref('')
const allowed = (action: string) => allows(props.workspace, 'maintenance.' + action, props.site)
const canStock = computed(() => allowed('parts') && allows(props.workspace, 'inventory.view', props.site) &&
  props.workspace.modules.some(m => m.id === 'inventory' && m.enabled))
const canIssue = computed(() => canStock.value && job.value?.stage === 'in_progress' &&
  (!job.value.center_required || Boolean(job.value.cost_center_id)))
const canReturn = computed(() => canStock.value && ['in_progress','completed'].includes(job.value?.stage ?? '')
  && job.value?.movements.some(row => row.kind === 'maint_issue'))
const canReadAsset = computed(() => allows(props.workspace, 'assets.view', props.site))
const locations = computed(() => inventory.value?.locations.filter(row =>
  inventory.value?.warehouses.some(wh => wh.id === row.warehouse_id && wh.site_id === props.site)) ?? [])
const label = (id: number) => inventory.value?.items.find(row => row.id === id)?.name ?? '#' + id
const unitCode = (id: number) => inventory.value?.units.find(u => u.id === id)?.code ?? ''
let replay: {value: string; key: string} | undefined

async function load() {
  try {
    const path = '/maintenance/orders/' + props.id
    const data = await api<Job>(path)
    job.value = data
    assignee.value = data.assignee_id ?? undefined
    center.value = data.cost_center_id
    findings.value = data.findings
    results.value = data.checklist.map((_, i) => data.results[i] ?? '')
    if (canStock.value && !inventory.value) inventory.value = await api<Catalog>('/inventory/catalog')
  } catch (failure) { error.value = message(failure) }
}
watch(() => props.orderRevision, () => { void load() }, {immediate: true})
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try { await action(); await load(); emit('changed'); notice.value = 'Changes saved.'; notify('saved', 'Maintenance Updated', 'Work order maintenance details and parts have been updated.', { suffix: `WO-${props.id}` }) }
  catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}
function change(action: string) {
  return run(async () => {
    await api('/maintenance/orders/' + props.id, 'POST', {action, revision: job.value!.revision,
      assignee_id: assignee.value, cost_center_id: center.value, reason: reason.value, findings: findings.value, results: results.value})
    reason.value = ''
  })
}
function stock(returning: boolean) {
  return run(async () => {
    const payload = returning ? {original_id: original.value, quantity: returnQty.value}
      : {item_id: item.value, location_id: location.value, quantity: issueQty.value}
    const value = JSON.stringify(payload)
    if (replay?.value !== value) replay = {value, key: requestKey()}
    await api('/maintenance/orders/' + props.id + '/parts', 'POST', payload, replay.key)
    replay = undefined
    if (returning) returnQty.value = ''
    else issueQty.value = ''
  })
}
</script>

<template>
  <section class="panel record-form approval-section">
    <h2>{{ t('Maintenance execution') }}</h2>
    <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
    <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
    <template v-if="job">
      <p><RouterLink v-if="canReadAsset" :to="'/maintenance/assets/' + job.asset_id">{{ job.asset }}</RouterLink>
        <span v-else>{{ job.asset }}</span> · {{ t(stageLabel[job.stage] ?? job.stage) }}
        <span v-if="job.due_date"> · {{ t('Due date') }} {{ job.due_date }}</span></p>
      <div v-if="allowed('supervise') && ['waiting','in_progress'].includes(job.stage) && !job.movements.length" class="actions">
        <div class="field-wrap"><label>{{ t('Cost center') }}<EntityAutocomplete v-model="center" lookup="cost_centers" :site-id="props.site" :local-items="(job.centers ?? []).map(row => ({ id: row.id, label: row.name }))" :empty-label="t('Unassigned')" data-field="center" /></label><FieldHint :label="t('Cost center')" /></div>
        <button class="secondary" :disabled="busy || !center" @click="change('cost_center')">{{ t('Save cost center') }}</button>
      </div>
      <p v-else-if="job.cost_center_id">{{ t('Cost center') }} · {{ job.centers.find(c=>c.id===job?.cost_center_id)?.name ?? '#' + job.cost_center_id }}</p>
      <p v-if="job.center_required && !job.cost_center_id && ['waiting','in_progress'].includes(job.stage)" class="muted">
        {{ t(allowed('supervise') ? (job.centers.length ? 'Set a cost center before issuing parts.' :
          'Create an active cost center for this site before issuing parts.') : 'A maintenance supervisor must set the cost center before parts can be issued.') }}
        <RouterLink v-if="allowed('supervise') && !job.centers.length && allows(workspace,'budget.view',site)"
          to="/budgeting/centers">{{ t('Cost centers') }}</RouterLink>
      </p>
      <p v-if="job.assignee">{{ t('Technician') }} · {{ job.assignee }}</p>
      <p v-for="[label, value] in [['Started',job.started_at],['Completed',job.completed_at],['Closed',job.closed_at]]" :key="label ?? ''">
        <template v-if="value">{{ t(label!) }} · {{ new Date(value).toLocaleString() }}</template></p>
      <ol v-if="job.checklist.length"><li v-for="(step, index) in job.checklist" :key="index">{{ step }}
        <span v-if="job.results[index]"> · {{ job.results[index] }}</span></li></ol>
      <p v-if="job.findings">{{ t('Findings') }} · {{ job.findings }}</p>
      <p v-if="job.close_note">{{ t('Reason') }} · {{ job.close_note }}</p>
      <div v-if="allowed('supervise') && job.stage === 'waiting'" class="actions">
        <div class="field-wrap"><label>{{ t('Technician') }}<EntityAutocomplete v-model="assignee" lookup="employees" :site-id="props.site" :local-items="(job.technicians ?? []).map(row => ({ id: row.id, label: row.name }))" data-field="assignee" /></label><FieldHint :label="t('Technician')" /></div>
        <button class="secondary" :disabled="busy || !assignee" @click="change('assign')">{{ t('Assign') }}</button>
        <button v-if="job.status === 'submitted'" class="primary" :disabled="busy" @click="change('authorize')">{{ t('Authorize maintenance') }}</button>
      </div>
      <button v-if="allowed('execute') && job.status === 'approved' && job.is_assignee && job.stage === 'waiting'"
        class="primary" :disabled="busy" @click="change('start')">{{ t('Start work') }}</button>
      <form v-validated-form v-if="allowed('execute') && job.is_assignee && job.stage === 'in_progress'" class="record-form" @submit.prevent="change('complete')">
        <div class="field-wrap" v-for="(step,index) in job.checklist" :key="index"><label>{{ step }}<input :placeholder="t('Enter a value')" v-model="results[index]" required maxlength="300"></label><FieldHint :label="step" required /></div>
        <div class="field-wrap"><label>{{ t('Findings') }}<textarea data-field="findings" :placeholder="t('Enter details')" v-model="findings" required maxlength="5000"></textarea></label><FieldHint :label="t('Findings')" required /></div>
        <button class="primary" :disabled="busy">{{ t('Complete work') }}</button>
      </form>
      <div v-if="allowed('supervise') && (job.stage === 'completed' || (['waiting','in_progress'].includes(job.stage)
        && job.status !== 'in_review' && !job.movements.length))" class="actions">
        <div class="field-wrap"><label>{{ t('Reason') }}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="reason" maxlength="1000"></label><FieldHint :label="t('Reason')" /></div>
        <button v-if="job.stage === 'completed'" class="primary" :disabled="busy || job.movements.some(row => row.posting === 'pending')"
          @click="change('close')">{{ t('Close work order') }}</button>
        <button v-else class="secondary" :disabled="busy || !reason.trim()" @click="change('cancel')">{{ t('Cancel work order') }}</button>
      </div>
      <WorkOrderTime v-if="workspace.modules.some(module => module.id === 'hr' && module.enabled)"
        :key="id" :id="id" :site="site" :stage="job.stage"
        :can-start="job.stage === 'in_progress' && job.is_assignee && allowed('execute')" :workspace="workspace" />
      <h3>{{ t('Spare parts cost') }}</h3>
      <p>{{ t('Total issued − Total returned = Net parts cost') }}</p>
      <p><bdi>{{ job.costs.issued }} − {{ job.costs.returned }} = <strong>{{ job.costs.net }} AED</strong></bdi></p>
      <p v-if="job.movements.some(row => row.posting === 'pending')" class="alert">{{ t('Post remaining stock movements before closing this work order.') }}</p>
      <h4 v-if="job.spare_parts.length">{{ t('Suggested spare parts') }}</h4>
      <ul v-if="job.spare_parts.length"><li v-for="part in job.spare_parts" :key="part.item_id">{{ part.item ?? label(part.item_id) }} · {{ part.quantity }}</li></ul>
      <HistoryTable :url="'/maintenance/orders/'+id+'/movements'" :version="job.revision" v-slot="{rows}" class="table-panel"><table>
        <thead><tr><th>{{ t('Movement') }}</th><th>{{ t('Item') }}</th><th>{{ t('Location') }}</th><th>{{ t('Quantity') }}</th>
          <th>{{ t('Amount') }}</th><th>{{ t('Posting') }}</th></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.id">
          <td>#{{ row.id }} · {{ t(row.kind === 'maint_issue' ? 'Issue parts' : 'Return unused parts') }}</td><td>{{ row.item }}</td>
          <td>{{ row.location }}</td><td>{{ row.quantity }}</td><td>{{ row.value }}</td><td>
            <RouterLink v-if="row.entry_id && allows(workspace,'accounting.view',null)" :to="{path:'/accounting/journals', query:{entry:row.entry_id}}">JE-{{ row.entry_id }}</RouterLink>
            <span v-else>{{ t(row.posting) }}</span></td></tr></tbody>
      </table></HistoryTable>
      <form v-validated-form v-if="canIssue && inventory" class="actions" @submit.prevent="stock(false)">
        <div class="field-wrap"><label>{{ t('Item') }}<EntityAutocomplete v-model="item" lookup="items" :local-items="inventory.items.filter(i => i.active).map(row => ({ id: row.id, label: row.code + ' · ' + row.name + ' · ' + unitCode(row.base_unit_id) }))" data-field="item" required /></label><FieldHint :label="t('Item')" required /></div>
        <div class="field-wrap"><label>{{ t('Location') }}<EntityAutocomplete v-model="location" lookup="locations" :site-id="props.site" :local-items="locations.map(row => ({ id: row.id, label: (inventory?.warehouses.find(wh => wh.id === row.warehouse_id)?.code ?? '') + ' · ' + row.code }))" data-field="location" required /></label><FieldHint :label="t('Location')" required /></div>
        <div class="field-wrap"><label>{{ t('Issue quantity') }}<input data-field="issueQty" :placeholder="t('e.g., 125.50')" v-model="issueQty" required inputmode="decimal"></label><FieldHint :label="t('Issue quantity')" required /></div>
        <button class="primary" :disabled="busy">{{ t('Issue parts') }}</button>
      </form>
      <form v-validated-form v-if="canReturn" class="actions" @submit.prevent="stock(true)">
        <div class="field-wrap"><label>{{ t('Original issue') }}<select data-field="original" v-model="original" required><option v-for="row in job.movements.filter(r => r.kind === 'maint_issue')"
          :key="row.id" :value="row.id">#{{ row.id }} · {{ row.item }} · {{ row.quantity }}</option></select></label><FieldHint :label="t('Original issue')" required /></div>
        <div class="field-wrap"><label>{{ t('Return quantity') }}<input data-field="returnQty" :placeholder="t('e.g., 125.50')" v-model="returnQty" required inputmode="decimal"></label><FieldHint :label="t('Return quantity')" required /></div>
        <button class="secondary" :disabled="busy">{{ t('Return unused parts') }}</button>
      </form>
    </template>
  </section>
</template>
