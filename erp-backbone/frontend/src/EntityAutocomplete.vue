<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { api } from './api'
import { t } from './i18n'

export type LookupItem = {
  id: number | string
  code?: string
  name: string
  subtitle?: string
  label?: string
  meta?: Record<string, unknown>
}

const props = withDefaults(defineProps<{
  label?: string
  lookup?: string
  choices?: Record<string, any>[]
  params?: Record<string, string | number | boolean | undefined | null>
  dataField?: string
  currentLabel?: string
  placeholder?: string
  required?: boolean
  disabled?: boolean
  clearable?: boolean
  emptyValue?: null | undefined | '' | 0
  inline?: boolean
}>(), {
  clearable: true,
  emptyValue: null,
  inline: false,
})

const model = defineModel<number | string | null | undefined>()
const emit = defineEmits<{
  (e: 'select', item: LookupItem | null): void
}>()

const root = ref<HTMLElement>()
const inputEl = ref<HTMLInputElement>()
const inputId = useId()
const listId = useId()

const open = ref(false)
const query = ref('')
const userTyped = ref(false)
const remoteItems = ref<LookupItem[]>([])
const hydratedItem = ref<LookupItem | null>(null)
const pending = ref(false)
const activeIndex = ref(-1)
let requestTicket = 0
let debounceTimer: number | undefined

function normalizeRow(raw: Record<string, any>): LookupItem {
  const id = raw.id
  const code = String(raw.code ?? raw.number ?? raw.tag ?? raw.username ?? '').trim()
  const name = String(raw.name ?? raw.title ?? raw.username ?? code ?? id).trim()
  const subtitle = String(raw.subtitle ?? raw.description ?? raw.email ?? '').trim()
  const label = raw.label
    ? String(raw.label)
    : (code && name && code !== name ? `${code} · ${name}` : (name || code || `#${id}`))
  return { id, code, name, subtitle, label, meta: raw.meta }
}

const localItems = computed<LookupItem[]>(() => (props.choices ?? []).map(normalizeRow))

const hasValue = computed(() =>
  model.value !== null && model.value !== undefined && model.value !== '' && model.value !== 0
)

const selectedItem = computed<LookupItem | null>(() => {
  if (!hasValue.value) return null
  const target = String(model.value)
  const fromLocal = localItems.value.find(item => String(item.id) === target)
  if (fromLocal) return fromLocal
  const fromRemote = remoteItems.value.find(item => String(item.id) === target)
  if (fromRemote) return fromRemote
  if (hydratedItem.value && String(hydratedItem.value.id) === target) return hydratedItem.value
  if (props.currentLabel) {
    return { id: model.value as number | string, name: props.currentLabel, label: props.currentLabel }
  }
  return { id: model.value as number | string, name: `#${model.value}`, label: `#${model.value}` }
})

watch(selectedItem, (item) => {
  if (!open.value || !userTyped.value) {
    query.value = item?.label ?? ''
  }
}, { immediate: true })

// Hydrate label by ID when editing an existing record not in local choices
watch([() => model.value, () => props.lookup], async ([val, kind]) => {
  if (!kind || val === null || val === undefined || val === '' || val === 0) {
    hydratedItem.value = null
    return
  }
  const target = String(val)
  if (localItems.value.some(i => String(i.id) === target) || remoteItems.value.some(i => String(i.id) === target)) {
    return
  }
  try {
    const search = new URLSearchParams({ id: target, limit: '1' })
    const res = await api<{ results: LookupItem[] }>(`/workspace/lookup/${encodeURIComponent(kind)}?${search}`)
    if (String(model.value) === target && res.results?.length) {
      hydratedItem.value = normalizeRow(res.results[0]!)
      if (!open.value || !userTyped.value) {
        query.value = hydratedItem.value.label ?? ''
      }
    }
  } catch {
    // Keep fallback label silently
  }
}, { immediate: true })

const visibleOptions = computed<LookupItem[]>(() => {
  const term = userTyped.value ? query.value.trim().toLowerCase() : ''
  const filteredLocal = localItems.value.filter(item => {
    if (!term) return true
    const hay = `${item.code ?? ''} ${item.name} ${item.subtitle ?? ''} ${item.id}`.toLowerCase()
    return hay.includes(term)
  })
  if (!props.lookup) {
    return filteredLocal.slice(0, 30)
  }
  const merged: LookupItem[] = []
  const seen = new Set<string>()
  for (const item of remoteItems.value) {
    const key = String(item.id)
    if (!seen.has(key)) {
      seen.add(key)
      merged.push(item)
    }
  }
  for (const item of filteredLocal) {
    const key = String(item.id)
    if (!seen.has(key)) {
      seen.add(key)
      merged.push(item)
    }
  }
  return merged.slice(0, 30)
})

async function fetchRemote(term: string) {
  if (!props.lookup) return
  const ticket = ++requestTicket
  pending.value = true
  try {
    const search = new URLSearchParams({ q: term, limit: '20' })
    if (props.params) {
      for (const [k, v] of Object.entries(props.params)) {
        if (v !== null && v !== undefined && v !== '') {
          search.set(k, String(v))
        }
      }
    }
    const res = await api<{ results: LookupItem[] }>(`/workspace/lookup/${encodeURIComponent(props.lookup)}?${search}`)
    if (ticket === requestTicket) {
      remoteItems.value = (res.results ?? []).map(normalizeRow)
      activeIndex.value = remoteItems.value.length ? 0 : -1
    }
  } catch {
    if (ticket === requestTicket) remoteItems.value = []
  } finally {
    if (ticket === requestTicket) pending.value = false
  }
}

