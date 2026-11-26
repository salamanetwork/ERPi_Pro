<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPaging,ref as pagingRef} from 'vue'
const pg=pagingRef(1),ps=pagingRef(25),term=pagingRef(''),total=pagingRef(0)
function queryPage(){return new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}).toString()}

import WorkOrderMaintenance from './WorkOrderMaintenance.vue'
import SavedViews from './SavedViews.vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, ApiError, createRequest, type Asset, type Scope, type Workspace, type WorkOrder } from './api'
import { message, t } from './i18n'
import { notify } from './useUnifiedNotice'

const { workspace } = defineProps<{ workspace: Workspace }>()
const router = useRouter()
const route = useRoute()
const id = computed(() => route.params.id ? Number(route.params.id) : null)
const rows = ref<WorkOrder[]>([])
const assets = ref<Asset[]>([])
const record = ref<WorkOrder>()
const loading = ref(true)
const creating = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const conflict = ref(false)
const myReview = ref(false)
const reason = ref('')
const title = ref('')
const description = ref('')
const site = ref<Scope>(null)
const asset = ref<number | null>(null)
let requestSequence = 0
let create = createRequest()

const integration = computed(() => workspace.modules.some(module => module.id === 'assets' && module.visible))
const canCreateMaintenance = computed(() => integration.value && workspace.sites.some(row => row.active &&
  ['assets.view', 'workorders.view', 'workorders.create', 'maintenance.view', 'maintenance.plan']
    .every(permission => allows(workspace, permission, row.id))))
const correctivePath = computed(() => {
  const order = record.value
  if (order?.asset_reference && order.site_id !== null &&
    ['assets.view', 'workorders.view', 'workorders.create', 'maintenance.view', 'maintenance.plan']
      .every(permission => allows(workspace, permission, order.site_id))) {
    return {path: '/maintenance/assets/' + order.asset_reference, hash: '#new-corrective'}
  }
  return {path: '/maintenance'}
})
const permission = computed(() => creating.value ? 'workorders.create' : 'workorders.edit')
const availableSites = computed(() => workspace.sites.filter(item => item.active && allows(workspace, permission.value, item.id)))
const availableAssets = computed(() => assets.value.filter(item => item.site_id === site.value))
const maintenanceLocked = computed(() => Boolean(record.value?.maintenance_stage))
const canEdit = computed(() => creating.value || Boolean(record.value?.status === 'draft' && (!maintenanceLocked.value || record.value.maintenance_stage === 'waiting') && allows(workspace, 'workorders.edit', record.value.site_id)))
const canSubmit = computed(() => Boolean(record.value?.status === 'draft' && (!maintenanceLocked.value || record.value.maintenance_stage === 'waiting') && allows(workspace, 'workorders.submit', record.value.site_id)))
const statusLabels = { draft: 'Draft', submitted: 'Submitted', in_review: 'In review', approved: 'Approved' }
const statusText = (status: WorkOrder['status'], stage?: string) => t(stage && stage !== 'waiting' ? ({in_progress:'In progress', completed:'Completed', closed:'Closed', cancelled:'Cancelled'} as Record<string,string>)[stage] ?? stage : statusLabels[status])
const outcomes = { pending: 'In review', approved: 'Approved', returned: 'Returned for changes' }

function apply(order: WorkOrder) {
  record.value = order
  title.value = order.title
  description.value = order.description
  site.value = order.site_id
  asset.value = order.asset_reference
}

