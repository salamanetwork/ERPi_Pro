<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from './api'
import { message, t } from './i18n'
import TableToolbar from './TableToolbar.vue'

type TrashRecord = {
  kind: string
  module: string
  id: number
  code: string
  name: string
  deleted_at: string | null
  deleted_by?: string
}

type TrashResponse = {
  records: TrashRecord[]
  kinds: string[]
}

type PurgeAllResponse = {
  purged: number
  skipped: number
}

const records = ref<TrashRecord[]>([])
const kinds = ref<string[]>([])
const selectedKind = ref('')
const selectedModule = ref('')
const dateFrom = ref('')
const dateTo = ref('')
const search = ref('')
const page = ref(1)
const size = ref(25)

const busy = ref(false)
const actionKey = ref('')
const purgingAll = ref(false)
const failure = ref('')
const notice = ref('')

const KIND_LABELS: Record<string, string> = {
  item: 'Inventory Item',
  customer: 'Customer',
  supplier: 'Supplier',
  warehouse: 'Warehouse',
  location: 'Storage Location',
  unit: 'Unit of Measure',
  'hr-reference': 'HR Reference',
}

const MODULE_LABELS: Record<string, string> = {
  inventory: 'Inventory',
  sales: 'Sales',
  purchasing: 'Purchasing',
  hr: 'Human Resources',
}

function kindLabel(kind: string): string {
  return t(KIND_LABELS[kind] || kind)
}

function moduleLabel(mod: string): string {
  return t(MODULE_LABELS[mod] || mod || '—')
}

const availableModules = computed(() => {
  const mods = new Set<string>()
  for (const r of records.value) {
    if (r.module) mods.add(r.module)
  }
  return Array.from(mods)
})

function fuzzyMatch(query: string, candidate: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  const text = candidate.toLowerCase()
  if (text.includes(q)) return true

  // Token-based fuzzy match: every whitespace-separated token must match as substring or subsequence
  const tokens = q.split(/\s+/).filter(Boolean)
  return tokens.every(token => {
    if (text.includes(token)) return true
    let ti = 0
    for (let ci = 0; ci < text.length && ti < token.length; ci++) {
      if (text[ci] === token[ti]) ti++
    }
    return ti === token.length
  })
}

const filteredRecords = computed(() => {
  const fromTs = dateFrom.value ? new Date(`${dateFrom.value}T00:00:00`).getTime() : null
  const toTs = dateTo.value ? new Date(`${dateTo.value}T23:59:59.999`).getTime() : null

  return records.value.filter(row => {
    if (selectedKind.value && row.kind !== selectedKind.value) return false
    if (selectedModule.value && row.module !== selectedModule.value) return false

    if (fromTs !== null || toTs !== null) {
      if (!row.deleted_at) return false
      const rowTs = new Date(row.deleted_at).getTime()
      if (Number.isNaN(rowTs)) return false
      if (fromTs !== null && rowTs < fromTs) return false
      if (toTs !== null && rowTs > toTs) return false
    }

    if (search.value.trim()) {
      const searchable = [
        row.code,
        row.name,
        row.kind,
        kindLabel(row.kind),
        row.module,
        moduleLabel(row.module),
        row.deleted_by || '',
      ].join(' ')
      if (!fuzzyMatch(search.value, searchable)) return false
    }

    return true
  })
})

watch([selectedKind, selectedModule, dateFrom, dateTo, search], () => {
  page.value = 1
})

const pagedRecords = computed(() => {
  const start = (page.value - 1) * size.value
  return filteredRecords.value.slice(start, start + size.value)
})

