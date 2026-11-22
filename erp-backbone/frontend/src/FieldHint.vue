<script setup lang="ts">
import { ref, useId, watch } from 'vue'
import { t } from './i18n'
import { fieldHelp } from './helpText'

defineProps<{ label: string; hintKey?: string; required?: boolean; readonly?: boolean }>()
const id = useId()
const root = ref<HTMLElement>()
const pinned = ref(false)
const hovering = ref(false)
const focused = ref(false)
const dismissed = ref(false)
watch(pinned, (open, _, cleanup) => {
  if (!open) return
  function outside(event: PointerEvent) {
    if (!root.value?.contains(event.target as Node)) dismiss()
  }
  document.addEventListener('pointerdown', outside)
  cleanup(() => document.removeEventListener('pointerdown', outside))
})
function toggle() {
  if (pinned.value) dismiss()
  else { dismissed.value = false; pinned.value = true }
}
function dismiss() {
  pinned.value = false
  dismissed.value = true
}
</script>

<template>
  <div ref="root" class="field-hint" @pointerenter="event => { if (event.pointerType === 'mouse') { hovering = true; dismissed = false } }"
    @pointerleave="hovering = false" @keydown.esc.stop="dismiss">
    <span v-if="required && !readonly" class="field-required" aria-hidden="true">*</span>
    <button type="button" class="field-info" :aria-label="t('Help for') + ' ' + label"
      :aria-expanded="!dismissed && (pinned || hovering || focused)" :aria-describedby="id"
      @focus="focused = true; dismissed = false" @blur="focused = false; pinned = false" @click="toggle">ⓘ</button>
    <div v-show="!dismissed && (pinned || hovering || focused)" :id="id" class="field-tooltip panel" role="tooltip">
      <strong>{{ label }}</strong>
      <p>{{ fieldHelp(hintKey || label) }}</p>
      <small>{{ t(readonly ? 'Read-only field' : required ? 'Required field' : 'Optional field') }}</small>
    </div>
  </div>
</template>
