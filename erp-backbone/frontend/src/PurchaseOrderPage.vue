<script setup lang="ts">
import SavedViews from './SavedViews.vue'
import CsvExport from './CsvExport.vue'
import DataTable from './DataTable.vue'
import { useDirectory, useDirtyForm } from './useDirectory'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, type Workspace } from './api'
import { message, t } from './i18n'
import DocumentTools from './DocumentTools.vue'

type Supplier = { id: number; code: string; name: string; active?: boolean }
type Line = { id: number; description: string; quantity: string; unit_price: string; estimated_unit_price: string; amount: string; delivery_due?: string }
type PriceChange = { revision: number; actor: string; at: string; reason: string;
  changes: { line_id: number; from: string; to: string; delta: string }[] }
type Summary = {
  id: number; number: string; title: string; source_pr_id: number; source_pr_number: string;
  site_id: number; currency: string; total: string; supplier_id: number; supplier_name: string;
  status: 'draft' | 'issued' | 'cancelled'; revision: number; archived: boolean;
}
type Order = Summary & { lines: Line[]; price_changes: PriceChange[]; issued_by: string | null;
  issued_at: string | null; void_reason: string; voided_by: string | null; voided_at: string | null }
const { workspace } = defineProps<{ workspace: Workspace }>()
const route = useRoute()
const router = useRouter()
const readyOnly = computed(() => route.query.ready === "1")
const id = computed(() => Number(route.params.id) || null)
const directory = useDirectory<Summary>(() => '/purchase-orders', () => !id.value)
const {page, loading: listLoading, error: listError, filter, filters, change, filterBy} = directory
const back = computed(() => ({path:'/purchase-orders',query:route.query}))
const record = ref<Order>()
type ReceiptProgress = {receipt_status: string; lines: {id: number; description: string; received: string; remaining: string}[];
  receipts: {id: number; number: string; status: string}[]}
const progress = ref<ReceiptProgress>()
const receiptError = ref('')
const showInventory = computed(() => !!record.value && workspace.modules.some(module => module.id === 'inventory' && module.visible)
  && allows(workspace, 'inventory.view', record.value.site_id))
async function loadProgress(order: Order, token: number) {
  if (!showInventory.value || order.status !== 'issued') return
  try {
    const result = await api<ReceiptProgress>('/inventory/orders/' + order.id)
    if (token === request) progress.value = result
  } catch (failure) { if (token === request) receiptError.value = message(failure) }
}
const suppliers = ref<Supplier[]>([])
const supplier = ref<number>()
const prices = ref<{id: number; unit_price: string; delivery_due: string}[]>([])
const reason = ref('')
const voidReason = ref('')
const voidOpen = ref(false)
const archived = computed(() => filter('archived') === '1')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const loading = ref(false)
let request = 0
const statuses = { draft: 'Draft', issued: 'Issued', cancelled: 'Cancelled' }
const canEdit = computed(() => record.value?.status === 'draft'
  && allows(workspace, 'purchasing.po.create', record.value.site_id))
const priceDirty = computed(() => !!record.value && prices.value.some((line, index) =>
  Number(line.unit_price) !== Number(record.value?.lines[index]?.unit_price)))
const deliveryDirty = computed(() => !!record.value && prices.value.some((line, index) =>
  line.delivery_due !== (record.value?.lines[index]?.delivery_due ?? '')))
const dirty = computed(() => deliveryDirty.value || !!record.value && (priceDirty.value || supplier.value !== record.value.supplier_id))

const { reset: resetDraft } = useDirtyForm(() => [supplier.value, prices.value, reason.value, voidReason.value])

function apply(order: Order) {
  record.value = order
  supplier.value = order.supplier_id
  prices.value = order.lines.map(line => ({ id: line.id, unit_price: line.unit_price, delivery_due: line.delivery_due ?? '' }))
  reason.value = ''
  voidReason.value = ''
  voidOpen.value = false
  resetDraft()
}

async function load() {
  const current = ++request
  const pk = id.value
  loading.value = true
  error.value = ''
  notice.value = ''
  record.value = undefined
  progress.value = undefined
  receiptError.value = ''
  suppliers.value = []
  try {
    if (pk) {
      const order = await api<Order>('/purchase-orders/' + pk)
      if (current !== request) return
      apply(order)
      await loadProgress(order, current)
      if (canEdit.value) {
        const available = await api<{items: Supplier[]}>('/suppliers')
        if (current === request) suppliers.value = available.items.filter(row => row.active !== false)
      }
    }
  } catch (failure) { if (current === request) error.value = message(failure) }
  finally { if (current === request) loading.value = false }
}

watch(id, load, { immediate: true })

