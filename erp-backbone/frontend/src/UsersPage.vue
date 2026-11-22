<script setup lang="ts">
import CopyId from "./CopyId.vue"
import TableToolbar from "./TableToolbar.vue"
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type Workspace } from './api'
import {
  catalog,
  roleList,
  say as s,
  failure,
  allSites,
  type Catalog,
  type Role,
  type Person,
} from './iam'
import IamAssignments from './IamAssignments.vue'
import './iam.css'
const props = defineProps<{ workspace: Workspace }>()
const emit = defineEmits<{ changed: [] }>()
const route = useRoute(),
  router = useRouter(),
  id = computed(() => Number(route.params.id) || 0)
const data = ref<Catalog>(),
  roles = ref<Role[]>([]),
  people = ref<Person[]>([]),
  person = ref<Person>()
const q = ref(''),
  status = ref('all'),
  page = ref(1),
  size = ref(25),
  total = ref(0),
  tab = ref('profile')
const name = ref(''),
  active = ref(true),
  secret = ref(''),
  error = ref(''),
  notice = ref(''),
  busy = ref(false)
const directoryTab = ref('directory'),
  confirmPassword = ref(''),
  roleDetails = ref(0),
  hoverUser = ref(0),
  newUser = ref(''),
  newName = ref(''),
  newPassword = ref(''),
  newRole = ref(''),
  newSite = ref(''),
  createError = ref('')
let listRequest = 0,
  detailRequest = 0,
  timer: ReturnType<typeof setTimeout>
