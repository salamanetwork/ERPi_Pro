<template>
  <div class="pos-workspace">
    <!-- Top Control Bar -->
    <div class="pos-header-bar">
      <div class="pos-header-left">
        <h2>{{ t('Point of Sale (POS)') }}</h2>
        <select v-model="selectedTerminalId" class="pos-select" @change="onTerminalChange">
          <option v-for="term in terminals" :key="term.id" :value="term.id">
            {{ term.code }} — {{ term.name }}
          </option>
        </select>
        <span v-if="activeShift" class="badge-open">
          {{ t('Shift Open') }} #{{ activeShift.id }} ({{ Number(activeShift.opening_balance).toFixed(2) }})
        </span>
        <span v-else class="badge-closed">{{ t('Shift Closed') }}</span>
      </div>

      <div class="pos-tabs">
        <button
          :class="['tab-btn', activeTab === 'cashier' ? 'active' : '']"
          @click="activeTab = 'cashier'"
        >
          {{ t('Cashier') }}
        </button>
        <button
          :class="['tab-btn', activeTab === 'catalog' ? 'active' : '']"
          @click="activeTab = 'catalog'"
        >
          {{ t('Catalog & Stock') }}
        </button>
        <button
          :class="['tab-btn', activeTab === 'terminals' ? 'active' : '']"
          @click="activeTab = 'terminals'"
        >
          {{ t('Terminals & VAT') }}
        </button>
        <button
          :class="['tab-btn', activeTab === 'shifts' ? 'active' : '']"
          @click="activeTab = 'shifts'; loadShifts()"
        >
          {{ t('Shifts') }}
        </button>
      </div>

      <div class="pos-header-actions" v-if="activeTab === 'cashier'">
        <button class="btn-secondary" @click="showRecentSales = !showRecentSales; if (showRecentSales) loadRecentSales()">
          {{ t('Recent Sales') }}
        </button>
        <a
          v-if="activeShift"
          :href="`/api/pos/shifts/${activeShift.id}/report?type=X&format=html&lang=${locale}`"
          target="_blank"
          class="btn-secondary"
        >
          {{ t('X-Report') }}
        </a>
        <button v-if="activeShift" class="btn-danger" @click="showCloseShiftModal = true">
          {{ t('Close & Reconcile Shift') }}
        </button>
        <button v-else class="btn-primary" @click="showOpenShiftModal = true">
          {{ t('Open Shift') }}
        </button>
      </div>
    </div>

    <!-- INLINE STATUS BANNER (Always preserved in UI) -->
    <div
      v-if="inlineBanner"
      :class="['inline-banner', `inline-${inlineBanner.type}`]"
      @click="inlineBanner = null"
    >
      <div class="inline-banner-main">
        <span class="notice-badge">{{ inlineBanner.badge }}</span>
        <strong>{{ inlineBanner.title }}:</strong>
        <span>{{ inlineBanner.explanation }}</span>
        <code v-if="inlineBanner.rawCode" class="inline-code">{{ inlineBanner.rawCode }}</code>
      </div>
      <button class="notice-close-x" @click.stop="inlineBanner = null">×</button>
    </div>

    <!-- Global UnifiedNoticeHost mounted at App root -->

    <!-- TAB 1: CASHIER POS -->
    <div v-if="activeTab === 'cashier'" class="pos-cashier-grid">
      <div class="pos-catalog-panel">
        <div class="pos-search-row">
          <input
            ref="searchInput"
            v-model="searchQuery"
            type="text"
            class="pos-input"
            :placeholder="t('Scan barcode or search by name / code (F2)...')"
            @keydown.enter.prevent="handleBarcodeOrSearchEnter"
          />
        </div>

        <div class="pos-items-grid">
          <div
            v-for="item in filteredCatalog"
            :key="item.id"
            class="pos-item-card"
            @click="addToCart(item)"
          >
            <div class="item-card-top">
              <span class="item-code">{{ item.code }}</span>
              <span :class="['item-stock', Number(item.stock) > 0 ? 'in-stock' : 'out-stock']">
                {{ t('Stock') }}: {{ Number(item.stock) }}
              </span>
            </div>
            <div class="item-name">{{ item.name }}</div>
            <div class="item-card-bottom">
              <span class="item-barcode">{{ item.raw_barcode || '' }}</span>
              <span class="item-price">{{ Number(item.unit_price).toFixed(2) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Cart & Settlement -->
      <div class="pos-cart-panel">
        <h3>{{ t('Current Sale Cart') }}</h3>
        <div v-if="cart.length === 0" class="empty-cart">
          {{ t('Cart is empty — scan a barcode or click an item to add') }}
        </div>
        <table v-else class="cart-table">
          <thead>
            <tr>
              <th>{{ t('Item') }}</th>
              <th>{{ t('Qty') }}</th>
              <th>{{ t('Price') }}</th>
              <th>{{ t('Total') }}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(line, idx) in cart" :key="line.item_id">
              <td>
                <div class="cart-item-title">{{ line.name }}</div>
                <small class="muted">{{ line.code }}</small>
              </td>
              <td>
                <input v-model.number="line.quantity" type="number" min="1" step="1" class="qty-input" />
              </td>
              <td>
                <input v-model.number="line.unit_price" type="number" min="0" step="0.01" class="price-input" />
              </td>
              <td>{{ (line.quantity * line.unit_price).toFixed(2) }}</td>
              <td>
                <button class="btn-remove" @click="cart.splice(idx, 1)">×</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="cart-totals">
          <div class="total-row">
            <span>{{ t('Subtotal') }}</span>
            <span>{{ subtotal.toFixed(2) }}</span>
          </div>
          <div class="total-row">
            <span>{{ t('Cart-level Discount') }}</span>
            <input v-model.number="cartDiscount" type="number" min="0" step="0.01" class="small-num-input" />
          </div>
          <div class="total-row">
            <span>{{ t('Tax (VAT)') }} ({{ currentVatRate }}%)</span>
            <span>{{ vatAmount.toFixed(2) }}</span>
          </div>
          <div class="total-row grand">
            <span>{{ t('Grand Total') }}</span>
            <span>{{ grandTotal.toFixed(2) }}</span>
          </div>
        </div>

        <div class="payment-box">
          <div class="payment-header">
            <strong>{{ t('Payment & Split Settlement') }}</strong>
            <button class="btn-link" @click="addPaymentLine">+ {{ t('Add Split Payment') }}</button>
          </div>
          <div v-for="(pay, pIdx) in payments" :key="pIdx" class="payment-row">
            <select v-model="pay.method" class="pos-select-sm">
              <option value="cash">{{ t('Cash') }}</option>
              <option value="card">{{ t('Card') }}</option>
              <option value="bank_transfer">{{ t('Bank Transfer') }}</option>
            </select>
            <input v-model.number="pay.amount" type="number" min="0" step="0.01" class="pos-input-sm" />
            <input v-if="pay.method !== 'cash'" v-model="pay.reference" type="text" :placeholder="t('Reference')" class="pos-input-sm" />
            <button v-if="payments.length > 1" class="btn-remove" @click="payments.splice(pIdx, 1)">×</button>
          </div>
          <div class="total-row">
            <span>{{ t('Total Tendered') }}: {{ totalTendered.toFixed(2) }}</span>
            <span class="change-due">{{ t('Change Due') }}: {{ changeDue.toFixed(2) }}</span>
          </div>
          <button
            class="btn-complete"
            :disabled="!activeShift || cart.length === 0 || submitting"
            @click="completeSale"
          >
            {{ t('Complete Sale & Print Receipt (F9)') }}
          </button>
        </div>
      </div>
    </div>

    <!-- TAB 2: CATALOG, PRICING & INSTANT STOCK -->
    <div v-if="activeTab === 'catalog'" class="pos-admin-panel">
      <div class="panel-card">
        <h3>{{ t('Quick Add New Inventory Item') }}</h3>
        <div class="form-grid-5">
          <input v-model="newItem.code" type="text" class="pos-input" :placeholder="t('Code (e.g. ITEM-101)')" />
          <input v-model="newItem.name" type="text" class="pos-input" :placeholder="t('Item name')" />
          <input v-model="newItem.barcode" type="text" class="pos-input" :placeholder="t('Barcode')" />
          <input v-model.number="newItem.default_price" type="number" min="0" step="0.01" class="pos-input" :placeholder="t('Default price')" />
          <input v-model.number="newItem.initial_stock" type="number" min="0" step="1" class="pos-input" :placeholder="t('Initial stock')" />
        </div>
        <div class="mt-2">
          <button class="btn-primary" @click="createCatalogItem">{{ t('Create Item') }}</button>
        </div>
      </div>

      <div class="panel-card mt-3">
        <div class="flex-between">
          <h3>{{ t('Manage Prices, Barcodes & POS Stock') }}</h3>
          <input v-model="catalogFilter" type="text" class="pos-input max-w-300" :placeholder="t('Filter items...')" />
        </div>
        <table class="admin-table mt-2">
          <thead>
            <tr>
              <th>{{ t('Code') }}</th>
              <th>{{ t('Name') }}</th>
              <th>{{ t('Barcode') }}</th>
              <th>{{ t('Default price') }}</th>
              <th>{{ t('Current Stock') }}</th>
              <th>{{ t('Adjust Stock (+/-)') }}</th>
              <th>{{ t('Actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in paginatedAdminCatalog" :key="item.id">
              <td>{{ item.code }}</td>
              <td><input v-model="item.editName" type="text" class="pos-input-sm" /></td>
              <td><input v-model="item.editBarcode" type="text" class="pos-input-sm" /></td>
              <td><input v-model.number="item.editPrice" type="number" min="0" step="0.01" class="pos-input-sm" /></td>
              <td>
                <strong :class="Number(item.stock) > 0 ? 'text-green' : 'text-muted'">
                  {{ Number(item.stock) }}
                </strong>
              </td>
              <td>
                <div class="stock-adj-box">
                  <input v-model.number="item.stockDelta" type="number" step="1" class="qty-input" placeholder="+10" />
                  <button class="btn-secondary-sm" @click="adjustItemStock(item)">{{ t('Apply') }}</button>
                </div>
              </td>
              <td>
                <button class="btn-primary-sm" @click="saveCatalogItem(item)">{{ t('Save') }}</button>
              </td>
            </tr>
            <tr v-if="paginatedAdminCatalog.length === 0">
              <td colspan="7" class="text-center text-muted">{{ t('No matching records found.') }}</td>
            </tr>
          </tbody>
        </table>
        <div class="table-pagination-bar">
          <div class="pagination-info">
            {{ t('Showing') }} {{ catalogRangeStart }}–{{ catalogRangeEnd }} {{ t('of') }} {{ adminFilteredCatalog.length }}
          </div>
          <div class="pagination-controls">
            <label class="pagination-size-label">
              <span>{{ t('Rows') }}:</span>
              <select v-model.number="catalogPageSize" class="pos-select-xs">
                <option :value="10">10</option>
                <option :value="20">20</option>
                <option :value="50">50</option>
              </select>
            </label>
            <button class="btn-secondary-sm" :disabled="catalogPage <= 1" @click="catalogPage--">{{ t('Previous') }}</button>
            <span class="pagination-page-indicator">{{ t('Page') }} {{ catalogPage }} / {{ catalogTotalPages }}</span>
            <button class="btn-secondary-sm" :disabled="catalogPage >= catalogTotalPages" @click="catalogPage++">{{ t('Next') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 3: TERMINALS & VAT -->
    <div v-if="activeTab === 'terminals'" class="pos-admin-panel">
      <div class="panel-card">
        <div class="flex-between">
          <h3>{{ t('POS Terminals & VAT Configuration') }}</h3>
          <input v-model="terminalFilter" type="text" class="pos-input max-w-300" :placeholder="t('Filter terminals...')" />
        </div>
        <table class="admin-table mt-2">
          <thead>
            <tr>
              <th>{{ t('Code') }}</th>
              <th>{{ t('Name') }}</th>
              <th>{{ t('VAT Rate (%)') }}</th>
              <th>{{ t('Warehouse') }}</th>
              <th>{{ t('Location') }}</th>
              <th>{{ t('Actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="term in paginatedTerminals" :key="term.id">
              <td>{{ term.code }}</td>
              <td><input v-model="term.editName" type="text" class="pos-input-sm" /></td>
              <td><input v-model.number="term.editVat" type="number" min="0" step="0.01" class="qty-input" /></td>
              <td>{{ term.warehouse_code || term.warehouse }}</td>
              <td>{{ term.location_code || term.location }}</td>
              <td>
                <button class="btn-primary-sm" @click="saveTerminal(term)">{{ t('Save') }}</button>
              </td>
            </tr>
            <tr v-if="paginatedTerminals.length === 0">
              <td colspan="6" class="text-center text-muted">{{ t('No matching records found.') }}</td>
            </tr>
          </tbody>
        </table>
        <div class="table-pagination-bar">
          <div class="pagination-info">
            {{ t('Showing') }} {{ terminalRangeStart }}–{{ terminalRangeEnd }} {{ t('of') }} {{ filteredTerminals.length }}
          </div>
          <div class="pagination-controls">
            <label class="pagination-size-label">
              <span>{{ t('Rows') }}:</span>
              <select v-model.number="terminalPageSize" class="pos-select-xs">
                <option :value="10">10</option>
                <option :value="20">20</option>
                <option :value="50">50</option>
              </select>
            </label>
            <button class="btn-secondary-sm" :disabled="terminalPage <= 1" @click="terminalPage--">{{ t('Previous') }}</button>
            <span class="pagination-page-indicator">{{ t('Page') }} {{ terminalPage }} / {{ terminalTotalPages }}</span>
            <button class="btn-secondary-sm" :disabled="terminalPage >= terminalTotalPages" @click="terminalPage++">{{ t('Next') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 4: SHIFTS -->
    <div v-if="activeTab === 'shifts'" class="pos-admin-panel">
      <div class="panel-card">
        <div class="flex-between">
          <h3>{{ t('Shift History & Reports') }}</h3>
          <div class="table-filter-group">
            <select v-model="shiftStatusFilter" class="pos-select-sm">
              <option value="all">{{ t('All Statuses') }}</option>
              <option value="open">{{ t('Open') }}</option>
              <option value="closed">{{ t('Closed') }}</option>
            </select>
            <input v-model="shiftFilter" type="text" class="pos-input max-w-240" :placeholder="t('Filter shifts...')" />
          </div>
        </div>
        <table class="admin-table mt-2">
          <thead>
            <tr>
              <th>ID</th>
              <th>{{ t('Terminal') }}</th>
              <th>{{ t('Status') }}</th>
              <th>{{ t('Opening') }}</th>
              <th>{{ t('Closing') }}</th>
              <th>{{ t('Reports') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="sh in paginatedShifts" :key="sh.id">
              <td>#{{ sh.id }}</td>
              <td>{{ sh.terminal_code }}</td>
              <td>{{ sh.status }}</td>
              <td>{{ Number(sh.opening_balance).toFixed(2) }}</td>
              <td>{{ sh.closing_balance != null ? Number(sh.closing_balance).toFixed(2) : '—' }}</td>
              <td>
                <a :href="`/api/pos/shifts/${sh.id}/report?type=X&format=html&lang=${locale}`" target="_blank" class="btn-link">X-Report</a>
                |
                <a :href="`/api/pos/shifts/${sh.id}/report?type=Z&format=html&lang=${locale}`" target="_blank" class="btn-link">Z-Report</a>
              </td>
            </tr>
            <tr v-if="paginatedShifts.length === 0">
              <td colspan="6" class="text-center text-muted">{{ t('No matching records found.') }}</td>
            </tr>
          </tbody>
        </table>
        <div class="table-pagination-bar">
          <div class="pagination-info">
            {{ t('Showing') }} {{ shiftRangeStart }}–{{ shiftRangeEnd }} {{ t('of') }} {{ filteredShifts.length }}
          </div>
          <div class="pagination-controls">
            <label class="pagination-size-label">
              <span>{{ t('Rows') }}:</span>
              <select v-model.number="shiftPageSize" class="pos-select-xs">
                <option :value="10">10</option>
                <option :value="20">20</option>
                <option :value="50">50</option>
              </select>
            </label>
            <button class="btn-secondary-sm" :disabled="shiftPage <= 1" @click="shiftPage--">{{ t('Previous') }}</button>
            <span class="pagination-page-indicator">{{ t('Page') }} {{ shiftPage }} / {{ shiftTotalPages }}</span>
            <button class="btn-secondary-sm" :disabled="shiftPage >= shiftTotalPages" @click="shiftPage++">{{ t('Next') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Open Shift Modal -->
    <div v-if="showOpenShiftModal" class="modal-backdrop">
      <div class="modal-box">
        <h3>{{ t('Open Shift') }}</h3>
        <label>{{ t('Opening Cash Balance') }}</label>
        <input v-model.number="openingCashInput" type="number" min="0" step="0.01" class="pos-input mt-2" />
        <div class="modal-actions">
          <button class="btn-secondary" @click="showOpenShiftModal = false">{{ t('Cancel') }}</button>
          <button class="btn-primary" @click="openShift">{{ t('Open Shift') }}</button>
        </div>
      </div>
    </div>

    <!-- Close Shift Modal -->
    <div v-if="showCloseShiftModal" class="modal-backdrop">
      <div class="modal-box">
        <h3>{{ t('Close & Reconcile Shift') }}</h3>
        <label>{{ t('Actual Counted Cash') }}</label>
        <input v-model.number="closingCashInput" type="number" min="0" step="0.01" class="pos-input mt-2" />
        <div class="modal-actions">
          <button class="btn-secondary" @click="showCloseShiftModal = false">{{ t('Cancel') }}</button>
          <button class="btn-danger" @click="closeShift">{{ t('Confirm Close') }}</button>
        </div>
      </div>
    </div>

    <!-- Recent Sales Drawer -->
    <div v-if="showRecentSales" class="modal-backdrop">
      <div class="modal-box modal-wide">
        <div class="flex-between">
          <h3>{{ t('Recent Sales') }}</h3>
          <button class="btn-secondary" @click="showRecentSales = false">{{ t('Close') }}</button>
        </div>
        <table class="admin-table mt-2">
          <thead>
            <tr>
              <th>{{ t('Receipt') }}</th>
              <th>{{ t('Total') }}</th>
              <th>{{ t('Status') }}</th>
              <th>{{ t('Print') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="sale in recentSales" :key="sale.id">
              <td>{{ sale.receipt_number }}</td>
              <td>{{ Number(sale.grand_total).toFixed(2) }}</td>
              <td>{{ sale.status }}</td>
              <td>
                <a :href="`/pos/print/receipt/${sale.id}?lang=${locale}`" target="_blank" class="btn-link">{{ t('Receipt') }}</a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { locale, t } from '../../../frontend/src/i18n'
import { notify, type NoticeType, type UnifiedNotice } from '../../../frontend/src/useUnifiedNotice'

const inlineBanner = ref<UnifiedNotice | null>(null)

function showNotice(
  type: NoticeType,
  titleKey: string,
  explanationKey: string,
  options: { rawCode?: string; hintKey?: string; suffix?: string } = {}
) {
  inlineBanner.value = notify(type, titleKey, explanationKey, options)
}

function getCsrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return m ? decodeURIComponent(m[1]) : ''
}

async function apiFetch(url: string, options: RequestInit = {}) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  }
  if (options.method && options.method !== 'GET') {
    headers['X-CSRFToken'] = getCsrfToken()
  }
  const resp = await fetch(url, { ...options, headers, credentials: 'same-origin' })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const rawCode = data.error || data.code || data.detail || `HTTP_${resp.status}`
    const err: any = new Error(rawCode)
    err.rawCode = rawCode
    err.status = resp.status
    throw err
  }
  return data
}

const activeTab = ref<'cashier' | 'catalog' | 'terminals' | 'shifts'>('cashier')
const terminals = ref<any[]>([])
const selectedTerminalId = ref<number | null>(null)
const activeShift = ref<any | null>(null)
const catalog = ref<any[]>([])
const searchQuery = ref('')
const catalogFilter = ref('')
const catalogPage = ref(1)
const catalogPageSize = ref(10)

const terminalFilter = ref('')
const terminalPage = ref(1)
const terminalPageSize = ref(10)

const shiftFilter = ref('')
const shiftStatusFilter = ref<'all' | 'open' | 'closed'>('all')
const shiftPage = ref(1)
const shiftPageSize = ref(10)

watch([catalogFilter, catalogPageSize], () => { catalogPage.value = 1 })
watch([terminalFilter, terminalPageSize], () => { terminalPage.value = 1 })
watch([shiftFilter, shiftStatusFilter, shiftPageSize], () => { shiftPage.value = 1 })
const cart = ref<any[]>([])
const cartDiscount = ref(0)
const payments = ref<any[]>([{ method: 'cash', amount: 0, reference: '' }])
const submitting = ref(false)

const showOpenShiftModal = ref(false)
const openingCashInput = ref(500)
const showCloseShiftModal = ref(false)
const closingCashInput = ref(0)
const showRecentSales = ref(false)
const recentSales = ref<any[]>([])
const shiftsList = ref<any[]>([])

const newItem = ref({
  code: '',
  name: '',
  barcode: '',
  default_price: 0,
  initial_stock: 0,
})

const currentTerminal = computed(() => terminals.value.find((x) => x.id === selectedTerminalId.value) || null)
const currentVatRate = computed(() => Number(currentTerminal.value?.vat_rate || 5))

const filteredCatalog = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return catalog.value
  return catalog.value.filter(
    (i) =>
      String(i.name || '').toLowerCase().includes(q) ||
      String(i.code || '').toLowerCase().includes(q) ||
      String(i.raw_barcode || '').toLowerCase().includes(q)
  )
})

const adminFilteredCatalog = computed(() => {
  const q = catalogFilter.value.trim().toLowerCase()
  if (!q) return catalog.value
  return catalog.value.filter(
    (i) =>
      String(i.name || '').toLowerCase().includes(q) ||
      String(i.code || '').toLowerCase().includes(q) ||
      String(i.raw_barcode || '').toLowerCase().includes(q)
  )
})

const catalogTotalPages = computed(() => Math.max(1, Math.ceil(adminFilteredCatalog.value.length / catalogPageSize.value)))
const paginatedAdminCatalog = computed(() => {
  const page = Math.min(catalogPage.value, catalogTotalPages.value)
  const start = (page - 1) * catalogPageSize.value
  return adminFilteredCatalog.value.slice(start, start + catalogPageSize.value)
})
const catalogRangeStart = computed(() => adminFilteredCatalog.value.length === 0 ? 0 : (Math.min(catalogPage.value, catalogTotalPages.value) - 1) * catalogPageSize.value + 1)
const catalogRangeEnd = computed(() => Math.min(adminFilteredCatalog.value.length, Math.min(catalogPage.value, catalogTotalPages.value) * catalogPageSize.value))

const filteredTerminals = computed(() => {
  const q = terminalFilter.value.trim().toLowerCase()
  if (!q) return terminals.value
  return terminals.value.filter(
    (term) =>
      String(term.code || '').toLowerCase().includes(q) ||
      String(term.name || term.editName || '').toLowerCase().includes(q) ||
      String(term.warehouse_code || term.warehouse || '').toLowerCase().includes(q) ||
      String(term.location_code || term.location || '').toLowerCase().includes(q)
  )
})
const terminalTotalPages = computed(() => Math.max(1, Math.ceil(filteredTerminals.value.length / terminalPageSize.value)))
const paginatedTerminals = computed(() => {
  const page = Math.min(terminalPage.value, terminalTotalPages.value)
  const start = (page - 1) * terminalPageSize.value
  return filteredTerminals.value.slice(start, start + terminalPageSize.value)
})
const terminalRangeStart = computed(() => filteredTerminals.value.length === 0 ? 0 : (Math.min(terminalPage.value, terminalTotalPages.value) - 1) * terminalPageSize.value + 1)
const terminalRangeEnd = computed(() => Math.min(filteredTerminals.value.length, Math.min(terminalPage.value, terminalTotalPages.value) * terminalPageSize.value))

const filteredShifts = computed(() => {
  const q = shiftFilter.value.trim().toLowerCase()
  return shiftsList.value.filter((sh) => {
    if (shiftStatusFilter.value !== 'all' && String(sh.status || '').toLowerCase() !== shiftStatusFilter.value) {
      return false
    }
    if (!q) return true
    return (
      String(sh.id || '').includes(q) ||
      `#${sh.id}`.includes(q) ||
      String(sh.terminal_code || '').toLowerCase().includes(q) ||
      String(sh.status || '').toLowerCase().includes(q)
    )
  })
})
const shiftTotalPages = computed(() => Math.max(1, Math.ceil(filteredShifts.value.length / shiftPageSize.value)))
const paginatedShifts = computed(() => {
  const page = Math.min(shiftPage.value, shiftTotalPages.value)
  const start = (page - 1) * shiftPageSize.value
  return filteredShifts.value.slice(start, start + shiftPageSize.value)
})
const shiftRangeStart = computed(() => filteredShifts.value.length === 0 ? 0 : (Math.min(shiftPage.value, shiftTotalPages.value) - 1) * shiftPageSize.value + 1)
const shiftRangeEnd = computed(() => Math.min(filteredShifts.value.length, Math.min(shiftPage.value, shiftTotalPages.value) * shiftPageSize.value))

const subtotal = computed(() =>
  cart.value.reduce((acc, l) => acc + Number(l.quantity || 0) * Number(l.unit_price || 0), 0)
)
const taxableAmount = computed(() => Math.max(0, subtotal.value - Number(cartDiscount.value || 0)))
const vatAmount = computed(() => +(taxableAmount.value * (currentVatRate.value / 100)).toFixed(2))
const grandTotal = computed(() => +(taxableAmount.value + vatAmount.value).toFixed(2))
const totalTendered = computed(() =>
  payments.value.reduce((acc, p) => acc + Number(p.amount || 0), 0)
)
const changeDue = computed(() => Math.max(0, +(totalTendered.value - grandTotal.value).toFixed(2)))

function handleApiError(err: any) {
  const rawCode = err?.rawCode || err?.message || 'unknown_error'
  const isFault = Number(err?.status) >= 500
  showNotice(
    isFault ? 'fault' : 'error',
    'Operation Failed',
    'Server rejected the request with code:',
    { rawCode, suffix: rawCode }
  )
}

async function loadBootstrap() {
  try {
    const data = await apiFetch('/api/pos/terminals')
    const list = Array.isArray(data) ? data : (data.items || data.terminals || [])
    terminals.value = list.map((t: any) => ({
      ...t,
      editName: t.name,
      editVat: Number(t.vat_rate || 5),
    }))
    if (!selectedTerminalId.value && terminals.value.length > 0) {
      selectedTerminalId.value = terminals.value[0].id
    }
    await onTerminalChange()
  } catch (err: any) {
    handleApiError(err)
  }
}

async function onTerminalChange() {
  if (!selectedTerminalId.value) return
  try {
    const [shiftRes, catRes] = await Promise.all([
      apiFetch(`/api/pos/shifts?terminal_id=${selectedTerminalId.value}`),
      apiFetch(`/api/pos/catalog?terminal_id=${selectedTerminalId.value}`),
    ])
    const allShifts = Array.isArray(shiftRes) ? shiftRes : (shiftRes.items || shiftRes.shifts || [])
    shiftsList.value = allShifts
    activeShift.value = allShifts.find((s: any) => s.status === 'open') || null

    const catItems = Array.isArray(catRes) ? catRes : (catRes.items || [])
    catalog.value = catItems.map((i: any) => ({
      ...i,
      editName: i.name,
      editBarcode: i.raw_barcode || '',
      editPrice: Number(i.unit_price || i.price || 0),
      stockDelta: 0,
    }))
  } catch (err: any) {
    handleApiError(err)
  }
}

function addToCart(item: any) {
  if (Number(item.stock) <= 0) {
    showNotice(
      'warning',
      'Out of Stock Warning',
      'This item currently has 0 stock at this POS location.',
      {
        hintKey: 'Add stock in Catalog & Stock before completing the sale if stock enforcement is active.',
        suffix: item.code,
      }
    )
  }
  const existing = cart.value.find((c) => c.item_id === item.id)
  if (existing) {
    existing.quantity += 1
  } else {
    cart.value.push({
      item_id: item.id,
      code: item.code,
      name: item.name,
      quantity: 1,
      unit_price: Number(item.unit_price || item.price || 0),
    })
  }
  if (payments.value.length === 1 && payments.value[0].method === 'cash') {
    payments.value[0].amount = grandTotal.value
  }
}

function handleBarcodeOrSearchEnter() {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return
  const exact = catalog.value.find(
    (i) => String(i.raw_barcode || '').toLowerCase() === q || String(i.code || '').toLowerCase() === q
  )
  if (exact) {
    addToCart(exact)
    searchQuery.value = ''
  } else if (filteredCatalog.value.length === 1) {
    addToCart(filteredCatalog.value[0])
    searchQuery.value = ''
  } else {
    showNotice(
      'information',
      'Barcode Not Found',
      'No item matched the scanned barcode or code.',
      { hintKey: 'Verify the code or create the item in Catalog & Stock.', suffix: searchQuery.value }
    )
  }
}

function addPaymentLine() {
  payments.value.push({ method: 'card', amount: 0, reference: '' })
}

async function completeSale() {
  if (!activeShift.value) {
    showNotice('warning', 'Shift is closed', 'Open a shift before recording a sale.')
    return
  }
  if (cart.value.length === 0) {
    showNotice('warning', 'Cart is empty', 'Add at least one item before completing the sale.')
    return
  }
  submitting.value = true
  try {
    const res = await apiFetch('/api/pos/sales', {
      method: 'POST',
      body: JSON.stringify({
        shift_id: activeShift.value.id,
        discount_amount: Number(cartDiscount.value || 0),
        lines: cart.value.map((l) => ({
          item_id: l.item_id,
          quantity: l.quantity,
          unit_price: l.unit_price,
        })),
        payments: payments.value.filter((p) => Number(p.amount) > 0),
      }),
    })
    const saleObj = res.sale || res
    showNotice(
      'confirmation',
      'Sale Confirmed & Receipt Issued',
      'Sale recorded and stock quantities updated.',
      { suffix: saleObj?.receipt_number || '' }
    )
    cart.value = []
    cartDiscount.value = 0
    payments.value = [{ method: 'cash', amount: 0, reference: '' }]
    await onTerminalChange()
    if (saleObj?.id) {
      window.open(`/pos/print/receipt/${saleObj.id}?lang=${locale.value}`, '_blank')
    }
  } catch (err: any) {
    handleApiError(err)
  } finally {
    submitting.value = false
  }
}

async function createCatalogItem() {
  if (!newItem.value.code.trim() || !newItem.value.name.trim()) {
    showNotice(
      'warning',
      'Missing Item Fields',
      'Please enter both item code and item name.'
    )
    return
  }
  try {
    const created = await apiFetch('/admin/catalog', {
      method: 'POST',
      body: JSON.stringify({
        action: 'save_item',
        code: newItem.value.code,
        name: newItem.value.name,
        barcode: newItem.value.barcode,
        default_price: newItem.value.default_price,
      }),
    })
    if (Number(newItem.value.initial_stock) !== 0 && created?.id) {
      await apiFetch('/admin/catalog', {
        method: 'POST',
        body: JSON.stringify({
          action: 'adjust_stock',
          terminal_id: selectedTerminalId.value,
          item_id: created.id,
          delta: Number(newItem.value.initial_stock),
        }),
      })
    }
    const createdCode = newItem.value.code
    newItem.value = { code: '', name: '', barcode: '', default_price: 0, initial_stock: 0 }
    showNotice('saved', 'Item Created & Saved', 'New inventory item added to catalog.', { suffix: createdCode })
    await onTerminalChange()
  } catch (err: any) {
    handleApiError(err)
  }
}

async function saveCatalogItem(item: any) {
  try {
    await apiFetch('/admin/catalog', {
      method: 'POST',
      body: JSON.stringify({
        action: 'save_item',
        id: item.id,
        name: item.editName,
        barcode: item.editBarcode,
        default_price: item.editPrice,
      }),
    })
    const delta = Number(item.stockDelta || 0)
    if (delta !== 0) {
      await apiFetch('/admin/catalog', {
        method: 'POST',
        body: JSON.stringify({
          action: 'adjust_stock',
          terminal_id: selectedTerminalId.value,
          item_id: item.id,
          delta,
        }),
      })
    }
    showNotice('saved', 'Item Changes Saved', 'Item name, barcode, and price were saved.', { suffix: item.code })
    await onTerminalChange()
  } catch (err: any) {
    handleApiError(err)
  }
}

async function adjustItemStock(item: any) {
  const delta = Number(item.stockDelta || 0)
  if (!delta) {
    showNotice(
      'warning',
      'No Quantity Entered',
      'Enter a non-zero stock adjustment value first.',
      { suffix: item.code }
    )
    return
  }
  try {
    await apiFetch('/admin/catalog', {
      method: 'POST',
      body: JSON.stringify({
        action: 'adjust_stock',
        terminal_id: selectedTerminalId.value,
        item_id: item.id,
        delta,
      }),
    })
    showNotice(
      'saved',
      'Stock Balance Updated',
      'POS stock quantity has been updated.',
      { suffix: `${item.code}: ${delta > 0 ? '+' + delta : delta}` }
    )
    await onTerminalChange()
  } catch (err: any) {
    handleApiError(err)
  }
}

async function saveTerminal(term: any) {
  try {
    await apiFetch(`/terminals/${term.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        name: term.editName,
        vat_rate: term.editVat,
      }),
    })
    showNotice('saved', 'Terminal Settings Saved', 'Terminal name and VAT rate were updated.', { suffix: term.code })
    await loadBootstrap()
  } catch (err: any) {
    handleApiError(err)
  }
}

async function openShift() {
  try {
    await apiFetch('/api/pos/shifts', {
      method: 'POST',
      body: JSON.stringify({
        terminal_id: selectedTerminalId.value,
        opening_balance: openingCashInput.value,
      }),
    })
    showOpenShiftModal.value = false
    showNotice(
      'confirmation',
      'Shift Opened',
      'POS shift is now open and ready for sales.',
      { suffix: Number(openingCashInput.value).toFixed(2) }
    )
    await onTerminalChange()
  } catch (err: any) {
    handleApiError(err)
  }
}

async function closeShift() {
  if (!activeShift.value) return
  try {
    const shId = activeShift.value.id
    await apiFetch(`/api/pos/shifts/${shId}/close`, {
      method: 'POST',
      body: JSON.stringify({ closing_balance: closingCashInput.value }),
    })
    showCloseShiftModal.value = false
    showNotice(
      'confirmation',
      'Shift Closed & Reconciled',
      'Shift closed and Z-Report generated.',
      { suffix: `#${shId}` }
    )
    await onTerminalChange()
    window.open(`/api/pos/shifts/${shId}/report?type=Z&format=html&lang=${locale.value}`, '_blank')
  } catch (err: any) {
    handleApiError(err)
  }
}

async function loadRecentSales() {
  try {
    const res = await apiFetch(`/api/pos/sales?terminal_id=${selectedTerminalId.value}`)
    recentSales.value = Array.isArray(res) ? res : (res.items || res.sales || [])
  } catch (err: any) {
    handleApiError(err)
  }
}

async function loadShifts() {
  try {
    const res = await apiFetch(`/api/pos/shifts?terminal_id=${selectedTerminalId.value}`)
    shiftsList.value = Array.isArray(res) ? res : (res.items || res.shifts || [])
  } catch (err: any) {
    handleApiError(err)
  }
}

onMounted(loadBootstrap)
</script>

<style scoped>
.pos-workspace { display: flex; flex-direction: column; gap: 14px; }
.pos-header-bar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; background: var(--surface-card, #162231); padding: 14px 18px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); }
.pos-header-left, .pos-tabs, .pos-header-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tab-btn { background: rgba(255,255,255,0.06); color: #cbd5e1; border: 1px solid rgba(255,255,255,0.1); padding: 7px 14px; border-radius: 8px; cursor: pointer; font-weight: 600; }
.tab-btn.active { background: #38bdf8; color: #0f172a; border-color: #38bdf8; }
.badge-open { background: #bbf7d0; color: #14532d; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }
.badge-closed { background: #fecaca; color: #7f1d1d; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }

/* Inline Persistent Banner */
.inline-banner { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 14px; border-radius: 8px; border: 1px solid #38bdf8; background: rgba(15, 23, 42, 0.9); font-size: 13px; cursor: pointer; }
.inline-banner-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.inline-code { background: rgba(0,0,0,0.4); padding: 2px 6px; border-radius: 4px; font-size: 12px; }
.inline-error, .inline-fault { border-color: #ef4444; background: rgba(239,68,68,0.14); }
.inline-warning { border-color: #f59e0b; background: rgba(245,158,11,0.14); }
.inline-confirmation { border-color: #3b82f6; background: rgba(59,130,246,0.14); }
.inline-information { border-color: #06b6d4; background: rgba(6,182,212,0.14); }
.inline-saved { border-color: #22c55e; background: rgba(34,197,94,0.14); }

/* Unified Notification Modal & Top-Anchored Toast Styles */
.notice-z { z-index: 9999; }
.notice-modal { width: 480px; max-width: 94vw; background: #0f172a; border-radius: 12px; border: 2px solid #38bdf8; box-shadow: 0 20px 50px rgba(0,0,0,0.8); overflow: hidden; }
.notice-modal-header { display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; background: rgba(255,255,255,0.04); border-bottom: 1px solid rgba(255,255,255,0.08); }
.notice-title-group { display: flex; align-items: center; gap: 10px; }
.notice-title-group h3 { margin: 0; font-size: 16px; }
.notice-badge { font-size: 11px; font-weight: 800; padding: 3px 8px; border-radius: 6px; letter-spacing: 0.4px; }
.notice-close-x { background: none; border: none; color: #94a3b8; font-size: 20px; cursor: pointer; line-height: 1; }
.notice-modal-body { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.notice-raw-box { background: rgba(0,0,0,0.35); padding: 7px 10px; border-radius: 6px; font-size: 13px; display: flex; align-items: center; gap: 8px; }
.notice-raw-label { color: #94a3b8; }
.notice-explanation { margin: 0; font-size: 14px; line-height: 1.6; color: #f1f5f9; }
.notice-hint { background: rgba(56,189,248,0.1); border-inline-start: 3px solid #38bdf8; padding: 8px 12px; border-radius: 6px; font-size: 13px; color: #bae6fd; }
.notice-modal-footer { padding: 12px 18px; background: rgba(255,255,255,0.03); border-top: 1px solid rgba(255,255,255,0.08); display: flex; justify-content: space-between; align-items: center; }

.toast-container { position: fixed; top: 68px; inset-inline-end: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 10px; width: 400px; max-width: 92vw; pointer-events: auto; }
.toast-card { background: #0f172a; border-radius: 10px; border: 2px solid #38bdf8; padding: 12px 14px; box-shadow: 0 14px 36px rgba(0,0,0,0.85); cursor: pointer; display: flex; flex-direction: column; gap: 6px; }
.toast-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.toast-head-left { display: flex; align-items: center; gap: 8px; }
.toast-code { font-size: 12px; color: #94a3b8; }
.toast-desc { font-size: 13px; color: #f8fafc; line-height: 1.45; }
.toast-hint { font-size: 12px; color: #7dd3fc; }

/* Color Variants */
.notice-error, .notice-fault { border-color: #ef4444; }
.notice-error .notice-badge, .notice-fault .notice-badge { background: #ef4444; color: #fff; }
.notice-warning { border-color: #f59e0b; }
.notice-warning .notice-badge { background: #f59e0b; color: #0f172a; }
.notice-confirmation { border-color: #3b82f6; }
.notice-confirmation .notice-badge { background: #3b82f6; color: #fff; }
.notice-information { border-color: #06b6d4; }
.notice-information .notice-badge { background: #06b6d4; color: #0f172a; }
.notice-saved { border-color: #22c55e; }
.notice-saved .notice-badge { background: #22c55e; color: #0f172a; }

.pos-cashier-grid { display: grid; grid-template-columns: 1.35fr 1fr; gap: 16px; }
.pos-catalog-panel, .pos-cart-panel, .panel-card { background: var(--surface-card, #162231); padding: 16px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); }
.pos-items-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 10px; margin-top: 12px; max-height: 62vh; overflow-y: auto; }
.pos-item-card { background: #ffffff; color: #0f172a; border-radius: 8px; padding: 10px; cursor: pointer; display: flex; flex-direction: column; justify-content: space-between; min-height: 112px; }
.item-card-top, .item-card-bottom, .total-row, .payment-header, .flex-between { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.item-code { font-size: 11px; color: #64748b; font-weight: 600; }
.item-stock { font-size: 11px; font-weight: 700; }
.in-stock { color: #16a34a; }
.out-stock { color: #94a3b8; }
.item-name { font-weight: 700; font-size: 13px; margin: 6px 0; }
.item-barcode { font-size: 11px; color: #64748b; }
.item-price { font-weight: 800; color: #1d4ed8; font-size: 14px; }
.pos-input, .pos-select { width: 100%; background: #0f172a; color: #f8fafc; border: 1px solid rgba(255,255,255,0.16); padding: 8px 10px; border-radius: 7px; }
.pos-input-sm, .pos-select-sm { background: #0f172a; color: #f8fafc; border: 1px solid rgba(255,255,255,0.16); padding: 6px 8px; border-radius: 6px; width: 100%; }
.qty-input, .price-input, .small-num-input { width: 74px; background: #0f172a; color: #f8fafc; border: 1px solid rgba(255,255,255,0.16); padding: 5px 6px; border-radius: 6px; }
.cart-table, .admin-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.cart-table th, .cart-table td, .admin-table th, .admin-table td { padding: 8px 6px; border-bottom: 1px solid rgba(255,255,255,0.08); text-align: start; }
.cart-totals { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; border-top: 1px solid rgba(255,255,255,0.12); padding-top: 10px; }
.total-row.grand { font-size: 17px; font-weight: 800; }
.payment-box { margin-top: 14px; display: flex; flex-direction: column; gap: 8px; }
.payment-row { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 6px; align-items: center; }
.btn-primary, .btn-primary-sm { background: #38bdf8; color: #0f172a; border: none; padding: 8px 14px; border-radius: 7px; font-weight: 700; cursor: pointer; }
.btn-primary-sm, .btn-secondary-sm { padding: 5px 10px; font-size: 12px; }
.btn-secondary, .btn-secondary-sm { background: #334155; color: #f8fafc; border: none; padding: 8px 12px; border-radius: 7px; font-weight: 600; cursor: pointer; text-decoration: none; }
.btn-danger { background: #ef4444; color: #fff; border: none; padding: 8px 12px; border-radius: 7px; font-weight: 700; cursor: pointer; }
.btn-complete { background: #2563eb; color: #fff; border: none; padding: 11px; border-radius: 8px; font-weight: 700; cursor: pointer; margin-top: 6px; }
.btn-complete:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-remove { background: #ef4444; color: #fff; border: none; border-radius: 5px; width: 24px; height: 24px; cursor: pointer; }
.btn-link { background: none; border: none; color: #38bdf8; cursor: pointer; text-decoration: underline; font-size: 12px; }
.form-grid-5 { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-top: 10px; }
.stock-adj-box { display: flex; gap: 6px; align-items: center; }
.text-green { color: #4ade80; }
.text-muted { color: #94a3b8; }
.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 14px; }
.max-w-300 { max-width: 300px; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.65); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal-box { background: #1e293b; padding: 20px; border-radius: 10px; width: 380px; border: 1px solid rgba(255,255,255,0.12); }
.modal-wide { width: 640px; max-width: 92vw; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }

.table-filter-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.max-w-240 {
  max-width: 240px;
}
.pos-select-sm,
.pos-select-xs {
  background: var(--surface-2, #0f172a);
  color: var(--text-1, #f8fafc);
  border: 1px solid var(--border-color, #334155);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.875rem;
}
.pos-select-xs {
  padding: 3px 6px;
  font-size: 0.8rem;
}
.table-pagination-bar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color, #1e293b);
  font-size: 0.85rem;
}
.pagination-info {
  color: var(--text-muted, #94a3b8);
}
.pagination-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}
.pagination-size-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted, #94a3b8);
}
.pagination-page-indicator {
  font-weight: 600;
  padding: 0 4px;
}
.text-center {
  text-align: center;
}
</style>
