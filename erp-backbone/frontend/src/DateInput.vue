<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { locale, t } from './i18n'

defineOptions({ inheritAttrs: false })
const props = withDefaults(defineProps<{
  type?: 'date' | 'datetime-local'; min?: string | null; max?: string | null
  required?: boolean; readonly?: boolean; disabled?: boolean; label: string
}>(), {type: 'date'})
const emit = defineEmits<{change: []}>()
const value = defineModel<string | null | undefined>({required:true})
const control = ref<HTMLInputElement>()
const date = ref('')
const time = ref('')
const id = useId()
const calendar = ref<HTMLDetailsElement>()
const month = ref(new Date())
const hasTime = computed(() => props.type === 'datetime-local')
const dayValue = date
const clockValue = time
watch(value, (next) => {
  if (hasTime.value && !next && (date.value || time.value) && !(date.value && time.value)) return
  const [day = '', clock = ''] = (next ?? '').split('T')
  date.value = day
  time.value = clock
}, {immediate:true})
function update(day: string, clock: string) {
  date.value = day
  time.value = clock
  const next = hasTime.value ? day && clock ? day + 'T' + clock : '' : day
  if (next !== value.value) {
    value.value = next
    emit('change')
  }
}
function sync() {
  if (!calendar.value?.open) return
  const selected = dayValue.value ? new Date(dayValue.value + 'T12:00:00') : new Date()
  if (!Number.isNaN(selected.getTime())) month.value = selected
}
const monthLabel = computed(() => new Intl.DateTimeFormat(locale.value, {month:'long', year:'numeric'}).format(month.value))
const dayNames = computed(() => Array.from({length:7}, (_, i) =>
  new Intl.DateTimeFormat(locale.value, {weekday:'short', timeZone:'UTC'}).format(new Date(Date.UTC(2024,0,1+i)))))
function iso(d: Date) {
  return String(d.getFullYear()).padStart(4,'0') + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0')
}
const days = computed(() => {
  const first = new Date(month.value)
  first.setDate(1)
  const blank = (first.getDay() + 6) % 7
  const lastDay = new Date(first)
  lastDay.setMonth(lastDay.getMonth() + 1, 0)
  const last = lastDay.getDate()
  return [...Array<null>(blank).fill(null), ...Array.from({length:last}, (_, i) => {
    const d = new Date(first)
    d.setDate(i+1)
    return {date:iso(d), display: new Intl.NumberFormat(locale.value).format(i+1)}
  })]
})
function move(delta: number) {
  const next = new Date(month.value)
  next.setDate(1)
  next.setMonth(next.getMonth()+delta)
  if (next.getFullYear() >= 1 && next.getFullYear() <= 9999) month.value = next
}
function choose(day: string) {
  update(day, clockValue.value)
  control.value?.dispatchEvent(new Event('validation-edit', {bubbles:true}))
  if (calendar.value) {
    calendar.value.open = false
    calendar.value.querySelector('summary')?.focus()
  }
}
</script>

<template>
  <div class="date-field field-wrap" :class="{'date-only': !hasTime}" v-bind="$attrs">
    <FieldHint :label="label" :required="required" :readonly="readonly" />
    <label :for="id">{{ label }}</label>
    <div class="date-controls">
      <input ref="control" :id="id" type="date" :aria-describedby="!readonly && !disabled ? id + '-hint' : undefined" :value="dayValue" :min="min ?? undefined" :max="max ?? undefined"
        :required="required || hasTime && !!clockValue" :readonly="readonly" :disabled="disabled"
        @input="update(($event.target as HTMLInputElement).value, clockValue)">
      <details v-if="!readonly && !disabled" ref="calendar" class="calendar-picker" @toggle="sync"
        @keydown.esc.stop.prevent="calendar!.open = false; calendar!.querySelector('summary')?.focus()">
        <summary class="button secondary" :aria-label="t('Open calendar')">▦</summary>
        <div class="panel calendar-panel">
          <div class="calendar-header">
            <button type="button" class="secondary" :aria-label="t('Previous month')" @click="move(-1)">‹</button>
            <strong aria-live="polite">{{ monthLabel }}</strong>
            <button type="button" class="secondary" :aria-label="t('Next month')" @click="move(1)">›</button>
          </div>
          <div class="calendar-grid">
            <span v-for="name in dayNames" :key="name" class="muted">{{ name }}</span>
            <template v-for="(day, index) in days" :key="index">
              <span v-if="!day"></span>
              <button v-else type="button" :class="day.date === dayValue ? 'primary' : 'secondary'"
                :aria-pressed="day.date === dayValue" :aria-label="day.date"
                :disabled="!!(min && day.date < min || max && day.date > max)" @click="choose(day.date)">{{ day.display }}</button>
            </template>
          </div>
        </div>
      </details>
    </div>
    <small v-if="!readonly && !disabled" :id="id + '-hint'" class="muted date-entry-hint">{{ t('Choose a date from the calendar or enter it in the date field.') }}</small>
    <div v-if="hasTime" class="field-wrap">
      <label class="date-clock">{{ t('Time (hour and minute)') }}
        <input type="time" :aria-describedby="!readonly && !disabled ? id + '-time-hint' : undefined" :value="clockValue" :required="required || !!dayValue" :readonly="readonly" :disabled="disabled"
          @input="update(dayValue, ($event.target as HTMLInputElement).value)">
      </label>
      <small v-if="!readonly && !disabled" :id="id + '-time-hint'" class="muted date-entry-hint">{{ t('Enter the hour and minute.') }}</small>
      <FieldHint :label="t('Time (hour and minute)')" :required="required || !!dayValue" :readonly="readonly" />
    </div>
  </div>
</template>
