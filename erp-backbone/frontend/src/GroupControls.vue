<script setup lang="ts">
import { t } from './i18n'
defineProps<{ expanded: boolean; collapsed: boolean }>()
const emit = defineEmits<{open: []; close: []}>()
</script>
<template>
  <div class="group-controls" role="group" :aria-label="t('Section controls')">
    <button type="button" class="secondary" :aria-label="t('Expand all')" :title="t('Expand all')"
      :disabled="expanded" @click="emit('open')"><span aria-hidden="true">+</span></button>
    <button type="button" class="secondary" :aria-label="t('Collapse all')" :title="t('Collapse all')"
      :disabled="collapsed" @click="emit('close')"><span aria-hidden="true">−</span></button>
  </div>
</template>
