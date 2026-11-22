<script setup lang="ts">
import ExpensePage from '../../../frontend/src/ExpensePage.vue'
import type {Workspace} from '../../../frontend/src/api'
defineProps<{workspace:Workspace}>()
</script>
<template><ExpensePage :workspace="workspace"/></template>