function formatDeletedAt(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function clearFilters() {
  selectedKind.value = ''
  selectedModule.value = ''
  dateFrom.value = ''
  dateTo.value = ''
  search.value = ''
  page.value = 1
}

async function load() {
  busy.value = true
  failure.value = ''
  try {
    const res = await api<TrashResponse>('/trash')
    records.value = res.records || []
    kinds.value = res.kinds || []
  } catch (error) {
    failure.value = message(error)
  } finally {
    busy.value = false
  }
}

async function restore(row: TrashRecord) {
  const key = `restore:${row.kind}:${row.id}`
  if (actionKey.value || purgingAll.value) return
  actionKey.value = key
  failure.value = ''
  notice.value = ''
  try {
    await api(`/trash/${encodeURIComponent(row.kind)}/${row.id}/restore`, 'POST', {})
    records.value = records.value.filter(r => !(r.kind === row.kind && r.id === row.id))
    notice.value = t('Record restored successfully.')
  } catch (error) {
    failure.value = message(error)
  } finally {
    actionKey.value = ''
  }
}

async function purgeOne(row: TrashRecord) {
  const label = row.code ? `${row.code} — ${row.name}` : (row.name || `#${row.id}`)
  const promptMsg = `${t('Permanently delete this record? This action cannot be undone.')}\n\n${kindLabel(row.kind)}: ${label}`
  if (!window.confirm(promptMsg)) return

  const key = `purge:${row.kind}:${row.id}`
  if (actionKey.value || purgingAll.value) return
  actionKey.value = key
  failure.value = ''
  notice.value = ''
  try {
    await api(`/trash/${encodeURIComponent(row.kind)}/${row.id}/purge`, 'POST', {})
    records.value = records.value.filter(r => !(r.kind === row.kind && r.id === row.id))
    notice.value = t('Record permanently deleted.')
  } catch (error) {
    failure.value = message(error)
  } finally {
    actionKey.value = ''
  }
}

async function purgeAll() {
  const targets = filteredRecords.value
  if (!targets.length || purgingAll.value || actionKey.value) return

  const promptMsg = `${t('Permanently delete all listed records? This action cannot be undone.')}\n\n${t('Total records')}: ${targets.length}`
  if (!window.confirm(promptMsg)) return

  purgingAll.value = true
  failure.value = ''
  notice.value = ''
  try {
    const res = await api<PurgeAllResponse>('/trash/purge-all', 'POST', {
      items: targets.map(r => ({ kind: r.kind, id: r.id })),
    })
    await load()
    if (res.skipped > 0) {
      notice.value = `${t('Purged records')}: ${res.purged}. ${t('Skipped linked records')}: ${res.skipped}.`
    } else {
      notice.value = `${t('Purged records')}: ${res.purged}.`
    }
  } catch (error) {
    failure.value = message(error)
  } finally {
    purgingAll.value = false
  }
}

onMounted(load)
</script>

<template>
  <RouterLink class="back-link" to="/settings">{{ t('Settings') }}</RouterLink>
  <div class="page-heading">
    <div>
      <h1>{{ t('Trash & Restore') }}</h1>
      <p class="muted">{{ t('Review soft-deleted master records across ERP modules and restore or permanently purge them.') }}</p>
    </div>
    <div class="actions">
      <button
        type="button"
        class="danger"
        :disabled="busy || purgingAll || !filteredRecords.length"
        @click="purgeAll"
      >
        {{ purgingAll ? t('Purging...') : `${t('Purge All')} (${filteredRecords.length})` }}
      </button>
      <button type="button" class="secondary" :disabled="busy || purgingAll" @click="load">{{ t('Refresh') }}</button>
    </div>
  </div>

  <p v-if="failure" class="alert" role="alert">{{ t(failure) }}</p>
  <p v-if="notice" class="success" role="status">{{ notice }}</p>

  <div class="panel" style="display:grid;gap:1rem;">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.75rem;align-items:end;">
      <label>
        <span>{{ t('Module') }}</span>
        <select v-model="selectedModule" :disabled="busy">
          <option value="">{{ t('All modules') }}</option>
          <option v-for="m in availableModules" :key="m" :value="m">{{ moduleLabel(m) }}</option>
        </select>
      </label>

      <label>
        <span>{{ t('Record type') }}</span>
        <select v-model="selectedKind" :disabled="busy">
          <option value="">{{ t('All record types') }}</option>
          <option v-for="k in kinds" :key="k" :value="k">{{ kindLabel(k) }}</option>
        </select>
      </label>

      <label>
        <span>{{ t('Deleted from') }}</span>
        <input v-model="dateFrom" type="date" :disabled="busy">
      </label>

      <label>
        <span>{{ t('Deleted to') }}</span>
        <input v-model="dateTo" type="date" :disabled="busy">
      </label>

      <div class="actions">
        <button
          type="button"
          class="secondary"
          :disabled="busy || (!selectedKind && !selectedModule && !dateFrom && !dateTo && !search)"
          @click="clearFilters"
        >
          {{ t('Reset filters') }}
        </button>
      </div>
    </div>

    <TableToolbar
      :page="page"
      :size="size"
      :count="filteredRecords.length"
      :query="search"
      @search="search = $event"
      @page="page = $event"
      @size="size = $event; page = 1"
    />

    <div v-if="busy && !records.length" class="muted">{{ t('Loading...') }}</div>
    <p v-else-if="!pagedRecords.length" class="muted">{{ t('No deleted records found.') }}</p>

    <div v-else class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>{{ t('Module') }}</th>
            <th>{{ t('Record type') }}</th>
            <th>{{ t('Code') }}</th>
            <th>{{ t('Name') }}</th>
            <th>{{ t('Deleted by') }}</th>
            <th>{{ t('Deleted at') }}</th>
            <th>{{ t('Actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in pagedRecords" :key="`${row.kind}-${row.id}`">
            <td>{{ moduleLabel(row.module) }}</td>
            <td>{{ kindLabel(row.kind) }}</td>
            <td><code>{{ row.code || row.id }}</code></td>
            <td><bdi>{{ row.name || '—' }}</bdi></td>
            <td><bdi>{{ row.deleted_by || '—' }}</bdi></td>
            <td>{{ formatDeletedAt(row.deleted_at) }}</td>
            <td>
              <div class="actions">
                <button
                  type="button"
                  class="primary"
                  :disabled="Boolean(actionKey) || purgingAll"
                  @click="restore(row)"
                >
                  {{ actionKey === `restore:${row.kind}:${row.id}` ? t('Restoring...') : t('Restore') }}
                </button>
                <button
                  type="button"
                  class="danger"
                  :disabled="Boolean(actionKey) || purgingAll"
                  @click="purgeOne(row)"
                >
                  {{ actionKey === `purge:${row.kind}:${row.id}` ? t('Purging...') : t('Purge') }}
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
