<script setup lang="ts">
import { ref, computed,watch } from 'vue'
import TableToolbar from './TableToolbar.vue'
import { api } from './api'
import { say as s, failure, allSites, type Assigned,type Catalog, type Role } from './iam'
const props = defineProps<{ data: Catalog; roles: Role[]; userId?: number }>()
const emit = defineEmits<{ changed: []; inspect: [number] }>()
const username = ref(''),
  roleName = ref(''),
  scope = ref(''),
  userFilter = ref(''),
  roleFilter = ref(''),
  siteFilter = ref('*'),
  grouped = ref(false)
const collapsed = ref<Record<number, boolean>>({})
const error = ref(''),
  notice = ref(''),
  busy = ref(false)
const role = computed(() => props.roles.find((r) => r.name === roleName.value))
const user = computed(
  () =>
    props.userId ||
    props.data.users.find((u) => u.username === username.value)?.id,
)
const filtered=ref<Assigned[]>([]),page=ref(1),size=ref(25),count=ref(0)
let serial=0
async function load(){
 const key=++serial
 const params=new URLSearchParams({user:userFilter.value,role:roleFilter.value,page:String(page.value),page_size:String(size.value),group:grouped.value?'1':'0'})
 if(props.userId)params.set('user_id',String(props.userId))
 if(siteFilter.value!=='*')params.set('site',siteFilter.value||'all')
 try{const r=await api<{results:Assigned[];count:number;page:number}>('/access/assignments?'+params)
 if(key===serial){filtered.value=r.results;count.value=r.count;page.value=r.page}}
 catch(e){if(key===serial)error.value=failure(e)}
}
watch([userFilter,roleFilter,siteFilter,grouped,size,()=>props.userId],()=>page.value=1)
watch([userFilter,roleFilter,siteFilter,grouped,page,size,()=>props.userId,()=>props.data],load,{immediate:true})
async function mutate(id?: number) {
  if (busy.value) return
  if (id && !window.confirm(s('Revoke this assignment?', 'إلغاء هذا التعيين؟')))
    return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    if (id) await api('/access/assignments/' + id, 'DELETE')
    else
      await api('/access/assignments', 'POST', {
        user_id: user.value,
        role_id: role.value?.id,
        site_id: scope.value ? Number(scope.value) : null,
      })
    notice.value = s('Changes saved.', 'تم حفظ التغييرات.')
    await load();emit('changed')
  } catch (e) {
    error.value = failure(e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <section class="iam-stack">
    <p v-if="error" role="alert" class="alert">{{ error }}</p>
    <p v-if="notice" role="status" class="success">{{ notice }}</p>
    <form
      class="panel iam-row iam-assign"
      :class="{ personal: !!userId }"
      @submit.prevent="mutate()"
    >
      <label v-if="!userId"
        >{{ s('User', 'المستخدم')
        }}<input
          required
          list="iam-accounts"
          v-model="username"
          autocomplete="off" /><datalist id="iam-accounts">
          <option
            v-for="u in data.users"
            :key="u.id"
            :value="u.username"
          /></datalist
      ></label>
      <label
        >{{ s('Role', 'الدور')
        }}<input
          required
          list="iam-role-options"
          v-model="roleName"
          autocomplete="off" /><datalist id="iam-role-options">
          <option v-for="r in roles" :key="r.id" :value="r.name" /></datalist
      ></label>
      <label
        >{{ s('Site scope', 'نطاق الموقع')
        }}<select v-model="scope">
          <option value="">{{ allSites() }}</option>
          <option v-for="v in data.sites" :value="String(v.id)" :key="v.id">
            {{ v.name }}
          </option>
        </select></label
      >
      <button class="primary" :disabled="busy || !role || !user">
        {{ s('Assign role', 'تعيين الدور') }}
      </button>
      <details v-if="role" class="iam-wide">
        <summary>
          {{ s('Preview permissions', 'معاينة الصلاحيات') }} ({{
            role.permissions.length
          }})
        </summary>
        <div class="iam-chips">
          <code v-for="k in role.permissions" :key="k">{{ k }}</code>
        </div>
      </details>
    </form>
    <div class="iam-row">
      <label v-if="!userId"
        >{{ s('Filter by user', 'بحث بالمستخدم')
        }}<input v-model="userFilter" type="search" /></label
      ><label
        >{{ s('Filter by role', 'بحث بالدور')
        }}<input type="search" v-model="roleFilter" /></label
      ><label
        >{{ s('Filter by site', 'تصفية حسب الموقع')
        }}<select v-model="siteFilter">
          <option value="*">{{ s('Any scope', 'كل النطاقات') }}</option>
          <option value="">{{ allSites() }}</option>
          <option
            v-for="a in [
              ...new Map(
                data.assignments
                  .filter((x) => x.site_id)
                  .map((x) => [x.site_id, x]),
              ).values(),
            ]"
            :key="a.id"
            :value="String(a.site_id)"
          >
            {{ a.site__name }}
          </option>
        </select></label
      ><label v-if="!userId"
        >{{ s('Display', 'العرض')
        }}<select v-model="grouped">
          <option :value="false">{{ s('Flat list', 'قائمة') }}</option>
          <option :value="true">
            {{ s('Grouped by user', 'تجميع حسب المستخدم') }}
          </option>
        </select></label
      >
      <button
        class="secondary"
        @click="userFilter = roleFilter = ''; siteFilter = '*'"
      >
        {{ s('Reset filters', 'مسح الفلاتر') }}
      </button>
    </div>
    <TableToolbar :page="page" :count="count" :size="size" @page="page=$event" @size="size=$event"/><div class="panel table-panel">
      <table>
        <thead>
          <tr>
            <th>{{ s('User', 'المستخدم') }}</th>
            <th>{{ s('Role', 'الدور') }}</th>
            <th>{{ s('Site scope', 'نطاق الموقع') }}</th>
            <th>{{ s('Action', 'الإجراء') }}</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(a, i) in filtered" :key="a.id"
            ><tr v-if="grouped && filtered[i - 1]?.user_id !== a.user_id">
              <th colspan="4">
                <button
                  class="secondary"
                  :aria-expanded="!collapsed[a.user_id]"
                  @click="collapsed[a.user_id] = !collapsed[a.user_id]"
                >
                  {{ collapsed[a.user_id] ? '+' : '−' }} ·
                  {{ a.user__username }}
                </button>
              </th>
            </tr>
            <tr v-if="!grouped || !collapsed[a.user_id]">
              <td>{{ a.user__username }}</td>
              <td>
                <button class="secondary" @click="emit('inspect', a.role_id)">
                  {{ a.role__name }}
                </button>
              </td>
              <td>{{ a.site__name ?? allSites() }}</td>
              <td>
                <button
                  class="secondary"
                  :disabled="busy"
                  @click="mutate(a.id)"
                >
                  {{ s('Revoke', 'إلغاء') }}
                </button>
              </td>
            </tr></template
          >
        </tbody>
      </table>
      <p v-if="!filtered.length" class="empty">
        {{ s('No matching assignments.', 'لا توجد تعيينات مطابقة.') }}
      </p>
    </div>
  </section>
</template>
