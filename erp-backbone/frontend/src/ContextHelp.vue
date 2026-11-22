<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { fieldHelp, formHelpFields, helpTopic } from './helpText'
import { t } from './i18n'

const emit = defineEmits<{close: []}>()
const route = useRoute()
const fields = computed(() => formHelpFields(String(route.name ?? 'home'), route.path))
const topic = computed(() => helpTopic(String(route.name ?? 'home'), String(route.params.section ?? '')))
</script>

<template>
  <section id="context-help" class="context-help panel" :aria-label="t('Page help')" @keydown.esc.stop="emit('close')">
    <header>
      <span>{{ t('Page help') }}</span>
      <button type="button" class="secondary" :aria-label="t('Close help')" @click="emit('close')">×</button>
    </header>
    <div class="context-help-body" aria-live="polite">
      <h2>{{ topic.title }}</h2>
      <p>{{ topic.purpose }}</p>
      <h3>{{ t('Fields and choices') }}</h3>
      <p>{{ topic.fields }}</p>
      <dl v-if="fields.length" class="context-help-fields">
        <div v-for="field in fields" :key="field"><dt>{{ t(field) }}</dt><dd>{{ fieldHelp(field) }}</dd></div>
      </dl>
      <h3>{{ t('Next step') }}</h3>
      <p>{{ topic.next }}</p>
    </div>
  </section>
</template>
