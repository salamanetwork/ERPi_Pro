<script setup lang="ts">
import { computed } from 'vue'
const p = defineProps<{ text: string; query: string }>()
const parts = computed(() => {
  const query = p.query.trim().toLowerCase(),
    folded = p.text.toLowerCase()
  const result: { text: string; hit: boolean }[] = []
  let cursor = 0
  if (query) {
    let index = folded.indexOf(query)
    while (index >= 0) {
      result.push(
        { text: p.text.slice(cursor, index), hit: false },
        { text: p.text.slice(index, index + query.length), hit: true },
      )
      cursor = index + query.length
      index = folded.indexOf(query, cursor)
    }
  }
  result.push({ text: p.text.slice(cursor), hit: false })
  return result
})
</script>
<template>
  <template v-for="(part, i) in parts" :key="i"
    ><mark v-if="part.hit">{{ part.text }}</mark
    ><template v-else>{{ part.text }}</template></template
  >
</template>
