<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import { api } from './api'
import { message } from './i18n'
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { type Module, type ModuleView } from './api'
import { locale, t } from './i18n'
import GroupControls from './GroupControls.vue'
import { groups, screens } from './navigation'

const props = defineProps<{ modules: Module[]; view: ModuleView; busy: boolean }>()
const emit = defineEmits<{
  toggle: [module: Module]
  toggleChain: [module: Module]
}>()

const router = useRouter()
const overrides = ref<Record<string, boolean>>({})
const statusFilter = ref<'all' | 'enabled' | 'disabled'>('all')
const jumpQuery = ref('')
const highlightedId = ref('')
let highlightTimer: number | undefined

const pg = ref(1), ps = ref(50), term = ref(''), count = ref(0), keys = ref<string[]>([]), error = ref('')
let serial = 0

watch([pg, ps, term], async () => {
  const token = ++serial
  try {
    const d = await api<{ results: string[]; count: number }>('/modules?' + new URLSearchParams({
      page: String(pg.value),
      page_size: String(ps.value),
      q: term.value,
    }))
    if (token === serial) {
      keys.value = d.results
      count.value = d.count
      error.value = ''
    }
  } catch (e) {
    if (token === serial) error.value = message(e)
  }
}, { immediate: true })

const modulesById = computed(() => new Map(props.modules.map(m => [m.id, m])))

const enabledCount = computed(() => props.modules.filter(m => m.enabled).length)
const disabledCount = computed(() => props.modules.filter(m => !m.enabled).length)

function matchesTerm(module: Module, q: string): boolean {
  if (!q) return true
  const needle = q.toLowerCase()
  return [
    module.id,
    module.group,
    module.name.en,
    module.name.ar,
    module.description.en,
    module.description.ar,
  ].some(val => (val || '').toLowerCase().includes(needle))
}

const filteredModules = computed(() => {
  const q = term.value.trim()
  if (statusFilter.value === 'all') {
    return props.modules.filter(m => keys.value.includes(m.id))
  }
  const wantEnabled = statusFilter.value === 'enabled'
  return props.modules.filter(m => m.enabled === wantEnabled && matchesTerm(m, q))
})

const categories = computed(() => {
  const classified = new Map<string, Module[]>()
  for (const module of filteredModules.value) {
    const group = module.group || 'General'
    const entries = classified.get(group) ?? []
    entries.push(module)
    classified.set(group, entries)
  }
  const ordered = new Set([...groups.map(group => group.label), ...classified.keys()])
  return [...ordered].filter(name => classified.has(name)).map(name => ({ name, modules: classified.get(name)! }))
})

const sortedModules = computed(() =>
  [...props.modules].sort((a, b) =>
    (a.name[locale.value] || a.name.en).localeCompare(b.name[locale.value] || b.name.en)
  )
)

function jumpLabel(module: Module): string {
  const primary = module.name[locale.value] || module.name.en
  const secondary = locale.value === 'ar' ? module.name.en : module.name.ar
  const state = t(module.enabled ? 'Enabled' : 'Disabled')
  return secondary && secondary !== primary
    ? `${primary} — ${secondary} (${state})`
    : `${primary} (${state})`
}

function findJumpTarget(): Module | undefined {
  const raw = jumpQuery.value.trim()
  if (!raw) return undefined
  const exact = sortedModules.value.find(
    m => jumpLabel(m) === raw || m.id.toLowerCase() === raw.toLowerCase() ||
         m.name.en.toLowerCase() === raw.toLowerCase() || m.name.ar === raw
  )
  if (exact) return exact
  const needle = raw.toLowerCase()
  return sortedModules.value.find(
    m => jumpLabel(m).toLowerCase().includes(needle) || m.id.toLowerCase().includes(needle)
  )
}

const selectedJumpModule = computed(() => findJumpTarget())

function moduleRoute(module: Module): string | null {
  if (!module.enabled || !module.navigation_allowed) return null
  const screen = screens.find(s => s.module === module.id)
  if (screen) return screen.path
  const entries = (module as Module & { ui_entries?: string[] }).ui_entries
  if (entries?.length) return entries[0]
  if (module.category !== 'platform_core') return '/' + module.id
  return null
}

