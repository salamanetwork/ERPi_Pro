<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, type Language } from './api'
import { locale, message, t } from './i18n'

type CommandItem = {
  id: string
  category: 'action' | 'navigation' | 'record'
  module: string
  title: Record<Language, string>
  subtitle: Record<Language, string>
  route: string
}

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const router = useRouter()
const inputRef = ref<HTMLInputElement>()
const query = ref('')
const items = ref<CommandItem[]>([])
const selectedIndex = ref(0)
const busy = ref(false)
const error = ref('')
let debounceTimer: number | undefined
let requestSerial = 0

const categoryLabels: Record<string, string> = {
  action: 'Quick actions',
  navigation: 'Navigation',
  record: 'Matching records',
}

const grouped = computed(() => {
  const order: Array<'action' | 'navigation' | 'record'> = ['action', 'navigation', 'record']
  const groups: Array<{ category: string; label: string; rows: Array<{ item: CommandItem; flatIndex: number }> }> = []
  let flatIndex = 0
  for (const cat of order) {
    const matched = items.value.filter(row => row.category === cat)
    if (!matched.length) continue
    groups.push({
      category: cat,
      label: categoryLabels[cat] ?? cat,
      rows: matched.map(item => ({ item, flatIndex: flatIndex++ })),
    })
  }
  return groups
})

const flatItems = computed(() => grouped.value.flatMap(g => g.rows.map(r => r.item)))

async function fetchCommands(term: string) {
  const current = ++requestSerial
  busy.value = true
  error.value = ''
  try {
    const qs = term.trim() ? '?q=' + encodeURIComponent(term.trim()) : ''
    const result = await api<{ items: CommandItem[] }>('/commands' + qs)
    if (current === requestSerial) {
      items.value = result.items
      selectedIndex.value = 0
    }
  } catch (failure) {
    if (current === requestSerial) error.value = message(failure)
  } finally {
    if (current === requestSerial) busy.value = false
  }
}

watch(() => props.open, async (isOpen) => {
  if (!isOpen) {
    window.clearTimeout(debounceTimer)
    return
  }
  query.value = ''
  selectedIndex.value = 0
  void fetchCommands('')
  await nextTick()
  inputRef.value?.focus()
})

watch(query, (term) => {
  if (!props.open) return
  window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => {
    void fetchCommands(term)
  }, 140)
})

onBeforeUnmount(() => {
  window.clearTimeout(debounceTimer)
})

function moveSelection(delta: number) {
  const count = flatItems.value.length
  if (!count) return
  selectedIndex.value = (selectedIndex.value + delta + count) % count
}

function executeSelected() {
  const target = flatItems.value[selectedIndex.value]
  if (target) {
    executeItem(target)
  } else if (query.value.trim().length >= 2) {
    const term = query.value.trim()
    emit('close')
    void router.push({ path: '/search', query: { q: term } })
  }
}

function executeItem(item: CommandItem) {
  emit('close')
  void router.push(item.route)
}

function fullSearch() {
  const term = query.value.trim()
  if (term.length < 2) return
  emit('close')
  void router.push({ path: '/search', query: { q: term } })
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="command-backdrop" @click.self="emit('close')" @keydown.esc.stop.prevent="emit('close')">
      <div class="command-dialog panel" role="dialog" aria-modal="true" :aria-label="t('Command palette')">
        <div class="command-header">
          <input
            ref="inputRef"
            v-model="query"
            type="search"
            class="command-input"
            :placeholder="t('Type a command, module, or record number...')"
            :aria-label="t('Command palette')"
            maxlength="120"
            @keydown.down.prevent="moveSelection(1)"
            @keydown.up.prevent="moveSelection(-1)"
            @keydown.enter.prevent="executeSelected"
          />
          <button type="button" class="secondary command-esc" @click="emit('close')">Esc</button>
        </div>

        <div class="command-body" role="listbox" :aria-label="t('Command palette')">
          <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
          <p v-else-if="busy && !items.length" class="command-empty muted">{{ t('Loading') }}…</p>
          <p v-else-if="!flatItems.length" class="command-empty muted">{{ t('No matching commands or records.') }}</p>

          <section v-for="group in grouped" :key="group.category" class="command-group">
            <h3 class="command-group-title">{{ t(group.label) }}</h3>
            <ul class="command-list">
              <li v-for="{ item, flatIndex } in group.rows" :key="item.id">
                <button
                  type="button"
                  class="command-item"
                  :class="{ active: flatIndex === selectedIndex }"
                  role="option"
                  :aria-selected="flatIndex === selectedIndex"
                  @mouseenter="selectedIndex = flatIndex"
                  @click="executeItem(item)"
                >
                  <span class="command-item-main">
                    <strong><bdi>{{ item.title[locale] || item.title.en }}</bdi></strong>
                    <small class="muted"><bdi>{{ item.subtitle[locale] || item.subtitle.en }}</bdi></small>
                  </span>
                  <code class="command-route" dir="ltr">{{ item.route }}</code>
                </button>
              </li>
            </ul>
          </section>
        </div>

        <div class="command-footer">
          <span class="muted">{{ t('Use ↑↓ to navigate, Enter to open') }}</span>
          <button v-if="query.trim().length >= 2" type="button" class="secondary command-full-search" @click="fullSearch">
            {{ t('Open full search') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
