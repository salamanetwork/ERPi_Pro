<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch as watchPaging,ref as pagingRef} from 'vue'
const pg=pagingRef(1),ps=pagingRef(25),term=pagingRef(''),total=pagingRef(0)
function queryPage(){return new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}).toString()}

import CsvExport from './CsvExport.vue'
import { computed, onMounted, ref } from 'vue'
import { allows, api, createRequest, type Asset, type Scope, type Workspace } from './api'
import { useRoute } from 'vue-router'
import { message, t } from './i18n'

const { workspace } = defineProps<{ workspace: Workspace }>()
const route = useRoute()
const rows = ref<Asset[]>([])
const matchingRecords = computed(() => {
  const id = Number(route.query.tag)
  return rows.value.filter(row => !id || row.id === id)
})
const loading = ref(true)
const editing = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const name = ref('')
const tag = ref('')
const site = ref<Scope>(null)
const sites = computed(() => workspace.sites.filter(item => item.active && allows(workspace, 'assets.create', item.id)))
let create = createRequest()

async function load() {
  try { const d=await api<{items:Asset[];count:number}>('/assets?'+queryPage()+'&record='+encodeURIComponent(String(route.query.tag||'')));rows.value=d.items;total.value=d.count }
  catch (reason) { error.value = message(reason) }
  finally { loading.value = false }
}

function start() {
  create = createRequest()
  error.value = ''
  notice.value = ''
  site.value = allows(workspace, 'assets.create', null) ? null : sites.value[0]?.id ?? null
  editing.value = true
}

async function save() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await create<Asset>('/assets', { name: name.value, tag: tag.value, site_id: site.value })
    editing.value = false
    name.value = ''
    tag.value = ''
    notice.value = 'Record created.'
    await load()
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}

watchPaging([pg,ps,term,()=>route.query.tag],load)
onMounted(load)
</script>

<template>
  <CsvExport :workspace="workspace" screen="asset" :filters="{record: typeof route.query.tag === 'string' ? route.query.tag : ''}" />
  <div class="page-heading"><h1>{{ t('Assets') }}</h1><button v-if="allows(workspace, 'assets.create') && !editing" class="primary" @click="start">{{ t('New asset') }}</button></div>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <form v-validated-form v-if="editing" class="panel record-form" @submit.prevent="save">
    <h2>{{ t('New asset') }}</h2>
    <div class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="name" required maxlength="120"></label><FieldHint :label="t('Name')" required /></div>
    <div class="field-wrap"><label>{{ t('Tag') }}<input data-field="tag" :placeholder="t('Enter {field}').replace('{field}', t('Tag'))" v-model="tag" required maxlength="60"></label><FieldHint :label="t('Tag')" required /></div>
    <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site_id" v-model="site">
      <option v-if="allows(workspace, 'assets.create', null)" :value="null">{{ t('No site') }}</option>
      <option v-for="item in sites" :key="item.id" :value="item.id">{{ item.name }}</option>
    </select></label><FieldHint :label="t('Site')" /></div>
    <div class="actions"><button class="primary" :disabled="busy">{{ t('Save') }}</button><button class="secondary" type="button" @click="editing = false">{{ t('Cancel') }}</button></div>
  </form>
<TableToolbar  :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/>
  <p v-if="loading" class="muted">{{ t('Loading') }}…</p>
  <section v-else class="panel table-panel">
    <table v-if="matchingRecords.length"><thead><tr><th>{{ t('Tag') }}</th><th>{{ t('Name') }}</th><th>{{ t('Site') }}</th></tr></thead>
      <tbody><tr v-for="asset in matchingRecords" :key="asset.id"><td><RouterLink :to="'/assets/' + asset.id"><bdi>{{ asset.tag }}</bdi></RouterLink></td><td>{{ asset.name }}</td><td>{{ workspace.sites.find(s => s.id === asset.site_id)?.name ?? '—' }}</td></tr></tbody>
    </table>
    <p v-else class="empty muted">{{ t('No assets yet.') }}</p>
  </section>
</template>
