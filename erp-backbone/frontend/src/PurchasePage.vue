<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPaging,ref as pagingRef} from 'vue'
const pg=pagingRef(1),ps=pagingRef(25),term=pagingRef(''),total=pagingRef(0)
function queryPage(){return new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}).toString()}

import SavedViews from './SavedViews.vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, allows, createRequest, type Workspace } from './api'
import { message, t } from './i18n'
import DocumentTools from './DocumentTools.vue'

type Line = { description: string; quantity: string; unit_price: string; amount?: string }
type Step = { id: number; name: string }
type Review = {
  version: number; currency: string; total: string; threshold: string; status: string;
  next_step: number; steps: Step[]; submitted_by: string; submitted_at: string;
  decisions: { step: number; actor: string; action: string; reason: string; delegation_id: number | null }[];
}
type Request = {
  id: number; number: string; archived: boolean; site_locked: boolean; title: string; site_id: number; currency: string; total: string;
  status: 'draft' | 'in_review' | 'approved'; revision: number;
  purchase_order?: { id: number; number: string }; can_create_purchase_order?: boolean;
  lines: Line[]; reviews: Review[]; can_review: boolean;
}
const { workspace } = defineProps<{ workspace: Workspace }>()
const route = useRoute()
const router = useRouter()
const id = computed(() => route.params.id ? Number(route.params.id) : null)
const rows = ref<Request[]>([])
const currencies = ref<string[]>([])
const record = ref<Request>()
const creating = ref(false)
const listMode = ref<'active' | 'archived' | 'mine'>('active')
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const title = ref('')
const site = ref<number>()
const currency = ref('')
const lines = ref<Line[]>([])
const reason = ref('')
let sequence = 0
let create = createRequest()
let convert = createRequest()
const purchaseOrdersEnabled = computed(() => workspace.modules.some(module => module.id === 'purchase_orders' && module.enabled))
const suppliers = ref<{id: number; code: string; name: string; active?: boolean}[]>([])
const supplier = ref<number>()
const converting = ref(false)
const statuses = { draft: 'Draft', in_review: 'In review', approved: 'Approved' }
const reviewStatuses: Record<string, string> = { pending: 'In review', approved: 'Approved', returned: 'Returned for changes' }
const canManage = computed(() => record.value?.status === 'draft'
  && allows(workspace, 'purchasing.edit', record.value.site_id))
const canEdit = computed(() => creating.value || (canManage.value && !record.value?.archived))
const sites = computed(() => workspace.sites.filter(item =>
  item.active && allows(workspace, 'purchasing.view', item.id) &&
  allows(workspace, creating.value ? 'purchasing.create' : 'purchasing.edit', item.id)))

function apply(item: Request) {
  record.value = item
  title.value = item.title
  site.value = item.site_id
  currency.value = item.currency
  lines.value = item.lines.map(line => ({ ...line }))
}

async function load() {
  const current = ++sequence
  const target = id.value
  error.value = ''
  notice.value = ''
  reason.value = ''
  converting.value = false
  supplier.value = undefined
  convert = createRequest()
  loading.value = true
  record.value = undefined
  creating.value = false
  try {
    if (target) {
      const item = await api<Request>('/purchase-requests/' + target)
      if (current === sequence) apply(item)
    } else {
      const result = await api<{ items: Request[]; currencies: string[];count:number }>('/purchase-requests?'+queryPage() + ({ mine: '&review=mine', archived: '&archived=1', active: '' }[listMode.value]))
      if (current === sequence) {
        rows.value = result.items;total.value=result.count
        currencies.value = result.currencies;if(route.query.compose==='1' && allows(workspace,'purchasing.create')){start();const target=sites.value.find(s=>s.id===Number(route.query.site));if(target)site.value=target.id;void router.replace({query:{...route.query,compose:undefined}})}
      }
    }
  } catch (failure) { if (current === sequence) error.value = message(failure) }
  finally { if (current === sequence) loading.value = false }
}

watchPaging([pg,ps,term],load)
watch([id, listMode], load, { immediate: true })