function openModule(module: Module) {
  const target = moduleRoute(module)
  if (target) void router.push(target)
}

async function jumpToModule() {
  const target = findJumpTarget()
  if (!target) return
  if (statusFilter.value === 'enabled' && !target.enabled) statusFilter.value = 'all'
  if (statusFilter.value === 'disabled' && target.enabled) statusFilter.value = 'all'
  if (term.value.trim() && !matchesTerm(target, term.value.trim())) term.value = ''
  const group = target.group || 'General'
  overrides.value = { ...overrides.value, [group]: true }
  await nextTick()
  const el = document.getElementById('module-item-' + target.id)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    highlightedId.value = target.id
    window.clearTimeout(highlightTimer)
    highlightTimer = window.setTimeout(() => { highlightedId.value = '' }, 3200)
  }
}

const isOpen = (name: string) => overrides.value[name] ?? true
function setAll(open: boolean) {
  overrides.value = Object.fromEntries(categories.value.map(category => [category.name, open]))
}

function activationChain(target: Module): Module[] {
  const visited = new Set<string>()
  const ordered: Module[] = []
  function visit(mod: Module) {
    if (visited.has(mod.id)) return
    visited.add(mod.id)
    for (const depId of mod.required_dependencies ?? []) {
      const dep = modulesById.value.get(depId)
      if (dep) visit(dep)
    }
    ordered.push(mod)
  }
  visit(target)
  return ordered
}

function prerequisitesInOrder(target: Module): Module[] {
  return activationChain(target).filter(m => m.id !== target.id)
}

function missingPrerequisites(target: Module): Module[] {
  return prerequisitesInOrder(target).filter(m => !m.enabled)
}

function activeDependents(target: Module): Module[] {
  if (!target.enabled) return []
  return props.modules.filter(row => row.enabled && row.required_dependencies.includes(target.id))
}

function isActionDisabled(module: Module): boolean {
  if (module.category === 'platform_core' || props.busy) return true
  if (module.enabled) return activeDependents(module).length > 0
  return module.compatible === false || missingPrerequisites(module).length > 0
}

function actionHint(module: Module): string {
  if (module.category === 'platform_core') return ''
  if (module.compatible === false) return `${t('Requires compatible core')} · ${module.core_version ?? module.platform_contract}`
  if (module.enabled) {
    const deps = activeDependents(module)
    if (deps.length) {
      return `${t('Required by')}: ${deps.map(m => m.name[locale.value] || m.name.en).join(' · ')}`
    }
    return ''
  }
  const missing = missingPrerequisites(module)
  if (missing.length) {
    const seq = [...missing, module].map((m, i) => `${i + 1}. ${m.name[locale.value] || m.name.en}`).join(' → ')
    return `${t('Enable first (in order)')}: ${seq}`
  }
  return ''
}
</script>

