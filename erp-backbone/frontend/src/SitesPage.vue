<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPaging,ref as pagingRef} from 'vue'
const pg=pagingRef(1),ps=pagingRef(25),term=pagingRef(''),total=pagingRef(0)
function queryPage(){return new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}).toString()}

import { onMounted, ref } from 'vue'
import { say as s } from './iam'
import './iam.css'
import { api } from './api'
import { message, t } from './i18n'
type Site = {
  id: number
  name: string
  description: string
  active: boolean
  revision: number
  blockers?: Record<string, number>
}
const emit = defineEmits<{ changed: [] }>()

const rows = ref<Site[]>([]),
  form = ref<Site>(),
  error = ref(''),
  busy = ref(false)
const blockers: Record<string, string> = {
  assignments: 'Assignments',
  warehouses: 'Active warehouses',
  purchase_requests: 'Purchase requisitions',
  purchase_orders: 'Purchase orders',
  goods_receipts: 'Goods receipts',
  work_orders: 'Work orders',
  sales_orders: 'Sales orders',
  supplier_invoices: 'Supplier invoices',
  payroll_runs: 'Payroll runs',
  leave_requests: 'Leave requests',
}
async function load() {
  const data=await api<{items:Site[];count:number}>('/sites?'+queryPage());rows.value=data.items;total.value=data.count
}
watchPaging([pg,ps,term],()=>load().catch(e=>error.value=message(e)))
onMounted(async () => {
  try {
    await load()
  } catch (e) {
    error.value = message(e)
  }
})
async function edit(id: number) {
  error.value = ''
  if (!id) {
    form.value = { id: 0, name: '', description: '', active: true, revision: 0 }
    return
  }
  try {
    form.value = await api<Site>('/sites/' + id)
  } catch (e) {
    error.value = message(e)
  }
}
async function save(active: boolean) {
  if (!form.value || busy.value) return
  const { id, name, revision, description } = form.value
  busy.value = true
  error.value = ''
  try {
    await api(id ? '/sites/' + id : '/access/sites', id ? 'PATCH' : 'POST', {
      name,
      revision,
      description,
      active,
    })
    form.value = undefined
    await load()
    emit('changed')
  } catch (e) {
    error.value = message(e)
    if (id && form.value) {
      try {
        const latest = await api<Site>('/sites/' + id)
        if (form.value?.id === id) form.value.blockers = latest.blockers
      } catch {}
    }
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <main class="iam-stack">
    <div class="page-heading">
      <h1>{{ t('Sites') }}</h1>
      <button v-if="!form" class="primary" @click="edit(0)">
        {{ t('New site') }}
      </button>
    </div>
    <p v-if="error" role="alert" class="alert">{{ t(error) }}</p>
    <form
      v-validated-form
      v-if="form"
      class="panel record-form iam-form"
      @submit.prevent="save(form.active)"
    >
      <div class="field-wrap">
        <label
          >{{ t('Name')
          }}<input
            data-field="name"
            :placeholder="s('e.g., Main branch', 'مثال: الفرع الرئيسي')"
            v-model="form.name"
            required
            maxlength="120" /></label
        ><FieldHint :label="t('Name')" required />
      </div>
      <label
        >{{ t('Description')
        }}<textarea maxlength="500" v-model="form.description" rows="3" />
      </label>
      <label
        >{{ t('Status')
        }}<select v-model="form.active">
          <option :value="true">{{ t('Active') }}</option>
          <option :value="false">{{ t('Inactive') }}</option>
        </select></label
      >
      <div v-if="form.blockers && Object.keys(form.blockers).length">
        <h2>{{ t('Records blocking site deactivation') }}</h2>
        <ul>
          <li v-for="(count, key) in form.blockers" :key="key">
            {{ t(blockers[key] ?? key) }} · {{ count }}
          </li>
        </ul>
      </div>
      <div class="actions">
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
        <button class="secondary" type="button" @click="form = undefined">
          {{ t('Cancel') }}
        </button>
      </div>
    </form>
    <template v-else
      ><TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/>
      <section class="panel table-panel">
        <table>
          <thead>
            <tr>
              <th>{{ t('Name') }}</th>
              <th>{{ t('Description') }}</th>
              <th>{{ t('Status') }}</th>
              <th>{{ t('Actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in rows"
              :key="row.id"
            >
              <td>{{ row.name }}</td>
              <td>{{ row.description || '—' }}</td>
              <td>
                <span class="iam-pill iam-status" :data-active="row.active">{{
                  t(row.active ? 'Active' : 'Inactive')
                }}</span>
              </td>
              <td>
                <button class="secondary" @click="edit(row.id)">
                  {{ t('Edit') }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section></template
    >
  </main>
</template>