function start() {
  create = createRequest()
  creating.value = true
  error.value = ''
  title.value = ''
  site.value = sites.value[0]?.id
  currency.value = currencies.value[0] ?? ''
  lines.value = [{ description: '', quantity: '1', unit_price: '' }]
}

async function run(operation: (current: number) => Promise<void>) {
  if (busy.value) return
  const current = sequence
  busy.value = true
  error.value = ''
  notice.value = ''
  try { await operation(current) }
  catch (failure) { if (current === sequence) error.value = message(failure) }
  finally { busy.value = false }
}

function save() {
  void run(async current => {
    const payload = { title: title.value, site_id: site.value, currency: currency.value, lines: lines.value }
    if (creating.value) {
      const item = await create<Request>('/purchase-requests', payload)
      if (current === sequence) {
        await router.push('/purchase-requests/' + item.id)
        notice.value = 'Purchase request saved successfully.'
      }
    } else if (record.value) {
      const item = await api<Request>('/purchase-requests/' + record.value.id, 'PATCH',
        { ...payload, revision: record.value.revision })
      if (current === sequence) { apply(item); notice.value = 'Purchase request saved successfully.' }
    }
  })
}

function transition(action: 'submit' | 'approve' | 'return') {
  void run(async current => {
    const item = record.value
    if (!item) return
    const endpoint = '/purchase-requests/' + item.id + (action === 'submit' ? '/submit' : '/review')
    const updated = await api<Request>(endpoint, 'POST', { revision: item.revision, action, reason: reason.value })
    if (current === sequence) {
      apply(updated)
      reason.value = ''
      notice.value = action === 'submit' ? 'Purchase request submitted.' : 'Review saved.'
    }
  })
}

function chooseSupplier() {
  void run(async current => {
    const data = await api<{items: typeof suppliers.value}>('/suppliers')
    if (current === sequence) {
      suppliers.value = data.items.filter(item => item.active !== false)
      converting.value = true
    }
  })
}

function createOrder() {
  void run(async current => {
    if (!record.value || !supplier.value) return
    const result = await convert<{id: number}>('/purchase-requests/' + record.value.id + '/purchase-order',
      { supplier_id: supplier.value })
    if (current === sequence) await router.push('/purchase-orders/' + result.id)
  })
}

const dirty = computed(() => record.value && (
  title.value !== record.value.title || site.value !== record.value.site_id || currency.value !== record.value.currency
  || JSON.stringify(lines.value) !== JSON.stringify(record.value.lines)
))
</script>

