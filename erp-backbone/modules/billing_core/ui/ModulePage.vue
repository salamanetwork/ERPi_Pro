<script setup lang="ts">
import BusinessWorkspace from '../../../frontend/src/BusinessWorkspace.vue'
import type {Workspace} from '../../../frontend/src/api'
defineProps<{workspace:Workspace}>()
</script>
<template><BusinessWorkspace module="billing_core" :workspace="workspace"/></template>