async function loadList() {
  const token = ++listRequest
  try {
    const result = await api<{
      results: Person[]
      page: number
      pages: number
      count: number
    }>(
      '/admin/users?' +
        new URLSearchParams({
          q: q.value,
          status: status.value,
          page: String(page.value), page_size:String(size.value),
        }),
    )
    if (token === listRequest) {
      people.value = result.results
      page.value = result.page
      total.value = result.count
    }
  } catch (e) {
    if (token === listRequest) error.value = failure(e)
  }
}
async function loadDetail() {
  const token = ++detailRequest
  if (!id.value) {
    person.value = undefined
    return
  }
  try {
    const row = await api<Person>('/admin/users/' + id.value)
    if (token !== detailRequest) return
    person.value = row
    name.value = row.display_name
    active.value = row.active
    secret.value = confirmPassword.value = ''
  } catch (e) {
    if (token === detailRequest) error.value = failure(e)
  }
}
async function loadCatalog() {
  ;[data.value, roles.value] = await Promise.all([catalog(), roleList()])
}
async function granted() {
  try {
    await loadCatalog()
    await loadDetail()
    emit('changed')
  } catch (e) {
    error.value = failure(e)
  }
}
watch([q, status,size], () => {
  page.value = 1
  listRequest++
  clearTimeout(timer)
  timer = setTimeout(loadList, 180)
})
watch(page, loadList)
watch(id, () => {
  error.value = ''
  notice.value = ''
  tab.value = 'profile'
  person.value = undefined
  void loadDetail()
  if (!id.value) void loadList()
})
onMounted(async () => {
  try {
    await Promise.all([loadCatalog(), loadList(), loadDetail()])
  } catch (e) {
    error.value = failure(e)
  }
})
onBeforeUnmount(() => {
  clearTimeout(timer)
  detailRequest++
  listRequest++
})
function openCreate() {
  createError.value = ''
  newName.value = ''
  newUser.value = ''
  newPassword.value = ''
  newRole.value = ''
  newSite.value = ''
  directoryTab.value = 'create'
}
async function create() {
  if (busy.value) return
  busy.value = true
  createError.value = ''
  try {
    const row = await api<{ id: number }>('/admin/users', 'POST', {
      username: newUser.value,
      display_name: newName.value,
      password: newPassword.value,
      role_id: Number(newRole.value) || null,
      site_id: newRole.value && newSite.value ? Number(newSite.value) : null,
    })
    newPassword.value = ''
    directoryTab.value = 'directory'
    await loadCatalog()
    await router.push('/admin/users/' + row.id)
    notice.value = s('User created.', 'تم إنشاء المستخدم.')
    emit('changed')
  } catch (e) {
    createError.value = failure(e)
  } finally {
    busy.value = false
  }
}
async function save(reset = false) {
  if (!person.value || busy.value) return
  if (reset && secret.value !== confirmPassword.value) {
    error.value = s(
      'The passwords do not match.',
      'كلمتا المرور غير متطابقتين.',
    )
    return
  }
  if (
    reset &&
    !window.confirm(
      s(
        'Reset this password and invalidate existing sign-ins?',
        'إعادة تعيين كلمة المرور وإبطال جلسات هذا المستخدم؟',
      ),
    )
  )
    return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const base = '/admin/users/' + person.value.id
    if (reset)
      await api(base + '/reset-password', 'POST', {
        revision: person.value.revision,
        password: secret.value,
      })
    else
      await api(base, 'PATCH', {
        revision: person.value.revision,
        display_name: name.value,
        active: active.value,
      })
    await loadDetail()
    notice.value = s('Changes saved.', 'تم حفظ التغييرات.')
    emit('changed')
  } catch (e) {
    error.value = failure(e)
  } finally {
    busy.value = false
  }
}
const statuses = computed(() => [
  ['all', s('All', 'الكل')],
  ['active', s('Active', 'نشط')],
  ['inactive', s('Inactive', 'غير نشط')],
  ['admins', s('Admins', 'المسؤولون')],
])
function distinctRoles(row: Person) {
  return [...new Map(row.assignments.map((a) => [a.role_id, a.role])).values()]
}
</script>
<template>
  <main class="iam-stack">
    <div class="actions">
      <h1>{{ s('Users', 'المستخدمون') }}</h1>
      <RouterLink v-if="id" to="/admin/users">{{
        s('Back to users', 'العودة للمستخدمين')
      }}</RouterLink>
    </div>
    <p v-if="error" class="alert" role="alert">
      {{ error }}
      <button class="secondary" @click="id ? loadDetail() : loadList()">
        {{ s('Reload', 'إعادة التحميل') }}
      </button>
    </p>
    <p v-if="notice" class="success" role="status">{{ notice }}</p>
    <nav v-if="!id" class="iam-nav">
      <button
        class="secondary"
        :aria-pressed="directoryTab === 'directory'"
        @click="directoryTab = 'directory'; newPassword = ''"
      >
        {{ s('Users Directory', 'دليل المستخدمين') }}
      </button>
      <button
        class="secondary"
        :aria-pressed="directoryTab === 'create'"
        @click="directoryTab !== 'create' && openCreate()"
      >
        {{ s('Create User', 'إنشاء مستخدم') }}
      </button>
    </nav>
    <template v-if="!id && directoryTab === 'directory'"
      ><label class="iam-field"
        >{{ s('Search users', 'بحث عن مستخدم')
        }}<input
          v-model="q"
          type="search"
          :placeholder="
            s('Username or display name', 'اسم الدخول أو الاسم المعروض')
          "
      /></label>
      <div class="iam-nav">
        <button
          class="secondary"
          v-for="[key, label] in statuses"
          :aria-pressed="status === key"
          :key="key"
          @click="status = key"
        >
          {{ label }}
        </button>
      </div>
      <div class="panel table-panel">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>{{ s('User', 'المستخدم') }}</th>
              <th>{{ s('Status', 'الحالة') }}</th>
              <th>{{ s('Assigned roles', 'الأدوار المعينة') }}</th>
              <th>{{ s('Actions', 'إجراءات') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in people" :key="u.id"><td><CopyId :value="u.id"/></td>
              <td>
                <span class="iam-identity"
                  ><span class="iam-avatar" aria-hidden="true">{{
                    u.display_name.slice(0, 1)
                  }}</span
                  ><span
                    >{{ u.display_name
                    }}<small dir="ltr">@{{ u.username }}</small></span
                  ></span
                >
              </td>
              <td>
                <span class="iam-pill iam-status" :data-active="u.active">{{
                  u.active ? s('Active', 'نشط') : s('Inactive', 'غير نشط')
                }}</span>
              </td>
              <td>
                <div class="iam-chips">
                  <b>{{ distinctRoles(u).length }}</b><span
                    v-for="r in distinctRoles(u).slice(0, 2)"
                    :key="r"
                    class="iam-pill"
                    >{{ r }}</span
                  >
                  <button
                    v-if="distinctRoles(u).length > 2"
                    class="secondary"
                    :aria-expanded="roleDetails === u.id || hoverUser === u.id"
                    @click="roleDetails = roleDetails === u.id ? 0 : u.id; hoverUser = 0"
                    @mouseenter="hoverUser = u.id"
                    @mouseleave="hoverUser = 0"
                  >
                    +{{ distinctRoles(u).length - 2 }} {{ s('more', 'أخرى') }}
                  </button>
                  <ul
                    class="iam-hint"
                    v-if="
                      distinctRoles(u).length > 2 &&
                      (roleDetails === u.id || hoverUser === u.id)
                    "
                  >
                    <li v-for="g in u.assignments" :key="g.id">
                      {{ g.role }} · {{ g.site ?? allSites() }}
                    </li>
                  </ul>
                </div>
              </td>
              <td>
                <RouterLink :to="'/admin/users/' + u.id">{{
                  s('Manage', 'إدارة')
                }}</RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="empty" v-if="!people.length">
          {{ s('No matching users.', 'لا يوجد مستخدم مطابق.') }}
        </p>
      </div>
      <TableToolbar :page="page" :size="size" :count="total" @page="page=$event" @size="size=$event"/></template
    >
    <template v-else-if="id && person"
      ><h2>
        <CopyId :value="person.id"/> {{ person.display_name }} · <bdi>@{{ person.username }}</bdi>
      </h2>
      <div class="iam-nav">
        <button
          class="secondary"
          :aria-pressed="tab === 'profile'"
          @click="tab = 'profile'"
        >
          {{ s('Profile & Status', 'الملف والحالة') }}</button><button
          class="secondary"
          :aria-pressed="tab === 'security'"
          @click="tab = 'security'"
        >
          {{ s('Security & Password', 'الأمان وكلمة المرور') }}</button
        ><button
          class="secondary"
          :aria-pressed="tab === 'roles'"
          @click="tab = 'roles'"
        >
          {{ s('Assigned Roles & Sites', 'الأدوار والمواقع المعينة') }}
        </button>
      </div>
      <form
        v-if="tab === 'profile'"
        class="panel iam-form"
        @submit.prevent="save()"
      >
        <label
          >{{ s('Display name', 'الاسم المعروض')
          }}<input required maxlength="150" v-model="name" /></label
        ><label
          >{{ s('Status', 'الحالة')
          }}<select
            v-model="active"
            :disabled="person.username === props.workspace.username"
          >
            <option :value="true">{{ s('Active', 'نشط') }}</option>
            <option :value="false">{{ s('Inactive', 'غير نشط') }}</option>
          </select></label
        ><button class="primary" :disabled="busy">
          {{ s('Save changes', 'حفظ التغييرات') }}
        </button>
      </form>
      <form
        v-else-if="tab === 'security'"
        class="panel iam-form"
        @submit.prevent="save(true)"
      >
        <label
          >{{ s('New password', 'كلمة المرور الجديدة')
          }}<input
            required
            minlength="12"
            maxlength="256"
            type="password"
            autocomplete="new-password"
            v-model="secret" /></label
        ><label
          >{{ s('Confirm password', 'تأكيد كلمة المرور')
          }}<input
            v-model="confirmPassword"
            required
            type="password"
            autocomplete="new-password" /></label
        ><button class="secondary" :disabled="busy">
          {{ s('Reset password', 'إعادة تعيين كلمة المرور') }}
        </button>
      </form>
      <IamAssignments
        v-else-if="data"
        :data="data"
        :roles="roles"
        :user-id="person.id"
        @changed="granted"
        @inspect="
          (rid) =>
            router.push({ path: '/admin/access-control', query: { role: rid } })
        "
    /></template>
    <form
      v-if="!id && directoryTab === 'create'"
      class="panel iam-form"
      @submit.prevent="create"
    >
      <h2 id="new-user-title">{{ s('Create user', 'إنشاء مستخدم') }}</h2>
      <p class="alert" v-if="createError" role="alert">{{ createError }}</p>
      <label
        >{{ s('Username', 'اسم المستخدم')
        }}<input
          required
          maxlength="150"
          v-model="newUser"
          autocomplete="off" /></label
      ><label
        >{{ s('Display name', 'الاسم المعروض')
        }}<input required maxlength="150" v-model="newName" /></label
      ><label
        >{{ s('Initial password', 'كلمة المرور الأولية')
        }}<input
          required
          minlength="12"
          maxlength="256"
          type="password"
          v-model="newPassword"
          autocomplete="new-password"
        /><small>{{
          s('Use at least 12 characters.', 'استخدم ١٢ حرفًا على الأقل.')
        }}</small></label
      ><label
        >{{ s('Initial role', 'الدور الأولي')
        }}<select v-model="newRole">
          <option value="">
            {{ s('No initial role', 'بدون دور أولي') }}
          </option>
          <option v-for="r in roles" :key="r.id" :value="String(r.id)">
            {{ r.name }}
          </option>
        </select></label
      ><label v-if="newRole"
        >{{ s('Site scope', 'نطاق الموقع')
        }}<select v-model="newSite">
          <option value="">{{ allSites() }}</option>
          <option v-for="v in data?.sites" :key="v.id" :value="String(v.id)">
            {{ v.name }}
          </option>
        </select></label
      >
      <div class="actions">
        <button class="primary" :disabled="busy">
          {{ s('Create user', 'إنشاء مستخدم') }}</button><button
          type="button"
          class="secondary"
          @click="directoryTab = 'directory'; newPassword = ''"
        >
          {{ s('Cancel', 'إلغاء') }}
        </button>
      </div>
    </form>
  </main>
</template>
