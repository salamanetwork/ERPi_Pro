<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import { ref, onMounted,watch } from 'vue'
import { api } from './api'
import { message, t } from './i18n'
import type { Stage } from './crm'

const rows = ref<Stage[]>([])
const blank = () => ({code:'',name_en:'',name_ar:'',order:35,active:true})
const form = ref(blank())
const editing = ref<Stage>()
const error = ref('')
const saved = ref(false)
const busy = ref(false)
const pg=ref(1),ps=ref(25),term=ref(''),count=ref(0)
async function load(){const d=await api<{items:Stage[];count:number}>('/crm/stages?'+new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}));rows.value=d.items;count.value=d.count}
watch([pg,ps,term],()=>load().catch(e=>error.value=message(e)))
onMounted(async () => { try { await load() } catch (failure) { error.value=message(failure) } })
function edit(row?:Stage) {
  editing.value = row
  form.value = row ? {...row} : blank()
  saved.value = false
}
async function save() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  saved.value = false
  try {
    await api('/crm/stages' + (editing.value ? '/'+editing.value.id : ''), editing.value?'PATCH':'POST',
      {...form.value,revision:editing.value?.revision})
    await load()
    edit()
    saved.value = true
  } catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}
</script>

<template>
  <section class="panel record-form">
    <h2>{{ t('Pipeline stages') }}</h2>
    <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
    <p v-if="saved" class="success" role="status">{{ t('Changes saved.') }}</p>
    <TableToolbar :page="pg" :size="ps" :count="count" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/><div class="table-panel">
      <table><thead><tr><th>{{ t('Code') }}</th><th>{{ t('English name') }}</th><th>{{ t('Arabic name') }}</th><th>{{ t('Position') }}</th><th>{{ t('Status') }}</th><th>{{ t('Actions') }}</th></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.id"><td>{{ row.code }}</td><td>{{ row.name_en }}</td><td>{{ row.name_ar }}</td><td>{{ row.order }}</td>
          <td>{{ t(row.active ? 'Active' : 'Archived') }}</td><td><button class="secondary" type="button" @click="edit(row)">{{ t('Edit') }}</button></td></tr></tbody>
      </table>
    </div>
    <form v-validated-form class="record-form" @submit.prevent="save">
      <h3>{{ t(editing ? 'Edit stage' : 'New stage') }}</h3>
      <div class="crm-fields">
        <label>{{ t('Code') }}<input v-model="form.code" data-field="code" required :readonly="Boolean(editing)" maxlength="32" placeholder="technical-review"></label>
        <label>{{ t('English name') }}<input v-model="form.name_en" data-field="name_en" required maxlength="80" placeholder="Technical review"></label>
        <label>{{ t('Arabic name') }}<input v-model="form.name_ar" data-field="name_ar" required maxlength="80" placeholder="المراجعة الفنية"></label>
        <label>{{ t('Position') }}<input v-model.number="form.order" data-field="order" :readonly="!!editing&&editing.kind!=='open'" type="number" :min="1" :max="10000" required></label>
        <label v-if="editing?.kind==='open'" class="check"><input v-model="form.active" type="checkbox">{{ t('Active') }}</label>
      </div>
      <div class="actions">
        <button class="primary" :disabled="busy">{{ t('Save') }}</button>
        <button v-if="editing" type="button" class="secondary" @click="edit()">{{ t('Cancel') }}</button>
      </div>
    </form>
  </section>
</template>
