<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch} from 'vue'
const pg=ref(1),ps=ref(25),total=ref(0),term=ref('')

import SavedViews from './SavedViews.vue'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Workspace } from './api'
import { actionLabels, kindLabels } from './experience'
import { message, t } from './i18n'

type Task = {id: string; title: string; number: string; route: string; role: string | null; kind: string; site_id: number | null;
  action: string; remaining?: string}
const { workspace } = defineProps<{workspace: Workspace}>()
const route = useRoute()
const items = ref<Task[]>([])
const loading = ref(true)
const error = ref('')
const kind = ref(typeof route.query.kind === 'string' ? route.query.kind : '')
const site = ref(typeof route.query.site === 'string' ? route.query.site : '')
const action = ref(typeof route.query.action === 'string' ? route.query.action : '')
function applyView(filters: Record<string, string>) {
  kind.value = filters.kind ?? ''
  site.value = filters.site ?? ''
  action.value = filters.action ?? ''
}
const filtered = computed(() => items.value.filter(row => (!kind.value || kind.value === row.kind) &&
  (!site.value || site.value === String(row.site_id)) && (!action.value || action.value === row.action)))
const facets=ref<{site_id:string[];kind:string[];action:string[]}>({site_id:[],kind:[],action:[]})
const sites = computed(() => workspace.sites.filter(entry => facets.value.site_id.includes(String(entry.id)) || String(entry.id) === site.value))
const actions = computed(() => Object.keys(actionLabels).filter(key => facets.value.action.includes(key) || key === action.value))
const kinds = computed(() => Object.keys(kindLabels).filter(key => facets.value.kind.includes(key) || key === kind.value))
let sequence = 0

async function load() {
  const token = ++sequence
  try {
    const data = await api<{items: Task[];count:number;facets:typeof facets.value}>('/inbox?'+new URLSearchParams({kind:kind.value,action:action.value,site:site.value,page:String(pg.value),page_size:String(ps.value),q:term.value}))
    if (token === sequence) { items.value = data.items;total.value=data.count;facets.value=data.facets; error.value = '' }
  } catch (failure) { if (token === sequence) error.value = message(failure) }
  finally { if (token === sequence) loading.value = false }
}
function link(item: Task) {
  const back = new URLSearchParams()
  for (const [key, value] of Object.entries({kind: kind.value, site: site.value, action: action.value})) {
    if (value) back.set(key, value)
  }
  return {path: item.route, query: {fromInbox: back.toString() || '1'}}
}
watch([kind,site,action],()=>pg.value=1)
watch([pg,ps,term,kind,site,action],load)
const poll = window.setInterval(() => { if (!document.hidden) void load() }, 60000)
onMounted(() => {
  void load()
  window.addEventListener('focus', load)
  window.addEventListener('pageshow', load)
  window.addEventListener('erp:work-changed', load)
})
onUnmounted(() => {
  ++sequence
  window.clearInterval(poll)
  window.removeEventListener('erp:work-changed', load)
  window.removeEventListener('focus', load)
  window.removeEventListener('pageshow', load)
})
</script>

<template>
  <div class="page-heading">
    <h1>{{ t('Inbox') }}</h1>
    <button class="secondary" :disabled="loading" @click="load">{{ t('Refresh') }}</button>
  </div>
  <div class="actions">
    <div class="field-wrap"><label>{{ t('Module') }}<select data-field="kind" v-model="kind"><option value="">{{ t('all') }}</option>
      <option v-for="key in kinds" :key="key" :value="key">{{ t(kindLabels[key]!) }}</option></select></label><FieldHint :label="t('Module')" /></div>
    <div class="field-wrap"><label>{{ t('Site') }}<select data-field="site" v-model="site"><option value="">{{ t('all') }}</option>
      <option v-if="site && !sites.some(row => String(row.id) === site)" :value="site">{{ t('Site unavailable') }}</option>
      <option v-for="row in sites" :key="row.id" :value="String(row.id)">{{ row.name }}</option></select></label><FieldHint :label="t('Site')" /></div>
    <div class="field-wrap"><label>{{ t('Action') }}<select data-field="action" v-model="action"><option value="">{{ t('all') }}</option>
      <option v-for="key in actions" :key="key" :value="key">{{ t(actionLabels[key]!) }}</option></select></label><FieldHint :label="t('Action')" /></div>
  </div>
  <SavedViews :workspace="workspace" screen="inbox" :filters="{kind, site, action}"
    :skip-default="Boolean(route.query.fromTask || route.query.kind || route.query.site || route.query.action)" @apply="applyView" />
<TableToolbar :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-else-if="loading">{{ t('Loading') }}…</p>
  <section v-else class="panel table-panel approval-section">
    <table v-if="filtered.length">
      <thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Title') }}</th><th>{{ t('Site') }}</th><th>{{ t('Action') }}</th><th>{{ t('Role') }}</th></tr></thead>
      <tbody><tr v-for="item in filtered" :key="item.id">
        <td><RouterLink :to="link(item)">{{ item.number }}</RouterLink></td><td>{{ item.title }}</td>
        <td>{{ workspace.sites.find(row => row.id === item.site_id)?.name ?? '—' }}</td>
        <td>{{ t(actionLabels[item.action] ?? item.action) }}<span v-if="item.remaining"> · {{ t('Remaining') }} {{ item.remaining }}</span></td>
        <td>{{ item.role ?? '—' }}</td>
      </tr></tbody>
    </table>
    <p v-else class="empty muted">{{ t('No tasks in your inbox.') }}</p>
  </section>
</template>
