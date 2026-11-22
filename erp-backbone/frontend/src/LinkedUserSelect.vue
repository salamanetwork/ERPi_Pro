<script setup lang="ts">
import { nextTick, ref, useId, watch } from 'vue'
import { api } from './api'
import { message, t } from './i18n'

type Account = { id: number; username: string; name: string }
type Result = { selected: Account | null; locked: boolean; items: Account[] }
const props = defineProps<{ site: number; employee?: number; disabled?: boolean }>()
const selected = defineModel<string>({ required: true })
const listId = useId()
const searchId = useId()
const input = ref<HTMLInputElement>()
const root = ref<HTMLElement>()
const expanded = ref(false)
const query = ref('')
const options = ref<Account[]>([])
const choice = ref<Account>()
const locked = ref(false)
const ready = ref(false)
const pending = ref(false)
const active = ref(-1)
const failure = ref('')

function params() {
  return new URLSearchParams(props.employee ? {employee: String(props.employee)} : {})
}
function url() { return '/hr/sites/' + props.site + '/linkable-users?' }
watch(() => [props.site, props.employee], async (_, previous, cleanup) => {
  let current = true
  cleanup(() => { current = false })
  if (previous && previous[0] !== props.site && !props.employee) selected.value = ''
  expanded.value = ready.value = locked.value = false
  choice.value = undefined
  query.value = failure.value = ''
  try {
    const data = await api<Result>(url() + params())
    if (!current) return
    choice.value = data.selected ?? undefined
    locked.value = data.locked
    ready.value = true
  } catch (error) { if (current) failure.value = message(error) }
}, {immediate: true})

watch([query, expanded], ([term, open], _, cleanup) => {
  options.value = []
  active.value = -1
  failure.value = ''
  pending.value = open && term.trim().length >= 2
  if (!pending.value) return
  let current = true
  const timeout = window.setTimeout(async () => {
    const search = params()
    search.set('q', term)
    try {
      const data = await api<Result>(url() + search)
      if (current) options.value = data.items
    } catch (error) { if (current) failure.value = message(error) }
    finally { if (current) pending.value = false }
  }, 220)
  cleanup(() => { current = false; window.clearTimeout(timeout) })
})
function open() {
  expanded.value = true
  void nextTick(() => input.value?.focus())
}
function choose(account?: Account) {
  choice.value = account
  selected.value = account ? String(account.id) : ''
  root.value?.dispatchEvent(new Event('validation-edit', {bubbles: true}))
  close()
}
function close() {
  expanded.value = false
  query.value = ''
  void nextTick(() => root.value?.querySelector<HTMLElement>('.linked-user-trigger')?.focus())
}
function keyboard(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); close() }
  if (!options.value.length) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    active.value = Math.min(options.value.length - 1, Math.max(0, active.value + (event.key === 'ArrowDown' ? 1 : -1)))
    document.getElementById(listId + '-' + active.value)?.scrollIntoView({block:'nearest'})
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    if (active.value >= 0) choose(options.value[active.value])
  }
}
</script>

<template>
  <div ref="root" class="linked-user field-wrap">
    <FieldHint :label="t('Linked user')" :readonly="locked" />
    <label :for="searchId" class="linked-user-label">{{ t('Linked user') }}</label>
    <button data-validation-control :value="selected" :id="searchId" type="button" class="secondary linked-user-trigger"
      :disabled="disabled || !ready || locked" :aria-expanded="expanded" :aria-controls="listId" @click="expanded ? close() : open()">
      <bdi v-if="choice">{{ choice.username }}<template v-if="choice.name"> · {{ choice.name }}</template></bdi>
      <span v-else>{{ t('Choose user') }}</span><span aria-hidden="true">⌄</span>
    </button>
    <small v-if="locked">{{ t('The linked account is locked after a payslip is created.') }}</small>
    <div v-if="expanded" class="panel linked-user-options">
      <div class="field-wrap table-field"><input data-field="query" ref="input" v-model="query" maxlength="120" type="search" role="combobox" aria-autocomplete="list"
        aria-expanded="true" :aria-controls="listId" :aria-activedescendant="active < 0 ? undefined : listId + '-' + active"
        :aria-label="t('Search users by username or name')" :placeholder="t('Search users by username or name')"
        @keydown="keyboard" @keydown.enter.prevent><FieldHint :label="t('Search users by username or name')" /></div>
      <p v-if="pending" role="status">{{ t('Loading') }}…</p>
      <p v-else-if="query.trim().length < 2">{{ t('Enter at least two characters.') }}</p>
      <p v-else-if="!options.length">{{ t('No matching users.') }}</p>
      <div :id="listId" role="listbox" :aria-label="t('Linked user')">
        <button v-for="(account, index) in options" :id="listId + '-' + index" :key="account.id" type="button"
          role="option" :aria-selected="active === index" @click="choose(account)">
          <bdi>{{ account.username }}</bdi><bdi v-if="account.name">{{ account.name }}</bdi>
        </button>
      </div>
      <div class="actions">
        <button v-if="choice" class="secondary" type="button" @click="choose()">{{ t('Clear linked user') }}</button>
        <button class="secondary" type="button" @click="close">{{ t('Close') }}</button>
      </div>
    </div>
    <small v-if="failure" class="field-error" role="alert">{{ t(failure) }}</small>
  </div>
</template>