<template>
  <div class="module-catalog">
    <p v-if="error" role="alert">{{ error }}</p>
    <TableToolbar
      :page="pg"
      :size="ps"
      :count="statusFilter === 'all' ? count : filteredModules.length"
      :query="term"
      @page="pg=$event"
      @size="ps=$event;pg=1"
      @search="term=$event;pg=1"
    />

    <div class="panel module-quick-bar">
      <div class="actions" role="group" :aria-label="t('Status')">
        <button
          type="button"
          :class="statusFilter === 'all' ? 'primary' : 'secondary'"
          :aria-pressed="statusFilter === 'all'"
          @click="statusFilter = 'all'"
        >
          {{ t('All') }} ({{ modules.length }})
        </button>
        <button
          type="button"
          :class="statusFilter === 'enabled' ? 'primary' : 'secondary'"
          :aria-pressed="statusFilter === 'enabled'"
          @click="statusFilter = 'enabled'"
        >
          {{ t('Enabled only') }} ({{ enabledCount }})
        </button>
        <button
          type="button"
          :class="statusFilter === 'disabled' ? 'primary' : 'secondary'"
          :aria-pressed="statusFilter === 'disabled'"
          @click="statusFilter = 'disabled'"
        >
          {{ t('Disabled only') }} ({{ disabledCount }})
        </button>
      </div>

      <form class="actions module-jump-form" @submit.prevent="jumpToModule">
        <label class="module-jump-field">
          <span class="sr-only">{{ t('Quick jump to module') }}</span>
          <input
            v-model="jumpQuery"
            type="search"
            list="erpi-module-jump-list"
            :placeholder="t('Type module name to jump...')"
            @change="jumpToModule"
          >
          <datalist id="erpi-module-jump-list">
            <option v-for="m in sortedModules" :key="m.id" :value="jumpLabel(m)" />
          </datalist>
        </label>
        <button type="submit" class="secondary" :disabled="!selectedJumpModule">{{ t('Jump') }}</button>
        <button
          v-if="selectedJumpModule && moduleRoute(selectedJumpModule)"
          type="button"
          class="primary"
          @click="openModule(selectedJumpModule)"
        >
          {{ t('Open module') }}
        </button>
      </form>
    </div>

    <GroupControls :expanded="categories.every(category => isOpen(category.name))"
      :collapsed="categories.every(category => !isOpen(category.name))" @open="setAll(true)" @close="setAll(false)" />

    <p v-if="!categories.length" class="muted">{{ t('No modules match the current filter.') }}</p>

    <section v-for="(category, index) in categories" :key="category.name" class="panel module-category">
      <h2>
        <button type="button" class="module-category-toggle" :aria-expanded="isOpen(category.name)"
          :aria-controls="'module-category-'+index" @click="overrides[category.name] = !isOpen(category.name)">
          {{ t(category.name) }} <span aria-hidden="true">{{ isOpen(category.name) ? '−' : '+' }}</span>
        </button>
      </h2>

      <div v-show="isOpen(category.name)" :id="'module-category-'+index">
        <!-- List View -->
        <div v-if="view === 'list'" class="table-panel module-list">
          <table>
            <thead>
              <tr>
                <th scope="col">{{ t('Module') }}</th>
                <th scope="col">{{ t('Category') }}</th>
                <th scope="col">{{ t('Version') }}</th>
                <th scope="col">{{ t('Status') }}</th>
                <th scope="col">{{ t('Action') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="module in category.modules"
                :id="'module-item-' + module.id"
                :key="module.id"
                :class="{ 'is-highlighted': highlightedId === module.id }"
              >
                <td>
                  <strong>{{ module.name[locale] || module.name.en }}</strong>
                  <div class="muted">{{ module.description[locale] || module.description.en }}</div>

                  <div v-if="!module.enabled && missingPrerequisites(module).length" class="module-order-note">
                    <span class="module-order-label">{{ t('Enable first (in order)') }}:</span>
                    <span class="module-chain-pills">
                      <span
                        v-for="(step, idx) in [...missingPrerequisites(module), module]"
                        :key="step.id"
                        class="module-pill"
                        :class="{ 'is-target': step.id === module.id }"
                      >
                        {{ idx + 1 }}. {{ step.name[locale] }}
                      </span>
                    </span>
                  </div>

                  <div v-else-if="prerequisitesInOrder(module).length" class="module-order-note muted">
                    <span>{{ t('Required') }}:</span>
                    <span class="module-chain-pills">
                      <span v-for="dep in prerequisitesInOrder(module)" :key="dep.id" class="module-pill is-ready">
                        ✓ {{ dep.name[locale] }}
                      </span>
                    </span>
                  </div>

                  <div v-if="module.enabled && activeDependents(module).length" class="module-order-note">
                    <span class="module-order-label">{{ t('Required by') }}:</span>
                    <span class="module-chain-pills">
                      <span v-for="dep in activeDependents(module)" :key="dep.id" class="module-pill is-blocking">
                        {{ dep.name[locale] }}
                      </span>
                    </span>
                  </div>
                </td>
                <td>{{ t(category.name) }}</td>
                <td>{{ module.version }}</td>
                <td>
                  <span class="badge" :class="{ on: module.enabled }">
                    {{ t(module.compatible === false ? 'Incompatible' : module.enabled ? 'Enabled' : 'Disabled') }}
                  </span>
                </td>
                <td>
                  <div class="module-actions-cell">
                    <button
                      v-if="moduleRoute(module)"
                      type="button"
                      class="secondary"
                      @click="openModule(module)"
                    >
                      {{ t('Open module') }}
                    </button>
                    <button
                      :class="module.enabled ? 'secondary' : 'primary'"
                      :disabled="isActionDisabled(module)"
                      :title="actionHint(module)"
                      @click="emit('toggle', module)"
                    >
                      {{ module.category === 'platform_core' ? t('Core') : t(module.enabled ? 'Disable' : 'Enable') }}
                    </button>
                    <button
                      v-if="!module.enabled && module.compatible !== false && missingPrerequisites(module).length"
                      type="button"
                      class="secondary module-chain-btn"
                      :disabled="busy"
                      :title="actionHint(module)"
                      @click="emit('toggleChain', module)"
                    >
                      {{ t('Enable in order') }} ({{ missingPrerequisites(module).length + 1 }})
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Card & Grid View -->
        <div v-else class="card-grid module-category-cards" :class="{ 'compact-modules': view === 'grid' }">
          <article
            v-for="module in category.modules"
            :id="'module-item-' + module.id"
            :key="module.id"
            class="panel module-card"
            :class="{ 'is-highlighted': highlightedId === module.id }"
          >
            <div class="split">
              <h3>{{ module.name[locale] || module.name.en }}</h3>
              <span class="badge" :class="{ on: module.enabled }">
                {{ t(module.compatible === false ? 'Incompatible' : module.enabled ? 'Enabled' : 'Disabled') }}
              </span>
            </div>
            <p v-if="module.compatible === false" class="muted">
              {{ t('Requires compatible core') }} · <bdi>{{ module.core_version ?? module.platform_contract }}</bdi>
            </p>
            <small class="muted module-group">{{ t(category.name) }}</small>

            <template v-if="view === 'card'">
              <p class="muted">{{ module.description[locale] || module.description.en }}</p>
            </template>

            <div v-if="!module.enabled && missingPrerequisites(module).length" class="module-order-box">
              <span class="module-order-label">{{ t('Enable first (in order)') }}:</span>
              <div class="module-chain-pills">
                <span
                  v-for="(step, idx) in [...missingPrerequisites(module), module]"
                  :key="step.id"
                  class="module-pill"
                  :class="{ 'is-target': step.id === module.id }"
                >
                  {{ idx + 1 }}. {{ step.name[locale] }}
                </span>
              </div>
            </div>

            <div v-else-if="prerequisitesInOrder(module).length" class="module-order-box muted">
              <span>{{ t('Required') }}:</span>
              <div class="module-chain-pills">
                <span v-for="dep in prerequisitesInOrder(module)" :key="dep.id" class="module-pill is-ready">
                  ✓ {{ dep.name[locale] }}
                </span>
              </div>
            </div>

            <div v-if="module.enabled && activeDependents(module).length" class="module-order-box">
              <span class="module-order-label">{{ t('Required by') }}:</span>
              <div class="module-chain-pills">
                <span v-for="dep in activeDependents(module)" :key="dep.id" class="module-pill is-blocking">
                  {{ dep.name[locale] }}
                </span>
              </div>
            </div>

            <div class="split card-footer">
              <small class="muted">{{ t('Version') }} {{ module.version }}</small>
              <div class="module-actions-cell">
                <button
                  v-if="moduleRoute(module)"
                  type="button"
                  class="secondary"
                  @click="openModule(module)"
                >
                  {{ t('Open module') }}
                </button>
                <button
                  :class="module.enabled ? 'secondary' : 'primary'"
                  :disabled="isActionDisabled(module)"
                  :title="actionHint(module)"
                  @click="emit('toggle', module)"
                >
                  {{ module.category === 'platform_core' ? t('Core') : t(module.enabled ? 'Disable' : 'Enable') }}
                </button>
                <button
                  v-if="!module.enabled && module.compatible !== false && missingPrerequisites(module).length"
                  type="button"
                  class="secondary module-chain-btn"
                  :disabled="busy"
                  :title="actionHint(module)"
                  @click="emit('toggleChain', module)"
                >
                  {{ t('Enable in order') }} ({{ missingPrerequisites(module).length + 1 }})
                </button>
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.module-quick-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}
.module-jump-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.module-jump-field input {
  min-width: min(100%, 18rem);
}
.is-highlighted {
  outline: 2px solid #3b82f6;
  outline-offset: 2px;
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.2);
  transition: box-shadow 0.25s ease, outline-color 0.25s ease;
}
</style>
