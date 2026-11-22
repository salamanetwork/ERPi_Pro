<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, onMounted, reactive, ref } from 'vue'
import { allows,api,type Workspace } from './api'
import { locale, message, t } from './i18n'

type Event = { id: number; actor: string; actor_user_id: number | null; display_name: string; action: string; target: string; occurred_at: string }
const {workspace}=defineProps<{workspace:Workspace}>()
const filters=reactive({from:'',to:'',actor:'',action:''})
const applied=ref('')
const exporter=computed(()=>'/api/audit/export?'+applied.value)
const events = ref<Event[]>()
const error = ref('')
const page=ref(1),size=ref(25),count=ref(0),q=ref(""); let serial=0
async function load(){
  const token=++serial
  error.value=''
  const query=new URLSearchParams(Object.entries({...filters,q:q.value}).filter(([,value])=>value)).toString()
  try{
    const d=await api<{events:Event[];count:number;page:number}>('/audit?'+query+'&page='+page.value+'&page_size='+size.value);if(token!==serial)return;events.value=d.events;count.value=d.count;page.value=d.page
    applied.value=query
  }catch(reason){error.value=message(reason)}
}
onMounted(load)
import DateInput from './DateInput.vue'
</script>

<template>
  <h1>{{ t('Audit trail') }}</h1>
  <form v-validated-form class="actions approval-section" @submit.prevent="page=1;load()">
    <DateInput data-field="from" :label="t('From') + ' (UTC)'" v-model="filters.from" type="date" />
    <DateInput data-field="to" :label="t('To') + ' (UTC)'" v-model="filters.to" type="date" />
    <div class="field-wrap"><label>{{t('Actor')}}<input data-field="actor" :placeholder="t('Enter {field}').replace('{field}', t('Actor'))" v-model="filters.actor" maxlength="150"></label><FieldHint :label="t('Actor')" /></div>
    <div class="field-wrap"><label>{{t('Action')}}<input data-field="action" :placeholder="t('Enter {field}').replace('{field}', t('Action'))" v-model="filters.action" maxlength="100"></label><FieldHint :label="t('Action')" /></div>
    <button class="primary">{{t('Apply filters')}}</button>
    <a v-if="allows(workspace,'platform.export',null) && events" class="button secondary" :href="exporter">{{t('Export CSV')}}</a>
  </form>
  <TableToolbar :page="page" :size="size" :count="count" :query="q" @search="q=$event;page=1;load()" @page="page=$event;load()" @size="size=$event;page=1;load()"/>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-else-if="!events">{{ t('Loading') }}…</p>
  <section v-else class="panel table-panel">
    <table v-if="events.length"><thead><tr><th>{{ t('Actor') }}</th><th>{{ t('Action') }}</th><th>{{ t('Target') }}</th><th>{{ t('Time') }}</th></tr></thead>
      <tbody><tr v-for="event in events" :key="event.id"><td><bdi>{{ event.display_name }}</bdi><small v-if="event.actor_user_id" class="muted audit-identity"><bdi>{{ event.actor }} · #{{ event.actor_user_id }}</bdi></small></td><td><bdi>{{ event.action }}</bdi></td><td><bdi>{{ event.target }}</bdi></td><td>{{ new Date(event.occurred_at).toLocaleString(locale) }}</td></tr></tbody>
    </table>
    <p v-else class="empty muted">{{ t('No activity yet.') }}</p>
  </section>
</template>