async function load() {
  const sequence = ++requestSequence
  const target = id.value
  loading.value = true
  error.value = ''
  notice.value = ''
  conflict.value = false
  reason.value = ''
  record.value = undefined
  creating.value = false
  try {
    if (target) {
      const order = await api<WorkOrder>('/work-orders/' + target)
      if (sequence === requestSequence) apply(order)
    } else {
      const result = await api<{ items: WorkOrder[];count:number }>('/work-orders?'+queryPage()+(myReview.value?'&review=mine':''))
      if (sequence === requestSequence) {rows.value = result.items;total.value=result.count;if(route.query.compose==='1' && allows(workspace,'workorders.create')){start();const target=availableSites.value.find(s=>s.id===Number(route.query.site));if(target)site.value=target.id;void router.replace({query:{...route.query,compose:undefined}})}}
    }
    if (integration.value && sequence === requestSequence) {
      try {
        const result = await api<{ items: Asset[] }>('/assets')
        if (sequence === requestSequence) assets.value = result.items
      } catch { if (sequence === requestSequence) assets.value = [] }
    }
  } catch (reason) {
    if (sequence === requestSequence) error.value = message(reason)
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}
watchPaging([pg,ps,term],load)
watch([id, myReview], load, { immediate: true })

async function refreshOrder() {
  if (!id.value) return
  try {
    const incoming = await api<WorkOrder>('/work-orders/' + id.value)
    if (incoming.id === id.value) apply(incoming)
  } catch (failure) { error.value = message(failure) }
}

function start() {
  create = createRequest()
  creating.value = true
  error.value = ''
  title.value = ''
  description.value = ''
  site.value = allows(workspace, 'workorders.create', null) ? null : availableSites.value[0]?.id ?? null
  asset.value = null
}

function payload() {
  const fields: Record<string, string | number | null> = { title: title.value, description: description.value, site_id: site.value }
  if (integration.value && (creating.value || asset.value !== record.value?.asset_reference)) {
    fields.asset_reference = asset.value
  }
  return fields
}

async function save(submit = false) {
  if (busy.value) return
  const sequence = requestSequence
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    if (creating.value) {
      const order = await create<WorkOrder>('/work-orders', payload())
      if (sequence === requestSequence) {
        notify('saved', 'Work Order Created', 'New work order has been created.', { suffix: `WO-${order.id}` })
        await router.push('/work-orders/' + order.id)
      }
    } else if (record.value) {
      let saved = record.value
      const fields = payload()
      const changed = fields.title !== saved.title || fields.description !== saved.description ||
        fields.site_id !== saved.site_id || 'asset_reference' in fields
      if (canEdit.value && (changed || !submit)) {
        saved = await api<WorkOrder>('/work-orders/' + saved.id, 'PATCH', { ...fields, revision: saved.revision })
        if (sequence === requestSequence) apply(saved)
      }
      if (submit) {
        saved = await api<WorkOrder>('/work-orders/' + saved.id + '/submit', 'POST', { revision: saved.revision })
        if (sequence === requestSequence) apply(saved)
      }
      if (sequence === requestSequence) {
        notice.value = 'Changes saved.'
        if (submit) notify('confirmation', 'Work Order Submitted', 'Work order has been submitted for review.', { suffix: `WO-${saved.id}` })
        else notify('saved', 'Changes Saved', 'Your updates have been saved successfully.', { suffix: `WO-${saved.id}` })
      }
    }
  } catch (reason) {
    if (sequence === requestSequence) {
      error.value = message(reason)
      conflict.value = reason instanceof ApiError && reason.code === 'revision_conflict'
    }
  } finally { busy.value = false }
}
async function review(action: 'approve' | 'return') {
  if (busy.value || !record.value?.can_review) return
  const sequence = requestSequence
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const order = await api<WorkOrder>('/work-orders/' + record.value.id + '/review', 'POST', {
      revision: record.value.revision, action, reason: reason.value,
    })
    if (sequence === requestSequence) {
      apply(order)
      reason.value = ''
      notice.value = 'Review saved.'
      if (action === 'approve') notify('confirmation', 'Work Order Approved', 'Work order review decision has been recorded.', { suffix: `WO-${order.id}` })
      else notify('warning', 'Work Order Returned', 'Work order has been returned for revision.', { suffix: `WO-${order.id}` })
    }
  } catch (failure) {
    if (sequence === requestSequence) {
      error.value = message(failure)
      conflict.value = failure instanceof ApiError && failure.code === 'revision_conflict'
    }
  } finally { busy.value = false }
}
</script>