function scheduleRemote(term: string) {
  if (!props.lookup) return
  window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => {
    void fetchRemote(term)
  }, 240)
}

function onFocus() {
  if (props.disabled) return
  open.value = true
  userTyped.value = false
  activeIndex.value = visibleOptions.value.findIndex(o => String(o.id) === String(model.value))
  if (props.lookup && !remoteItems.value.length) {
    void fetchRemote('')
  }
}

function onInput() {
  if (props.disabled) return
  open.value = true
  userTyped.value = true
  activeIndex.value = 0
  if (props.lookup) {
    scheduleRemote(query.value.trim())
  }
}

function choose(item: LookupItem | null) {
  window.clearTimeout(debounceTimer)
  open.value = false
  userTyped.value = false
  if (item) {
    hydratedItem.value = item
    model.value = item.id as any
    query.value = item.label ?? ''
  } else {
    hydratedItem.value = null
    model.value = props.emptyValue as any
    query.value = ''
  }
  emit('select', item)
  root.value?.dispatchEvent(new Event('validation-edit', { bubbles: true }))
}

function closeDropdown() {
  open.value = false
  userTyped.value = false
  query.value = selectedItem.value?.label ?? ''
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (open.value) {
      event.preventDefault()
      closeDropdown()
    }
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (!open.value) {
      onFocus()
      return
    }
    if (visibleOptions.value.length) {
      activeIndex.value = (activeIndex.value + 1) % visibleOptions.value.length
      document.getElementById(`${listId}-${activeIndex.value}`)?.scrollIntoView({ block: 'nearest' })
    }
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (open.value && visibleOptions.value.length) {
      activeIndex.value = activeIndex.value <= 0 ? visibleOptions.value.length - 1 : activeIndex.value - 1
      document.getElementById(`${listId}-${activeIndex.value}`)?.scrollIntoView({ block: 'nearest' })
    }
    return
  }
  if (event.key === 'Enter' && open.value) {
    event.preventDefault()
    const picked = visibleOptions.value[activeIndex.value] ?? (visibleOptions.value.length === 1 ? visibleOptions.value[0] : undefined)
    if (picked) choose(picked)
  }
}

function onOutsideClick(event: MouseEvent) {
  if (root.value && !root.value.contains(event.target as Node)) {
    if (open.value) closeDropdown()
  }
}

onMounted(() => document.addEventListener('mousedown', onOutsideClick))
onBeforeUnmount(() => {
  window.clearTimeout(debounceTimer)
  document.removeEventListener('mousedown', onOutsideClick)
})
</script>

<template>
  <div ref="root" :class="['entity-autocomplete', inline ? 'entity-autocomplete-inline' : 'field-wrap']">
    <label v-if="label" :for="inputId">
      <span :class="{ 'required-caption': required }">{{ label }}</span>
    </label>
    <div class="autocomplete-control">
      <input
        :id="inputId"
        ref="inputEl"
        v-model="query"
        type="text"
        dir="auto"
        autocomplete="off"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="open"
        :aria-controls="listId"
        :aria-activedescendant="open && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined"
        :aria-label="label || placeholder || t('Search by code or name')"
        :placeholder="placeholder || t('Search by code or name')"
        :disabled="disabled"
        @focus="onFocus"
        @input="onInput"
        @keydown="onKeydown"
      />
      <button
        v-if="clearable && hasValue && !disabled"
        type="button"
        class="autocomplete-clear"
        :aria-label="t('Select')"
        @mousedown.prevent
        @click="choose(null)"
      >×</button>
      <span class="autocomplete-chevron" aria-hidden="true">▾</span>
    </div>

    <!-- Canonical validated value control for v-validated-form -->
    <input
      type="text"
      class="sr-only-validation"
      tabindex="-1"
      aria-hidden="true"
      :data-field="dataField"
      :aria-label="label"
      :value="hasValue ? String(model) : ''"
      :required="required"
      :disabled="disabled"
    />

    <div v-if="open" :id="listId" class="autocomplete-menu panel" role="listbox" :aria-label="label || t('Search by code or name')">
      <div v-if="pending" class="autocomplete-status" role="status">{{ t('Loading') }}…</div>
      <template v-else-if="visibleOptions.length">
        <button
          v-for="(item, idx) in visibleOptions"
          :id="`${listId}-${idx}`"
          :key="item.id"
          type="button"
          role="option"
          class="autocomplete-option"
          :class="{ 'is-active': idx === activeIndex, 'is-selected': String(item.id) === String(model) }"
          :aria-selected="idx === activeIndex"
          @mousedown.prevent
          @click="choose(item)"
        >
          <span class="autocomplete-main">
            <strong v-if="item.code" class="autocomplete-code"><bdi>{{ item.code }}</bdi></strong>
            <span class="autocomplete-name"><bdi>{{ item.name }}</bdi></span>
          </span>
          <small v-if="item.subtitle" class="autocomplete-sub"><bdi>{{ item.subtitle }}</bdi></small>
        </button>
      </template>
      <div v-else class="autocomplete-status">{{ t('No matching records.') }}</div>
    </div>

    <FieldHint v-if="label && !inline" :label="label" :required="required" />
  </div>
</template>
