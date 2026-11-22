<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from './api'
import { kindLabels } from './experience'
import { message, t } from './i18n'

type Result = {kind: string; title: string; number: string; route: string}
const route = useRoute()
const query = computed(() => typeof route.query.q === 'string' ? route.query.q : '')
const rows = ref<Result[]>([])
const error = ref('')
const busy = ref(false)
const groups = computed(() => {
  const grouped = new Map<string, Result[]>()
  for (const row of rows.value) {
    if (!grouped.has(row.kind)) grouped.set(row.kind, [])
    grouped.get(row.kind)!.push(row)
  }
  return grouped
})
watch(query, async (term, _, onCleanup) => {
  let current = true
  onCleanup(() => { current = false })
  rows.value = []
  error.value = ''
  busy.value = term.trim().length >= 2
  if (!busy.value) return
  try {
    const result = await api<{items: Result[]}>('/search?q=' + encodeURIComponent(term))
    if (current) rows.value = result.items
  } catch (failure) { if (current) error.value = message(failure) }
  finally { if (current) busy.value = false }
}, {immediate: true})
</script>

<template>
  <h1>{{ t('Global search') }}</h1>
  <p v-if="busy">{{ t('Loading') }}…</p>
  <p v-else-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-else-if="query.trim().length < 2">{{ t('Enter at least two characters.') }}</p>
  <p v-else-if="!rows.length">{{ t('No matching records.') }}</p>
  <section v-for="[kind, records] in groups" :key="kind" class="panel record-form">
    <h2>{{ t(kindLabels[kind] ?? kind) }}</h2>
    <ul><li v-for="row in records" :key="row.route">
      <RouterLink :to="row.route"><bdi>{{ row.number }}</bdi> · {{ row.title }}</RouterLink>
    </li></ul>
  </section>
</template>
