<script setup lang="ts" generic="Row extends { id: number }">
import { useRouter,useRoute } from 'vue-router'
import TableToolbar from './TableToolbar.vue'
import { t } from './i18n'
import type { Page } from './useDirectory'

const props = defineProps<{ page?: Page<Row>; headings: string[]; loading?: boolean; error?: string }>()
const emit = defineEmits<{page: [value: string]}>()
const router=useRouter(),route=useRoute()
function resize(n:number){void router.replace({query:{...route.query,page:'1',page_size:String(n)}})}
</script>

<template>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <section class="panel table-panel" :aria-busy="loading">
    <table v-if="page?.results.length">
      <thead><tr><th v-for="heading in headings" :key="heading" scope="col">{{ t(heading) }}</th></tr></thead>
      <tbody><tr v-for="row in page.results" :key="row.id"><slot :row="row" /></tr></tbody>
    </table>
    <p v-else class="empty">{{ t(loading ? 'Loading' : 'No matching records.') }}</p>
  </section>
  <TableToolbar v-if="props.page" :page="props.page.page" :size="props.page.page_size" :count="props.page.count" @size="resize" @page="emit('page',String($event))"/>
</template>