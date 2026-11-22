<script setup lang="ts">
import { onBeforeUnmount } from 'vue'
import { t } from './i18n'

const props = defineProps<{width:number;min:number;max:number;edge:'start'|'end';label:string;controls:string}>()
const emit = defineEmits<{resize:[size:number];commit:[]}>()
let drag: {id:number;x:number;width:number;direction:number} | undefined
let frame = 0
let pending: number | undefined

function direction(target: EventTarget | null) {
  const rtl = getComputedStyle(target as HTMLElement).direction === 'rtl'
  return (rtl ? -1 : 1) * (props.edge === 'start' ? 1 : -1)
}
function resize(value: number) {
  emit('resize', Math.round(Math.max(props.min, Math.min(props.max, value))))
}
function flush() {
  frame = 0
  if (pending !== undefined) resize(pending)
  pending = undefined
}
function start(event: PointerEvent) {
  if (!event.isPrimary || event.button !== 0) return
  const handle = event.currentTarget as HTMLElement
  handle.focus({preventScroll:true})
  handle.setPointerCapture(event.pointerId)
  drag = {id:event.pointerId,x:event.clientX,width:props.width,direction:direction(handle)}
}
function move(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.id) return
  pending = drag.width + (event.clientX - drag.x) * drag.direction
  if (!frame) frame = requestAnimationFrame(flush)
}
function finish(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.id) return
  if (frame) cancelAnimationFrame(frame)
  if (event.type === 'pointerup') {
    pending = drag.width + (event.clientX - drag.x) * drag.direction
    flush()
    emit('commit')
  } else {
    resize(drag.width)
    frame = 0
    pending = undefined
  }
  drag = undefined
}
function keyboard(event: KeyboardEvent) {
  const step = (event.shiftKey ? 32 : 8) * direction(event.currentTarget)
  const key = event.key
  if (key === 'ArrowLeft') resize(props.width - step)
  else if (key === 'ArrowRight') resize(props.width + step)
  else if (key === 'Home') resize(props.min)
  else if (key === 'End') resize(props.max)
  else return
  event.preventDefault()
  emit('commit')
}
onBeforeUnmount(() => {
  if (frame) cancelAnimationFrame(frame)
  if (drag) resize(drag.width)
})
</script>

<template>
  <div class="panel-resizer" :class="'resize-'+edge" role="separator" tabindex="0" aria-orientation="vertical"
    :aria-controls="controls" :aria-label="t(label)" :title="t(label)" :aria-valuemin="min" :aria-valuemax="max"
    :aria-valuenow="width" @pointerdown="start" @pointermove="move" @pointerup="finish" @pointercancel="finish"
    @lostpointercapture="finish" @keydown="keyboard" />
</template>