<template>
  <RouterLink v-if="id" class="back-link" to="/work-orders">‹ {{ t('Back to work orders') }}</RouterLink>
  <div class="page-heading">
    <h1>{{ id ? 'WO-' + id : t('Work orders') }}</h1>
    <button v-if="!id && !creating && allows(workspace, 'workorders.create')" class="primary" @click="start">{{ t('New work order') }}</button>
    <RouterLink v-if="!id && !creating && canCreateMaintenance" to="/maintenance" class="button secondary">
      {{ t('New corrective work order') }}
    </RouterLink>
    <span v-if="record" class="badge" :class="{ on: ['submitted', 'approved'].includes(record.status) }">{{ statusText(record.status, record.maintenance_stage) }}</span>
  </div>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <div v-if="conflict" class="actions">
    <p>{{ t('Your edits are still here. Review them before reloading.') }}</p>
    <button class="secondary" @click="load">{{ t('Reload latest version') }}</button>
  </div>
  <p v-if="loading" class="muted">{{ t('Loading') }}…</p>
  <form v-validated-form v-else-if="record || creating" class="panel record-form" @submit.prevent="save()">
    <h2 v-if="creating">{{ t('New work order') }}</h2>
    <p v-if="(creating || record) && !maintenanceLocked" class="muted">
      {{ t('General work order. To issue parts with a cost center, create a corrective work order from an asset.') }}
      <RouterLink v-if="canCreateMaintenance" :to="correctivePath">{{ t('New corrective work order') }}</RouterLink>
    </p>
    <div class="field-wrap"><label>{{ t('Title') }}<input data-field="title" :placeholder="t('Enter a short, descriptive title')" v-model="title" :readonly="!canEdit" required maxlength="120"></label><FieldHint :label="t('Title')" required :readonly="!canEdit" /></div>
    <div class="field-wrap"><label>{{ t('Description') }}<textarea data-field="description" :placeholder="t('Describe the item or activity')" v-model="description" :readonly="!canEdit" maxlength="5000" rows="4"></textarea></label><FieldHint :label="t('Description')" :readonly="!canEdit" /></div>
    <div class="field-wrap" v-if="canEdit && !maintenanceLocked"><label>{{ t('Site') }}<select data-field="site_id" v-model="site" @change="asset = null">
      <option v-if="allows(workspace, permission, null)" :value="null">{{ t('No site') }}</option>
      <option v-for="item in availableSites" :key="item.id" :value="item.id">{{ item.name }}</option>
    </select></label><FieldHint :label="t('Site')" /></div>
    <p v-else>{{ t('Site') }} · {{ workspace.sites.find(item => item.id === site)?.name ?? t('No site') }}</p>
    <div class="field-wrap" v-if="integration && canEdit && !maintenanceLocked"><label>{{ t('Asset link') }}<select data-field="asset_id" v-model="asset">
      <option :value="null">{{ t('No asset') }}</option>
      <option v-if="asset && !availableAssets.some(item => item.id === asset)" :value="asset" disabled>{{ record?.asset_snapshot }}</option>
      <option v-for="item in availableAssets" :key="item.id" :value="item.id">{{ item.tag }} · {{ item.name }}</option>
    </select></label><FieldHint :label="t('Asset link')" /></div>
    <p v-if="record?.asset_snapshot" class="reference"><strong>{{ t('Historical asset reference') }}</strong> <bdi>{{ record.asset_snapshot }}</bdi></p>
    <div v-if="canEdit || canSubmit" class="actions">
      <button v-if="canEdit" class="primary" :disabled="busy || conflict">{{ t('Save draft') }}</button>
      <button v-if="canSubmit" class="secondary" type="button" :disabled="busy || conflict" @click="save(true)">{{ t('Submit work order') }}</button>
      <button v-if="creating" class="secondary" type="button" @click="creating = false">{{ t('Cancel') }}</button>
    </div>
  </form>
  <section v-if="record?.approval_runs?.length" class="panel record-form approval-section">
    <h2>{{ t('Approval history') }}</h2>
    <div v-for="(run, index) in record.approval_runs" :key="index" class="approval-run">
      <strong>{{ t('Version') }} {{ run.version }} · {{ t(outcomes[run.outcome]) }}</strong>
      <p class="muted">{{ t('Submitted by') }} <bdi>{{ run.submitted_by }}</bdi> · {{ new Date(run.submitted_at).toLocaleString() }}</p>
      <ol>
        <li v-for="(step, position) in run.steps" :key="position">
          <bdi>{{ step }}</bdi>
          <span v-if="run.outcome === 'pending' && run.next_step === position"> · {{ t('Awaiting review') }}</span>
          <div v-for="decision in run.decisions.filter(item => item.step === position)" :key="decision.step" class="muted">
            {{ t(decision.action === 'approve' ? 'Approved' : 'Returned for changes') }} · <bdi>{{ decision.actor }}</bdi> ·
            {{ new Date(decision.decided_at).toLocaleString() }}
            <div v-if="decision.delegation_id">{{ t('Delegation') }} #{{ decision.delegation_id }}</div>
            <p v-if="decision.reason" class="approval-reason">{{ decision.reason }}</p>
          </div>
        </li>
      </ol>
    </div>
    <div v-if="record.review_routing" class="approval-run">
      <h3>{{ t('Review routing') }}</h3>
      <p>{{ t('Required role') }} · <bdi>{{ record.review_routing.role_name }} #{{ record.review_routing.role_id }}</bdi></p>
      <p v-if="record.review_routing.eligible_reviewers.length">
        {{ t('Eligible reviewers') }} · <bdi>{{ record.review_routing.eligible_reviewers.join(', ') }}</bdi>
      </p>
      <p v-else class="alert">{{ t('No eligible reviewer for this work order. Check the required role and site under Assignments.') }}</p>
      <RouterLink to="/access">{{ t('Access control') }}</RouterLink>
    </div>
    <form v-validated-form v-if="record.can_review" @submit.prevent="review(($event as SubmitEvent).submitter?.getAttribute('value') === 'return' ? 'return' : 'approve')">
      <div class="field-wrap"><label>{{ t('Review note') }}<textarea data-field="reason" data-required-action="return" :placeholder="t('Explain the reason')" v-model="reason" rows="3" maxlength="1000"></textarea></label><FieldHint :label="t('Review note')" /></div>
      <p class="muted">{{ t('Returning an order requires a reason.') }}</p>
      <div class="actions">
        <button class="primary" :disabled="busy || conflict">{{ t('Approve') }}</button>
        <button value="return" class="secondary" :disabled="busy || conflict">{{ t('Return for changes') }}</button>
      </div>
    </form>
  </section>
  <WorkOrderMaintenance v-if="record?.maintenance_stage && record.site_id !== null &&
    allows(workspace, 'maintenance.view', record.site_id) && workspace.modules.some(row => row.id === 'assets' && row.enabled)"
    :key="record.id" :id="record.id" :site="record.site_id" :order-revision="record.revision" :workspace="workspace" @changed="refreshOrder" />
  <SavedViews v-if="!id && !creating" :workspace="workspace" screen="work-orders" :filters="{review: myReview ? 'mine' : ''}"
    @apply="myReview = $event.review === 'mine'" />
  <div v-if="!id" class="actions">
    <button v-if="allows(workspace, 'workorders.approve')" type="button" class="secondary"
      :aria-pressed="myReview" @click="myReview = !myReview">{{ t(myReview ? 'Show all work orders' : 'Needs my review') }}</button>
    <RouterLink v-if="workspace.admin" class="button secondary" to="/approval-templates">{{ t('Approval templates') }}</RouterLink>
  </div>
<TableToolbar v-if="!id && !creating" :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/>
  <section v-if="!id && !loading" class="panel table-panel">
    <table v-if="rows.length"><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Title') }}</th><th>{{ t('Site') }}</th><th>{{ t('Status') }}</th></tr></thead>
      <tbody><tr v-for="order in rows" :key="order.id">
        <td><RouterLink :to="'/work-orders/' + order.id">WO-{{ order.id }}</RouterLink></td><td>{{ order.title }}</td>
        <td>{{ workspace.sites.find(item => item.id === order.site_id)?.name ?? '—' }}</td>
        <td><span class="badge" :class="{ on: ['submitted', 'approved'].includes(order.status) }">{{ statusText(order.status, order.maintenance_stage) }}</span></td>
      </tr></tbody>
    </table>
    <p v-else class="empty muted">{{ t(myReview ? 'No work orders need your review.' : 'No work orders yet.') }}</p>
  </section>
</template>
