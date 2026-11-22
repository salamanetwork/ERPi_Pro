<script setup lang="ts">
import { ref } from 'vue'
import { t } from './i18n'

defineProps<{version:string;mode:'development'|'production'}>()
const expanded = ref(false)
</script>

<template>
  <footer class="app-footer">
    <div id="footer-space" v-show="expanded" class="footer-space"></div>
    <div class="app-footer-inner">
      <div class="footer-meta">
        <bdi>ERPi Pro © 2026</bdi>
        <span>{{ t('Version') }} <bdi>{{ version }}</bdi></span>
        <span>{{ t('Mode:') }} {{ t(mode === 'production' ? 'Production' : 'Development') }}</span>
      </div>
      <button class="secondary footer-toggle" type="button" aria-controls="footer-space" :aria-expanded="expanded"
        :aria-label="t(expanded ? 'Collapse footer' : 'Expand footer')" @click="expanded = !expanded">
        <span aria-hidden="true">{{ expanded ? '−' : '+' }}</span>
      </button>
    </div>
  </footer>
</template>