<template>
  <RouterLink v-if="id" class="back-link" to="/purchase-requests">‹ {{ t('Back to purchase requisitions') }}</RouterLink>
  <div class="page-heading">
    <h1>{{ record?.number ?? t('Purchase requisitions') }}</h1>
    <span v-if="record" class="badge" :class="{ on: record.status === 'approved' }">{{ t(record.archived ? 'Archived' : statuses[record.status]) }}</span>
    <button v-if="!id && !creating && listMode !== 'archived' && allows(workspace, 'purchasing.create')" class="primary" @click="start">
      {{ t('New purchase requisition') }}
    </button>
  </div>
  <SavedViews v-if="!id && !creating" :workspace="workspace" screen="purchase-requests"
    :filters="{review: listMode === 'mine' ? 'mine' : '', archived: listMode === 'archived' ? '1' : ''}"
    @apply="listMode = $event.review === 'mine' ? 'mine' : $event.archived ? 'archived' : 'active'" />
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <p v-if="loading" class="muted">{{ t('Loading') }}…</p>
  <form v-validated-form v-else-if="record || creating" class="panel record-form purchase-form" @submit.prevent="save">
    <div class="field-wrap"><label>{{ t('Title') }}<input data-field="title" :placeholder="t('Enter a short, descriptive title')" v-model="title" :readonly="!canEdit" required maxlength="120"></label><FieldHint :label="t('Title')" required :readonly="!canEdit" /></div>
    <div class="field-wrap" v-if="canEdit && !record?.site_locked"><label>{{ t('Site') }}
      <select data-field="site_id" v-model="site" required><option v-for="item in sites" :key="item.id" :value="item.id">{{ item.name }}</option></select>
    </label><FieldHint :label="t('Site')" required /></div>
    <p v-else>{{ t('Site') }} · {{ workspace.sites.find(item => item.id === site)?.name }}</p>
    <div class="field-wrap" v-if="creating"><label>{{ t('Currency') }}
      <select data-field="currency" v-model="currency" required><option v-for="code in currencies" :key="code" :value="code">{{ code }}</option></select>
    </label><FieldHint :label="t('Currency')" required /></div>
    <p v-else>{{ t('Currency') }} · {{ currency }}</p>
    <fieldset v-for="(line, index) in lines" :key="index" class="purchase-line">
      <legend>{{ t('Line item') }} {{ index + 1 }}</legend>
      <div class="field-wrap"><label>{{ t('Description') }}<input :data-field="'lines.' + index + '.description'" :placeholder="t('Describe the item or activity')" v-model="line.description" :readonly="!canEdit" required maxlength="200"></label><FieldHint :label="t('Description')" required :readonly="!canEdit" /></div>
      <div class="field-wrap"><label>{{ t('Quantity') }}<input :data-field="'lines.' + index + '.quantity'" :placeholder="t('e.g., 2.500')" v-model="line.quantity" :readonly="!canEdit" inputmode="decimal" required pattern="[0-9]{1,7}(\.[0-9]{1,3})?"></label><FieldHint :label="t('Quantity')" required :readonly="!canEdit" /></div>
      <div class="field-wrap"><label>{{ t('Estimated unit price') }}<input :data-field="'lines.' + index + '.unit_price'" :placeholder="t('e.g., 125.50')" v-model="line.unit_price" :readonly="!canEdit" inputmode="decimal" required pattern="[0-9]{1,9}(\.[0-9]{1,4})?"></label><FieldHint :label="t('Estimated unit price')" required :readonly="!canEdit" /></div>
      <span v-if="line.amount && !dirty"><bdi>{{ line.amount }} {{ currency }}</bdi></span>
      <button v-if="canEdit && lines.length > 1" type="button" class="secondary" @click="lines.splice(index, 1)">{{ t('Remove item') }}</button>
    </fieldset>
    <p v-if="record && !dirty"><strong>{{ t('Estimated total') }}</strong> · <bdi>{{ record.total }} {{ record.currency }}</bdi></p>
    <p v-if="dirty" class="muted">{{ t('Save the draft to update the total.') }}</p>
    <div class="actions">
      <button v-if="canEdit" type="button" class="secondary" :disabled="lines.length >= 100 || busy"
        @click="lines.push({ description: '', quantity: '1', unit_price: '' })">{{ t('Add item') }}</button>
      <button v-if="canEdit" class="primary" :disabled="busy">{{ t('Save draft') }}</button>
      <button v-if="record?.status === 'draft' && !record.archived && allows(workspace, 'purchasing.submit', record.site_id)"
        type="button" class="secondary" :disabled="busy || dirty" @click="transition('submit')">{{ t('Submit purchase request') }}</button>
      <button v-if="creating" type="button" class="secondary" @click="creating = false">{{ t('Cancel') }}</button>
    </div>
  </form>
  <section v-if="purchaseOrdersEnabled && (record?.purchase_order || record?.can_create_purchase_order)" class="panel record-form approval-section">
    <h2>{{ t('Purchase order') }}</h2>
    <RouterLink v-if="record.purchase_order" :to="'/purchase-orders/' + record.purchase_order.id">
      {{ record.purchase_order.number }}
    </RouterLink>
    <button v-else-if="!converting" class="primary" :disabled="busy" @click="chooseSupplier">{{ t('Create purchase order') }}</button>
    <p v-else-if="!suppliers.length" class="muted">{{ t('No active suppliers. Ask an administrator to add one.') }}</p>
    <form v-validated-form v-else class="actions" @submit.prevent="createOrder">
      <EntityAutocomplete
        data-field="supplier_id"
        :label="t('Supplier')"
        lookup="suppliers"
        :choices="suppliers"
        :empty-value="undefined"
        required
        v-model="supplier"
      />
      <button class="primary" :disabled="busy">{{ t('Create purchase order') }}</button>
    </form>
  </section>
  <DocumentTools v-if="record" :key="record.id" :id="record.id" :revision="record.revision"
    :archived="record.archived" :editable="canManage && !dirty && !busy" @changed="load" />
  <section v-if="record?.reviews.length" class="panel record-form approval-section">
    <h2>{{ t('Approval history') }}</h2>
    <div v-for="(review, index) in record.reviews" :key="index" class="approval-run">
      <strong>{{ t('Version') }} {{ review.version }} · {{ t(reviewStatuses[review.status]) }}</strong>
      <p class="muted">{{ t('Submitted by') }} <bdi>{{ review.submitted_by }}</bdi> · {{ new Date(review.submitted_at).toLocaleString() }}</p>
      <p>{{ t('Estimated total') }} <bdi>{{ review.total }} {{ review.currency }}</bdi> · {{ t('Finance threshold') }} <bdi>{{ review.threshold }} {{ review.currency }}</bdi></p>
      <ol><li v-for="(step, position) in review.steps" :key="step.id">
        <bdi>{{ step.name }}</bdi>
        <span v-if="review.status === 'pending' && review.next_step === position"> · {{ t('Awaiting review') }}</span>
        <div v-for="decision in review.decisions.filter(item => item.step === position)" :key="decision.step" class="muted">
          {{ t(decision.action === 'approve' ? 'Approved' : 'Returned for changes') }} · <bdi>{{ decision.actor }}</bdi>
          <div v-if="decision.delegation_id">{{ t('Delegation') }} #{{ decision.delegation_id }}</div>
          <p v-if="decision.reason" class="approval-reason">{{ decision.reason }}</p>
        </div>
      </li></ol>
    </div>
    <form v-validated-form v-if="record.can_review" class="record-form"
      @submit.prevent="transition(($event as SubmitEvent).submitter?.getAttribute('value') === 'return' ? 'return' : 'approve')">
      <div class="field-wrap"><label>{{ t('Review note') }}<textarea data-field="reason" data-required-action="return" :placeholder="t('Explain the reason')" v-model="reason" rows="3" maxlength="1000"></textarea></label><FieldHint :label="t('Review note')" /></div>
      <div class="actions">
        <button class="primary" :disabled="busy">{{ t('Approve') }}</button>
        <button class="secondary" value="return" :disabled="busy">{{ t('Return for changes') }}</button>
      </div>
    </form>
  </section>
  <template v-if="!id && !loading && !creating">
    <div class="actions">
      <button v-if="allows(workspace, 'purchasing.approve')" class="secondary" :aria-pressed="listMode === 'mine'" @click="listMode = listMode === 'mine' ? 'active' : 'mine'">
        {{ t(listMode === 'mine' ? 'Show all purchase requests' : 'Needs my review') }}
      </button>
      <button class="secondary" :aria-pressed="listMode === 'archived'"
        @click="listMode = listMode === 'archived' ? 'active' : 'archived'">
        {{ t(listMode === 'archived' ? 'Show all purchase requests' : 'Archived drafts') }}
      </button>
      <RouterLink v-if="workspace.admin" class="button secondary" to="/purchase-templates">{{ t('Approval templates') }}</RouterLink>
    </div>
    <section class="panel table-panel approval-section">
<TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/>
      <table v-if="rows.length">
        <thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Title') }}</th><th>{{ t('Site') }}</th>
          <th>{{ t('Estimated total') }}</th><th>{{ t('Status') }}</th></tr></thead>
        <tbody><tr v-for="item in rows" :key="item.id">
          <td><RouterLink :to="'/purchase-requests/' + item.id">{{ item.number }}</RouterLink></td>
          <td>{{ item.title }}</td><td>{{ workspace.sites.find(site => site.id === item.site_id)?.name }}</td>
          <td><bdi>{{ item.total }} {{ item.currency }}</bdi></td><td>{{ t(item.archived ? 'Archived' : statuses[item.status]) }}</td>
        </tr></tbody>
      </table>
      <p v-else class="empty muted">{{ t(listMode === 'mine' ? 'No purchase requests need your review.' : listMode === 'archived' ? 'No archived drafts.' : 'No purchase requisitions yet.') }}</p>
    </section>
  </template>
</template>
