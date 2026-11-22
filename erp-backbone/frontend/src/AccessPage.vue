<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { api, type Workspace } from './api'
import { locale } from './i18n'
import {
  catalog,
  roleList,
  failure,
  say as s,
  type Catalog,
  type Role,
} from './iam'
import MatchText from './MatchText.vue'
import IamAssignments from './IamAssignments.vue'
import './iam.css'
const props = defineProps<{ workspace: Workspace }>()
const emit = defineEmits<{ changed: [] }>()
const route = useRoute(),
  data = ref<Catalog>(),
  roles = ref<Role[]>([]),
  current = ref<Role>(),
  name = ref(''),
  selected = ref<string[]>([])
const search = ref(''),
  permissionSearch = ref(''),
  domain = ref('*'),
  tab = ref('roles'),
  review = ref(false),
  busy = ref(false),
  error = ref(''),
  notice = ref('')
const sorted = (keys: string[]) => [...keys].sort().join('|')
const dirty = computed(
  () =>
    name.value !== (current.value?.name ?? '') ||
    sorted(selected.value) !== sorted(current.value?.permissions ?? []),
)
function canLeave() {
  return (
    !dirty.value ||
    window.confirm(
      s('Discard unsaved role changes?', 'تجاهل تعديلات الدور غير المحفوظة؟'),
    )
  )
}
onBeforeRouteLeave(canLeave)
function edit(r?: Role) {
  current.value = r
  name.value = r?.name ?? ''
  selected.value = [...(r?.permissions ?? [])]
  review.value = false
}
function inspect(id?: number) {
  if (!canLeave()) return
  tab.value = 'roles'
  edit(roles.value.find((r) => r.id === id))
}
const cloneDialog = ref<HTMLDialogElement>()
const destination = ref(''),
  cloneError = ref('')
function clone() {
  if (!current.value || !canLeave()) return
  edit(current.value)
  destination.value = current.value.name + '_Copy'
  cloneError.value = ''
  cloneDialog.value?.showModal()
}
async function confirmClone() {
  if (!current.value || busy.value || !destination.value.trim()) return
  busy.value = true
  try {
    const result = await api<{ id: number }>('/admin/roles', 'POST', {
      name: destination.value.trim(),
      permissions: [...current.value.permissions],
    })
    cloneDialog.value?.close()
    await load()
    edit(roles.value.find((r) => r.id === result.id))
    notice.value = s('Role cloned.', 'تم نسخ الدور.')
    emit('changed')
  } catch (e) {
    cloneError.value = failure(e)
  } finally {
    busy.value = false
  }
}
function switchTab(next: string) {
  if (next === tab.value) return
  if (!canLeave()) return
  edit(current.value)
  tab.value = next
}
async function load() {
  ;[data.value, roles.value] = await Promise.all([catalog(), roleList()])
}
async function init() {
  try {
    await load()
    edit(
      roles.value.find((r) => r.id === Number(route.query.role)) ||
        roles.value[0],
    )
  } catch (e) {
    error.value = failure(e)
  }
}
onMounted(init)
watch(
  () => route.query.role,
  (id) => {
    if (id) inspect(Number(id))
  },
)
async function save() {
  if (busy.value || !dirty.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const r = current.value
    const result = await api<{ id: number }>(
      r ? '/admin/roles/' + r.id : '/admin/roles',
      r ? 'PATCH' : 'POST',
      {
        name: name.value,
        permissions: selected.value,
        ...(r ? { revision: r.revision } : {}),
      },
    )
    await load()
    edit(roles.value.find((role) => role.id === result.id))
    notice.value = s('Role saved.', 'تم حفظ الدور.')
    emit('changed')
  } catch (e) {
    error.value = failure(e)
  } finally {
    busy.value = false
  }
}
async function granted() {
  try {
    await load()
    emit('changed')
  } catch (e) {
    error.value = failure(e)
  }
}
const expanded = ref<Record<string, boolean>>({})
const hint = ref(''),
  hover = ref('')
