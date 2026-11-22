<script setup lang="ts">
import { ref } from 'vue'
import { api } from './api'
import { message, t } from './i18n'
import { groups, platformScreens } from './navigation'

const sections = [{id:'platform',label:'Workspace',screens:platformScreens}, ...groups]
const props = defineProps<{ hidden: string[] }>()
const emit = defineEmits<{ saved: [hidden: string[]] }>()
const busy = ref(false)
const error = ref('')

async function toggle(id: string, event: Event) {
  const control = event.target as HTMLInputElement
  control.checked = !props.hidden.includes(id)
  if (busy.value) return
  busy.value = true
  error.value = ''
  const next = props.hidden.includes(id) ? props.hidden.filter(key => key !== id) : [...props.hidden, id]
  try {
    const result = await api<{ hidden_screens: string[] }>('/navigation/settings', 'PATCH', { hidden_screens: next })
    emit('saved', result.hidden_screens)
  } catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}
</script>

<template>
  <details class="panel navigation-settings">
    <summary>{{ t('Show or hide screens') }}</summary>
    <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
    <div class="navigation-settings-grid">
      <fieldset v-for="section in sections" :key="section.id">
        <legend>{{ t(section.label) }}</legend>
        <div class="field-wrap" v-for="screen in section.screens" :key="screen.id"><label class="check">
          <input type="checkbox" :checked="!hidden.includes(screen.id)" :disabled="busy" @change="toggle(screen.id, $event)">
          {{ t(screen.label) }}
        </label><FieldHint :label="t('Hide in navigation')" /></div>
      </fieldset>
    </div>
  </details>
</template>