async function save(action: 'edit' | 'issue' | 'void') {
  if (busy.value || !record.value) return
  if (action === 'edit' && (!canEdit.value || record.value.archived || !dirty.value)) return
  const current = request
  const { id: pk, revision } = record.value
  const path = '/purchase-orders/' + pk
  const payload = action === 'edit' ? { revision, supplier_id: supplier.value, prices: prices.value, price_change_reason: reason.value }
    : action === 'void' ? { revision, reason: voidReason.value } : { revision }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api<Order>(action === 'edit' ? path : path + '/' + action,
      action === 'edit' ? 'PATCH' : 'POST', payload)
    if (current === request) {
      apply(result)
      if (action === 'issue') await loadProgress(result, current)
      if (action === 'void') progress.value = undefined
      notice.value = action === 'issue' ? 'Purchase order issued.' : action === 'void' ? 'Purchase order voided.' : 'Changes saved.'
    }
  } catch (failure) { if (current === request) error.value = message(failure) }
  finally { busy.value = false }
}
import DateInput from './DateInput.vue'
</script>

<template>
  <RouterLink v-if="id" class="back-link"  :to="back">‹ {{ t('Back to purchase orders') }}</RouterLink>
  <div class="page-heading">
    <h1>{{ record?.number ?? t(readyOnly ? 'Purchase orders ready to issue' : 'Purchase orders') }}</h1>
    <span v-if="record" class="badge" :class="{ on: record.status === 'issued' }">
      {{ t(record.archived ? 'Archived' : statuses[record.status]) }}
    </span>
    <RouterLink v-if="!id && readyOnly" class="button secondary" to="/purchase-orders">{{ t('Show all purchase orders') }}</RouterLink>
    <button v-if="!id && !readyOnly" class="secondary" type="button" :aria-pressed="archived" @click="change('archived',archived ? '' : '1')">
      {{ t(archived ? 'Show all purchase orders' : 'Archived drafts') }}
    </button>
  </div>
  <SavedViews v-if="!id && !readyOnly" :workspace="workspace" screen="purchase-orders" :filters="filters"
    :skip-default="Object.keys(filters).length > 0" @apply="router.replace({query:$event})" />
  <div v-if="!id" class="directory-filters">
    <template v-if="!readyOnly">
      <label>{{ t('Search') }}<input type="search" maxlength="120" :value="filter('q')" @change="filterBy($event,'q')" @keyup.enter="filterBy($event,'q')"></label>
      <label>{{ t('Site') }}<select :value="filter('site')" @change="filterBy($event,'site')"><option value="">{{ t('All') }}</option>
        <option v-for="site in workspace.sites" :key="site.id" :value="site.id">{{ site.name }}</option></select></label>
      <label>{{ t('Status') }}<select :value="filter('status')" @change="filterBy($event,'status')"><option value="">{{ t('All') }}</option>
        <option v-for="(label,key) in statuses" :key="key" :value="key">{{ t(label) }}</option></select></label>
    </template>
    <CsvExport :workspace="workspace" screen="purchase-orders" :filters="filters" />
  </div>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <p v-if="loading" class="muted">{{ t('Loading') }}…</p>
  <template v-else-if="record">
    <form v-validated-form class="panel record-form purchase-form" @submit.prevent="save('edit')">
      <h2>{{ record.title }}</h2>
      <p>{{ t('Source requisition') }} · <RouterLink :to="'/purchase-requests/' + record.source_pr_id">{{ record.source_pr_number }}</RouterLink></p>
      <p>{{ t('Site') }} · {{ workspace.sites.find(site => site.id === record?.site_id)?.name }}</p>
      <p>{{ t('Currency') }} · {{ record.currency }}</p>
      <EntityAutocomplete
        v-if="canEdit && !record.archived"
        data-field="supplier"
        :label="t('Supplier')"
        lookup="suppliers"
        :choices="suppliers"
        :current-label="record.supplier_name"
        :empty-value="undefined"
        required
        v-model="supplier"
      />
      <p v-else>{{ t('Supplier') }} · {{ record.supplier_name }}</p>
      <fieldset v-for="(line, index) in record.lines" :key="line.id" class="purchase-line">
        <legend>{{ t('Line item') }} {{ line.id }}</legend>
        <p>{{ line.description }}</p>
        <p>{{ t('Quantity') }} · <bdi>{{ line.quantity }}</bdi></p>
        <div class="field-wrap"><label>{{ t('Unit price') }}
          <input :placeholder="t('e.g., 125.50')" v-model="prices[index]!.unit_price" inputmode="decimal" required
            pattern="[0-9]{1,9}(\.[0-9]{1,4})?" :readonly="!canEdit || record.archived">
        </label><FieldHint :label="t('Unit price')" required :readonly="!canEdit || record.archived" /></div>
        <DateInput :label="t('Confirmed delivery date')" v-model="prices[index]!.delivery_due" type="date"
          :readonly="!canEdit || record.archived" />
        <p class="muted">{{ t('Estimated unit price') }} · <bdi>{{ line.estimated_unit_price }}</bdi></p>
        <p v-if="!priceDirty"><bdi>{{ line.amount }} {{ record.currency }}</bdi></p>
      </fieldset>
      <p v-if="!priceDirty"><strong>{{ t('Total') }}</strong> · <bdi>{{ record.total }} {{ record.currency }}</bdi></p>
      <div class="field-wrap" v-if="priceDirty"><label>{{ t('Price change reason') }}<textarea data-field="reason" :placeholder="t('Explain the reason')" v-model="reason" required maxlength="1000" rows="2"></textarea></label><FieldHint :label="t('Price change reason')" required /></div>
      <div v-if="record.status === 'draft' && !record.archived" class="actions">
        <button v-if="canEdit" class="primary" :disabled="busy || !dirty">{{ t('Save draft') }}</button>
        <button v-if="allows(workspace, 'purchasing.po.issue', record.site_id)" type="button" class="secondary"
          :disabled="busy || dirty" @click="save('issue')">{{ t('Issue') }}</button>
      </div>
      <p v-if="record.issued_at">{{ t('Issued by') }} · {{ record.issued_by }} · {{ new Date(record.issued_at).toLocaleString() }}</p>
      <p v-if="record.voided_at">{{ t('Voided by') }} · {{ record.voided_by }} · {{ new Date(record.voided_at).toLocaleString() }}</p>
      <p v-if="record.void_reason">{{ t('Void reason') }} · {{ record.void_reason }}</p>
    </form>
    <section v-if="record.status === 'issued' && allows(workspace, 'purchasing.po.void', record.site_id)" class="panel record-form approval-section">
      <button v-if="!voidOpen" class="secondary" @click="voidOpen = true">{{ t('Void') }}</button>
      <form v-validated-form v-else @submit.prevent="save('void')">
        <div class="field-wrap"><label>{{ t('Void reason') }}<textarea data-field="reason" :placeholder="t('Enter details')" v-model="voidReason" required maxlength="1000" rows="2"></textarea></label><FieldHint :label="t('Void reason')" required /></div>
        <div class="actions">
          <button class="primary" :disabled="busy">{{ t('Confirm void') }}</button>
          <button type="button" class="secondary" :disabled="busy" @click="voidOpen = false">{{ t('Cancel') }}</button>
        </div>
      </form>
    </section>
    <section v-if="record.price_changes.length" class="panel record-form approval-section">
      <h2>{{ t('Price changes') }}</h2>
      <div v-for="change in record.price_changes" :key="change.revision">
        <p><strong>{{ change.actor }}</strong> · {{ new Date(change.at).toLocaleString() }} · {{ change.reason }}</p>
        <ul><li v-for="line in change.changes" :key="line.line_id">
          {{ t('Line item') }} {{ line.line_id }} · <bdi>{{ line.from }} → {{ line.to }} ({{ line.delta }})</bdi>
        </li></ul>
      </div>
    </section>
    <section v-if="progress || receiptError" class="panel table-panel approval-section">
      <p v-if="receiptError" class="alert">{{ t(receiptError) }}</p>
      <template v-if="progress">
        <p class="empty"><strong>{{ t('Receipt status') }}</strong> · {{ t(progress.receipt_status) }}</p>
        <table><thead><tr><th>{{ t('Line item') }}</th><th>{{ t('Received quantity') }}</th><th>{{ t('Remaining') }}</th></tr></thead>
          <tbody><tr v-for="line in progress.lines" :key="line.id"><td>{{ line.description }}</td><td>{{ line.received }}</td><td>{{ line.remaining }}</td></tr></tbody></table>
        <ul v-if="progress.receipts.length"><li v-for="receipt in progress.receipts" :key="receipt.id">
          <RouterLink :to="'/inventory/receipts/' + receipt.id">{{ receipt.number }}</RouterLink></li></ul>
        <div v-if="allows(workspace, 'inventory.receive', record.site_id)" class="empty">
          <p v-if="record.currency !== 'AED'">{{ t('Goods receipts require an AED purchase order.') }}</p>
          <RouterLink v-else-if="progress.receipt_status !== 'received'" class="button primary" :to="'/inventory/receive/' + record.id">{{ t('Create goods receipt') }}</RouterLink>
        </div>
      </template>
    </section>
    <DocumentTools :key="record.id" :id="record.id" kind="po" :revision="record.revision" :archived="record.archived"
      :editable="canEdit && !dirty && !busy" @changed="load" />
  </template>
  <DataTable v-else-if="!id" :page="page" :headings="['Record number','Source requisition','Supplier','Site','Total','Status']"
    :loading="listLoading" :error="listError" @page="change('page',$event)" v-slot="{row}">
    <td><RouterLink :to="{path:'/purchase-orders/'+row.id,query:route.query}">{{ row.number }}</RouterLink></td>
    <td>{{ row.source_pr_number }}</td><td>{{ row.supplier_name }}</td>
    <td>{{ workspace.sites.find(site=>site.id===row.site_id)?.name ?? row.site_id }}</td>
    <td><bdi>{{ row.total }} {{ row.currency }}</bdi></td>
    <td>{{ t(row.archived ? 'Archived' : statuses[row.status]) }}</td>
  </DataTable>
</template>
