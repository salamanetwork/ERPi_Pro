<script setup lang="ts">
type Choice = { id: number; name: string; code: string }
defineProps<{ label: string; choices: Choice[]; currentLabel?: string; required?: boolean }>()
const model = defineModel<number | null>({ required: true })
</script>

<template>
  <EntityAutocomplete
    data-field="model"
    :label="label"
    :choices="choices"
    :current-label="currentLabel"
    :required="required"
    :empty-value="null"
    v-model="model"
  />
</template>