function meta(k: string) {
  return data.value?.permission_metadata[k]
}
function action(k: string) {
  return meta(k)?.label[locale.value] ?? k
}
function description(k: string) {
  return meta(k)?.description[locale.value] ?? ''
}
const types = computed<Record<string, string>>(() => ({
  read: s('Read', 'قراءة'),
  write: s('Write', 'كتابة'),
  approve: s('Approve / Post', 'اعتماد / ترحيل'),
  admin: s('Admin', 'إدارة'),
}))
function expandAll(value: boolean) {
  expanded.value = Object.fromEntries(cards.value.map((c) => [c.id, value]))
}
const domainNames = computed<Record<string, string>>(() => ({
  general: s('General', 'عام'),
  finance: s('Finance & Payroll', 'المالية والرواتب'),
  procurement: s('Procurement & Inventory', 'المشتريات والمخزون'),
  sales: s('Sales & CRM', 'المبيعات والعملاء'),
  hr: s('HR', 'الموارد البشرية'),
  assets: s('Assets & Manufacturing', 'الأصول والتصنيع'),
}))
function domainOf(id: string, group: string) {
  if (id === 'manufacturing') return 'assets'
  return (
    (
      {
        Finance: 'finance',
        Payroll: 'finance',
        Procurement: 'procurement',
        Inventory: 'procurement',
        Sales: 'sales',
        'Human Resources': 'hr',
        'Assets & Maintenance': 'assets',
      } as Record<string, string>
    )[group] || 'general'
  )
}
const cards = computed(() => {
  const result = new Map<
    string,
    { id: string; title: string; domain: string; keys: string[] }
  >()
  for (const key of data.value?.permission_catalog ?? []) {
    const prefix = key.split('.')[0],
      owners = props.workspace.modules.filter((m) =>
        m.permissions?.includes(key),
      )
    const module =
      owners.find((m) => m.id === prefix) ||
      owners[0] ||
      props.workspace.modules.find(
        (m) =>
          m.id ===
          (
            { budget: 'accounting', maintenance: 'workorders' } as Record<
              string,
              string
            >
          )[prefix],
      )
    const id = module?.id || prefix
    if (!result.has(id))
      result.set(id, {
        id,
        title: module?.name[locale.value] || s('Platform', 'المنصة'),
        domain: domainOf(id, module?.group || 'General'),
        keys: [],
      })
    result.get(id)!.keys.push(key)
  }
  return [...result.values()].sort((a, b) =>
    a.title.localeCompare(b.title, locale.value),
  )
})
const domains = computed(() =>
  Object.keys(domainNames.value).filter((d) =>
    cards.value.some((c) => c.domain === d),
  ),
)
const filtered = computed(() =>
  cards.value
    .filter((c) => domain.value === '*' || c.domain === domain.value)
    .map((c) => {
      const needle = permissionSearch.value.toLowerCase().trim()
      return {
        ...c,
        allKeys: c.keys,
        keys: c.keys.filter((key) =>
          [key, c.title, action(key), description(key)].some((x) =>
            x.toLowerCase().includes(needle),
          ),
        ),
      }
    })
    .filter((c) => c.keys.length),
)
const chosen = computed(() =>
  cards.value
    .map((c) => ({
      ...c,
      keys: c.keys.filter((k) => selected.value.includes(k)),
    }))
    .filter((c) => c.keys.length),
)
function toggle(keys: string[]) {
  const all = keys.every((k) => selected.value.includes(k))
  selected.value = all
    ? selected.value.filter((k) => !keys.includes(k))
    : [...new Set([...selected.value, ...keys])]
}
watch([permissionSearch, domain], () => {
  expanded.value = permissionSearch.value.trim()
    ? Object.fromEntries(filtered.value.map((c) => [c.id, true]))
    : {}
})
</script>
<template>
  <main class="iam-stack">
    <h1>{{ s('Access control', 'التحكم في الوصول') }}</h1>
    <div class="iam-nav">
      <button
        class="secondary"
        :aria-pressed="tab === 'roles'"
        @click="switchTab('roles')"
      >
        {{ s('Roles & Permissions', 'الأدوار والصلاحيات') }}</button><button
        class="secondary"
        :aria-pressed="tab === 'assignments'"
        @click="switchTab('assignments')"
      >
        {{ s('User Assignments', 'تعيينات المستخدمين') }}
      </button>
    </div>
    <p v-if="error" class="alert" role="alert">
      {{ error }}
      <button class="secondary" @click="canLeave() && init()">
        {{ s('Reload', 'إعادة التحميل') }}
      </button>
    </p>
    <p v-if="notice" class="success" role="status">{{ notice }}</p>
    <section v-if="tab === 'roles' && data" class="iam-cols">
      <aside class="panel iam-list">
        <button class="primary" @click="inspect()">
          {{ s('+ New Role', '+ دور جديد') }}</button><label class="iam-field"
          >{{ s('Search roles', 'بحث عن دور')
          }}<input type="search" v-model="search" /></label
        ><button
          class="secondary"
          v-for="r in roles.filter((r) =>
            r.name.toLowerCase().includes(search.toLowerCase()),
          )"
          :key="r.id"
          :aria-pressed="current?.id === r.id"
          @click="inspect(r.id)"
        >
          <strong>{{ r.name }}</strong><small
            >{{ r.permissions.length }} {{ s('permissions', 'صلاحيات') }} ·
            {{ r.user_count }} {{ s('users', 'مستخدمين') }}</small
          >
        </button>
      </aside>
      <section class="iam-stack">
        <div class="panel iam-stack">
          <div class="iam-row">
            <label
              >{{ s('Role name', 'اسم الدور')
              }}<input maxlength="120" v-model="name" /></label
            ><button v-if="current" class="secondary" @click="clone">
              {{ s('Clone Role', 'نسخ الدور') }}
            </button>
          </div>
          <div class="iam-chips">
            <b
              >{{ selected.length }}
              {{ s('permissions selected', 'صلاحيات محددة') }}</b
            ><button
              class="secondary"
              :aria-expanded="review"
              @click="review = !review"
            >
              {{ s('Review Selected', 'مراجعة المحدد') }}</button
            ><button
              class="secondary"
              :disabled="!selected.length"
              @click="selected = []"
            >
              {{ s('Clear selection', 'مسح الاختيار') }}
            </button>
          </div>
          <div v-if="review" class="iam-stack">
            <div v-for="c in chosen" :key="c.id">
              <strong>{{ c.title }}</strong>
              <div class="iam-chips">
                <code v-for="key in c.keys" :key="key">{{ key }}</code>
              </div>
            </div>
          </div>
          <label class="iam-field"
            >{{ s('Search permissions', 'البحث في الصلاحيات')
            }}<input
              v-model="permissionSearch"
              type="search"
              :placeholder="
                s('Code, action or module', 'الكود أو الإجراء أو الموديول')
              "
          /></label>
          <div class="iam-nav">
            <button class="secondary" @click="expandAll(true)">
              {{ s('Expand All', 'فتح الكل') }}</button
            ><button class="secondary" @click="expandAll(false)">
              {{ s('Collapse All', 'طي الكل') }}
            </button>
          </div>
          <div class="iam-nav">
            <button
              class="secondary"
              v-for="d in ['*', ...domains]"
              :key="d"
              :aria-pressed="domain === d"
              @click="domain = d"
            >
              {{ domainNames[d] || s('All', 'الكل') }}
            </button>
          </div>
        </div>
        <div class="iam-grid">
          <section class="panel" v-for="c in filtered" :key="c.id">
            <header class="iam-accordion">
              <button
                class="secondary"
                :aria-expanded="!!expanded[c.id]"
                :aria-label="c.title"
                @click="expanded[c.id] = !expanded[c.id]"
              >
                {{ expanded[c.id] ? '−' : '+' }}
              </button>
              <h3><MatchText :text="c.title" :query="permissionSearch" /></h3>
              <small class="iam-pill"
                >{{ c.allKeys.filter((k) => selected.includes(k)).length }} /
                {{ c.allKeys.length }}</small
              >
              <label class="iam-perm"
                ><input
                  type="checkbox"
                  :checked="c.keys.every((k) => selected.includes(k))"
                  :indeterminate="
                    c.keys.some((k) => selected.includes(k)) &&
                    !c.keys.every((k) => selected.includes(k))
                  "
                  @change="toggle(c.keys)"
                />{{
                  permissionSearch
                    ? s('Select all matches', 'تحديد النتائج')
                    : s('Select All', 'تحديد الكل')
                }}</label
              >
            </header>
            <template v-if="expanded[c.id]">
              <div
                v-for="key in c.keys"
                :key="key"
                class="iam-rule"
                @keydown.esc="hint = hover = ''"
              >
                <label class="iam-perm" :data-tone="meta(key)?.type"
                  ><input type="checkbox" :value="key" v-model="selected" />
                  <span
                    ><MatchText
                      :text="action(key)"
                      :query="permissionSearch" /><code
                      ><MatchText :text="key" :query="permissionSearch" /></code
                  ></span>
                </label>
                <button
                  class="secondary iam-info"
                  :aria-label="
                    s('Permission details', 'تفاصيل الصلاحية') +
                    ' · ' +
                    action(key)
                  "
                  :aria-expanded="hint === key || hover === key"
                  @mouseenter="hover = key"
                  @mouseleave="hover = ''"
                  @click="hint = hint === key ? '' : key; hover = ''"
                >
                  ⓘ
                </button>
                <small
                  v-if="
                    permissionSearch.trim() &&
                    description(key)
                      .toLowerCase()
                      .includes(permissionSearch.trim().toLowerCase())
                  "
                  class="iam-wide"
                  ><MatchText
                    :text="description(key)"
                    :query="permissionSearch"
                /></small>
                <div v-if="hint === key || hover === key" class="iam-hint">
                  <strong
                    >{{ types[meta(key)?.type || ''] }} · {{ c.title }}</strong
                  >
                  <p>{{ description(key) }}</p>
                </div>
              </div>
            </template>
          </section>
        </div>
        <p v-if="!filtered.length" class="empty">
          {{ s('No matching permissions.', 'لا توجد صلاحيات مطابقة.') }}
        </p>
        <div class="iam-save iam-row">
          <button
            class="primary"
            :disabled="busy || !name.trim() || !selected.length || !dirty"
            @click="save"
          >
            {{ s('Save Role', 'حفظ الدور') }}</button><span v-if="dirty">{{
            s('Unsaved changes', 'تغييرات غير محفوظة')
          }}</span>
        </div>
      </section>
    </section>
    <IamAssignments
      v-else-if="data"
      :data="data"
      :roles="roles"
      @changed="granted"
      @inspect="inspect"
    />
    <dialog class="iam-dialog" ref="cloneDialog" aria-labelledby="clone-title">
      <form class="iam-stack" v-if="current" @submit.prevent="confirmClone">
        <h2 id="clone-title">{{ s('Clone Role', 'نسخ الدور') }}</h2>
        <p>
          {{ s('Source role', 'الدور المصدر') }} ·
          <bdi>{{ current.name }}</bdi> · {{ current.permissions.length }}
          {{ s('permissions', 'صلاحيات') }}
        </p>
        <label
          >{{ s('Destination role name', 'اسم الدور الجديد')
          }}<input v-model="destination" required maxlength="120"
        /></label>
        <ul class="iam-preview">
          <li v-for="k in current.permissions" :key="k">
            {{ action(k) }} · <bdi>{{ k }}</bdi>
          </li>
        </ul>
        <p v-if="cloneError" class="alert" role="alert">{{ cloneError }}</p>
        <div class="actions">
          <button class="primary" :disabled="busy">
            {{ s('Confirm Clone', 'تأكيد النسخ') }}</button
          ><button
            type="button"
            class="secondary"
            @click="cloneDialog?.close()"
          >
            {{ s('Cancel', 'إلغاء') }}
          </button>
        </div>
      </form>
    </dialog>
  </main>
</template>
