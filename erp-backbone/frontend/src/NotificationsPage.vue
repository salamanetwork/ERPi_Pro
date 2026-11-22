<script setup lang="ts">
import TableToolbar from './TableToolbar.vue'
import {watch} from 'vue'
const pg=ref(1),ps=ref(25),total=ref(0),term=ref('')

import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from './api'
import { actionLabels } from './experience'
import { message, t } from './i18n'

type Notice = {key: string; number: string; route: string; event: string; read: boolean}
const emit = defineEmits<{unread: [count: number]}>()
const router = useRouter()
const rows = ref<Notice[]>([])
const error = ref('')
const busy = ref(false)
async function load(payload?: {all: true} | {key: string}) {
  if (busy.value) return false
  busy.value = true
  error.value = ''
  try {
    const result = await api<{items: Notice[];count:number; unread: number}>('/notifications?'+new URLSearchParams({page:String(pg.value),page_size:String(ps.value),q:term.value}), payload ? 'POST' : 'GET', payload)
    rows.value = result.items;total.value=result.count
    emit('unread', result.unread)
    return true
  } catch (failure) { error.value = message(failure); return false }
  finally { busy.value = false }
}
async function open(row: Notice) {
  if (row.read || await load({key: row.key})) await router.push(row.route)
}
onMounted(() => { void load() })
watch([pg,ps,term],()=>load())
const poll = window.setInterval(() => { if (!document.hidden) void load() }, 60000)
onUnmounted(() => window.clearInterval(poll))
</script>

<template>
  <div class="page-heading"><h1>{{ t('Notifications') }}</h1>
    <button class="secondary" :disabled="busy || !rows.some(row => !row.read)" @click="load({all: true})">{{ t('Mark all as read') }}</button>
  </div>
<TableToolbar :page="pg" :size="ps" :count="total" :query="term" @page="pg=$event" @size="ps=$event;pg=1" @search="term=$event;pg=1"/>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-else-if="!rows.length && !busy">{{ t('No notifications.') }}</p>
  <section v-else class="panel table-panel">
    <table><thead><tr><th>{{ t('Record number') }}</th><th>{{ t('Action') }}</th><th>{{ t('Status') }}</th></tr></thead>
      <tbody><tr v-for="row in rows" :key="row.key">
        <td><button class="secondary" :disabled="busy" @click="open(row)">{{ row.number }}</button></td>
        <td>{{ t(actionLabels[row.event] ?? row.event) }}</td><td>{{ t(row.read ? 'Read' : 'Unread') }}</td>
      </tr></tbody>
    </table>
  </section>
</template>
