<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { api } from './api'
import { locale, message } from './i18n'

type Step = { id: string; text_en: string; text_ar: string; screenshots?: Record<string, string> }
type Guide = {
  id: string
  module: string
  module_name: { en: string; ar: string }
  required_permission: string
  route: string
  title_en: string
  title_ar: string
  keywords_en: string[]
  keywords_ar: string[]
  prerequisites_en?: string
  prerequisites_ar?: string
  steps: Step[]
}
type Slide = {
  key: string
  src: string
  route: string
  guideTitleEn: string
  guideTitleAr: string
  moduleNameEn: string
  moduleNameAr: string
  textEn: string
  textAr: string
}

const props = defineProps<{ username: string }>()
const emit = defineEmits<{ close: [] }>()

const storage = 'erp:sop:' + props.username
const done = ref<Record<string, string[]>>({})
try { done.value = JSON.parse(localStorage.getItem(storage) || '{}') } catch {}

const guideKey = (g: Guide) => g.module + '/' + g.id
function checked(g: Guide, s: Step) { return (done.value[guideKey(g)] || []).includes(s.id) }
function update(g: Guide, s?: Step) {
  const k = guideKey(g), old = done.value[k] || []
  done.value[k] = s ? (checked(g, s) ? old.filter(x => x !== s.id) : [...old, s.id]) : []
  try { localStorage.setItem(storage, JSON.stringify(done.value)) } catch {}
}

const list = ref<Guide[]>([])
const q = ref('')
const filter = ref('')
const error = ref('')
const l = (en: string, ar: string) => (locale.value === 'ar' ? ar : en)
const fmtNum = (n: number) => (locale.value === 'ar' ? n.toLocaleString('ar-EG') : String(n))

onMounted(async () => {
  try {
    list.value = (await api<{ results: Guide[] }>('/platform/v1/sop-guides')).results
  } catch (e) {
    error.value = message(e)
  }
})

const modules = computed(() => Array.from(new Map(list.value.map(g => [g.module, g.module_name])).entries()))
const found = computed(() =>
  list.value.filter(
    g =>
      (!filter.value || g.module === filter.value) &&
      JSON.stringify([g.title_en, g.title_ar, g.module_name, g.keywords_en, g.keywords_ar, g.steps])
        .toLowerCase()
        .includes(q.value.toLowerCase().trim())
  )
)

const slides = computed<Slide[]>(() => {
  const out: Slide[] = []
  for (const g of found.value) {
    for (const s of g.steps) {
      const src = s.screenshots?.[locale.value]
      if (src) {
        out.push({
          key: `${g.module}/${g.id}/${s.id}`,
          src,
          route: g.route,
          guideTitleEn: g.title_en,
          guideTitleAr: g.title_ar,
          moduleNameEn: g.module_name.en,
          moduleNameAr: g.module_name.ar,
          textEn: s.text_en,
          textAr: s.text_ar,
        })
      }
    }
  }
  return out
})

const activeIndex = ref<number | null>(null)
const currentSlide = computed<Slide | null>(() =>
  activeIndex.value !== null ? (slides.value[activeIndex.value] ?? null) : null
)

const stageEl = ref<HTMLElement | null>(null)
const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })
const dragging = ref(false)
const dragStart = ref({ x: 0, y: 0 })

function clampPan(x: number, y: number, scale: number) {
  if (scale <= 1) return { x: 0, y: 0 }
  const rect = stageEl.value?.getBoundingClientRect()
  const maxX = rect ? rect.width * 0.45 * scale : 640
  const maxY = rect ? rect.height * 0.45 * scale : 420
  return {
    x: Math.min(maxX, Math.max(-maxX, x)),
    y: Math.min(maxY, Math.max(-maxY, y)),
  }
}

function resetView() {
  zoom.value = 1
  pan.value = { x: 0, y: 0 }
}

function openLightbox(slideKey: string) {
  const idx = slides.value.findIndex(s => s.key === slideKey)
  if (idx !== -1) {
    activeIndex.value = idx
    resetView()
  }
}

function closeLightbox() {
  activeIndex.value = null
  dragging.value = false
  resetView()
}

function setZoom(next: number, focal?: { clientX: number; clientY: number }) {
  const prev = zoom.value
  const clamped = Math.min(4, Math.max(0.5, Number(next.toFixed(2))))
  if (clamped <= 1) {
    zoom.value = clamped
    pan.value = { x: 0, y: 0 }
    return
  }
  if (focal && stageEl.value && prev > 0) {
    const rect = stageEl.value.getBoundingClientRect()
    const offsetX = focal.clientX - (rect.left + rect.width / 2)
    const offsetY = focal.clientY - (rect.top + rect.height / 2)
    const ratio = clamped / prev
    const nextX = offsetX - (offsetX - pan.value.x) * ratio
    const nextY = offsetY - (offsetY - pan.value.y) * ratio
    zoom.value = clamped
    pan.value = clampPan(nextX, nextY, clamped)
    return
  }
  zoom.value = clamped
  pan.value = clampPan(pan.value.x, pan.value.y, clamped)
}

function stepSlide(delta: number) {
  if (activeIndex.value === null || !slides.value.length) return
  const total = slides.value.length
  activeIndex.value = (activeIndex.value + delta + total) % total
  resetView()
}

function onWheel(e: WheelEvent) {
  const step = e.deltaY < 0 ? 0.25 : -0.25
  setZoom(zoom.value + step, { clientX: e.clientX, clientY: e.clientY })
}

function toggleDoubleZoom(e: MouseEvent) {
  if (zoom.value > 1) resetView()
  else setZoom(2, { clientX: e.clientX, clientY: e.clientY })
}

function onPointerDown(e: PointerEvent) {
  if (zoom.value <= 1) return
  dragging.value = true
  dragStart.value = { x: e.clientX - pan.value.x, y: e.clientY - pan.value.y }
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (!dragging.value) return
  pan.value = clampPan(e.clientX - dragStart.value.x, e.clientY - dragStart.value.y, zoom.value)
}

function onPointerUp(e: PointerEvent) {
  if (!dragging.value) return
  dragging.value = false
  const el = e.currentTarget as HTMLElement
  if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId)
}

function onKeydown(e: KeyboardEvent) {
  if (currentSlide.value) {
    if (e.key === 'Escape') { e.preventDefault(); closeLightbox(); return }
    if (e.key === '+' || e.key === '=') { e.preventDefault(); setZoom(zoom.value + 0.25); return }
    if (e.key === '-') { e.preventDefault(); setZoom(zoom.value - 0.25); return }
    if (e.key === '0') { e.preventDefault(); resetView(); return }
    if (e.key === 'ArrowRight') { e.preventDefault(); stepSlide(locale.value === 'ar' ? -1 : 1); return }
    if (e.key === 'ArrowLeft') { e.preventDefault(); stepSlide(locale.value === 'ar' ? 1 : -1); return }
    return
  }
  if (e.key === 'Escape' && !e.defaultPrevented) emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <section class="panel stack" style="height:100%;overflow:auto">
    <header class="actions">
      <h2>{{ l('Training & procedures', 'التدريب وإجراءات العمل') }}</h2>
      <button class="secondary help-close-icon" :aria-label="l('Close help', 'إغلاق المساعدة')" @click="emit('close')">×</button>
    </header>

    <label>{{ l('Search guides', 'البحث في الأدلة') }}<input type="search" v-model="q"></label>
    <label>
      {{ l('Module filter', 'تصفية حسب الموديول') }}
      <select v-model="filter">
        <option value="">{{ l('All modules', 'كل الموديولات') }}</option>
        <option v-for="[k, n] in modules" :key="k" :value="k">{{ n[locale] }}</option>
      </select>
    </label>

    <p v-if="error">{{ error }}</p>
    <p v-if="!found.length">{{ l('No matching guides', 'لا توجد أدلة مطابقة') }}</p>

    <details v-for="g in found" :key="g.module + g.id" class="panel">
      <summary>{{ l(g.title_en, g.title_ar) }}</summary>
      <p>{{ l(g.prerequisites_en || '', g.prerequisites_ar || '') }}</p>
      <p>{{ l('Interactive progress checklist — check off steps as you follow them.', 'قائمة متابعة تفاعلية — ضع علامة على كل خطوة تنفذها.') }}</p>
      <p aria-live="polite">{{ fmtNum(g.steps.filter(s => checked(g, s)).length) }} / {{ fmtNum(g.steps.length) }} {{ l('completed', 'مكتملة') }}</p>
      <button @click="update(g)">{{ l('Reset this guide', 'مسح تقدم هذا الدليل') }}</button>
      <ol>
        <li v-for="s in g.steps" :key="s.id">
          <label class="sop-step">
            <input type="checkbox" :checked="checked(g, s)" @change="update(g, s)">
            {{ l(s.text_en, s.text_ar) }}
          </label>
          <button
            v-if="s.screenshots?.[locale]"
            type="button"
            class="sop-thumb"
            :aria-label="l('Zoom screenshot', 'تكبير لقطة الشاشة')"
            @click="openLightbox(`${g.module}/${g.id}/${s.id}`)"
          >
            <img :src="s.screenshots[locale]" :alt="l(s.text_en, s.text_ar)" loading="lazy">
            <span class="sop-thumb-badge">{{ l('Click to zoom', 'اضغط للتكبير') }}</span>
          </button>
        </li>
      </ol>
      <RouterLink v-if="g.route" :to="g.route">{{ g.module_name[locale] }}</RouterLink>
    </details>

    <Teleport to="body">
      <div
        v-if="currentSlide"
        class="sop-lightbox"
        role="dialog"
        aria-modal="true"
        :dir="locale === 'ar' ? 'rtl' : 'ltr'"
        :aria-label="l(currentSlide.guideTitleEn, currentSlide.guideTitleAr)"
        @click.self="closeLightbox"
      >
        <header class="sop-lb-bar">
          <div class="sop-lb-title">
            <strong>{{ l(currentSlide.moduleNameEn, currentSlide.moduleNameAr) }}</strong>
            <span> — {{ l(currentSlide.guideTitleEn, currentSlide.guideTitleAr) }}</span>
            <span v-if="slides.length > 1" class="sop-lb-count">({{ fmtNum((activeIndex ?? 0) + 1) }} / {{ fmtNum(slides.length) }})</span>
          </div>
          <div class="sop-lb-controls">
            <RouterLink
              v-if="currentSlide.route"
              :to="currentSlide.route"
              class="sop-lb-link"
              @click="closeLightbox"
            >{{ l('Open module', 'فتح الموديول') }}</RouterLink>
            <button type="button" :disabled="zoom <= 0.5" :title="l('Zoom out (-)', 'تصغير (-)')" @click="setZoom(zoom - 0.25)">−</button>
            <span class="sop-lb-zoom">{{ locale === 'ar' ? Math.round(zoom * 100).toLocaleString('ar-EG') + '٪' : Math.round(zoom * 100) + '%' }}</span>
            <button type="button" :disabled="zoom >= 4" :title="l('Zoom in (+)', 'تكبير (+)')" @click="setZoom(zoom + 0.25)">+</button>
            <button type="button" :disabled="zoom === 1 && pan.x === 0 && pan.y === 0" :title="l('Reset (0)', 'إعادة ضبط (0)')" @click="resetView">{{ l('Reset', 'إعادة ضبط') }}</button>
            <button type="button" class="sop-lb-close" :aria-label="l('Close (Esc)', 'إغلاق (Esc)')" @click="closeLightbox">×</button>
          </div>
        </header>

        <div
          ref="stageEl"
          class="sop-lb-stage"
          :class="{ 'is-zoomed': zoom > 1, 'is-dragging': dragging }"
          @wheel.prevent="onWheel"
          @dblclick="toggleDoubleZoom"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
        >
          <button
            v-if="slides.length > 1"
            type="button"
            class="sop-lb-nav prev"
            :aria-label="l('Previous', 'السابق')"
            @click.stop="stepSlide(-1)"
          >{{ locale === 'ar' ? '›' : '‹' }}</button>

          <img
            :key="currentSlide.key"
            :src="currentSlide.src"
            :alt="l(currentSlide.textEn, currentSlide.textAr)"
            class="sop-lb-img"
            draggable="false"
            :style="{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }"
          >

          <button
            v-if="slides.length > 1"
            type="button"
            class="sop-lb-nav next"
            :aria-label="l('Next', 'التالي')"
            @click.stop="stepSlide(1)"
          >{{ locale === 'ar' ? '‹' : '›' }}</button>
        </div>

        <footer class="sop-lb-caption">
          <p>{{ l(currentSlide.textEn, currentSlide.textAr) }}</p>
          <small>{{ l('Scroll or double-click to zoom • Drag to pan • Arrow keys to switch • Esc to close', 'استخدم عجلة الماوس أو الضغط المزدوج للتكبير • اسحب للتحريك • الأسهم للتنقل • Esc للإغلاق') }}</small>
        </footer>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.sop-thumb {
  position: relative;
  display: block;
  width: 100%;
  padding: 0;
  margin-top: 0.5rem;
  border: 1px solid rgba(128, 128, 128, 0.35);
  border-radius: 8px;
  overflow: hidden;
  background: #0f172a;
  cursor: zoom-in;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.sop-thumb:hover {
  border-color: #3b82f6;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.18);
}
.sop-thumb img {
  display: block;
  width: 100%;
  height: auto;
}
.sop-thumb-badge {
  position: absolute;
  bottom: 8px;
  inset-inline-end: 8px;
  padding: 3px 9px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.85);
  color: #f8fafc;
  font-size: 0.75rem;
  pointer-events: none;
}
.sop-lightbox {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  background: rgba(10, 15, 28, 0.94);
  color: #f8fafc;
}
.sop-lb-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.65rem 1rem;
  background: rgba(15, 23, 42, 0.95);
  border-bottom: 1px solid rgba(148, 163, 184, 0.2);
}
.sop-lb-title {
  font-size: 0.95rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sop-lb-count {
  opacity: 0.75;
  margin-inline-start: 0.4rem;
}
.sop-lb-controls {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-shrink: 0;
}
.sop-lb-controls button,
.sop-lb-link {
  display: inline-flex;
  align-items: center;
  padding: 0.3rem 0.65rem;
  min-height: 31px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 6px;
  background: #1e293b;
  color: #f8fafc;
  text-decoration: none;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 600;
}
.sop-lb-controls button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.sop-lb-controls button:hover:not(:disabled),
.sop-lb-link:hover {
  background: #334155;
}
.sop-lb-close {
  font-size: 1.15rem !important;
  font-weight: bold;
  margin-inline-start: 0.35rem;
}
.sop-lb-zoom {
  min-width: 3.2rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
  font-size: 0.85rem;
}
.sop-lb-stage {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  user-select: none;
  touch-action: none;
  cursor: zoom-in;
}
.sop-lb-stage.is-zoomed {
  cursor: grab;
}
.sop-lb-stage.is-dragging {
  cursor: grabbing;
}
.sop-lb-img {
  max-width: 92vw;
  max-height: calc(100vh - 135px);
  border-radius: 6px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.55);
  transform-origin: center center;
  transition: transform 0.08s ease-out;
}
.sop-lb-stage.is-dragging .sop-lb-img {
  transition: none;
}
.sop-lb-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  z-index: 2;
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(15, 23, 42, 0.85);
  color: #f8fafc;
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
}
.sop-lb-nav:hover {
  background: #334155;
}
.sop-lb-nav.prev {
  inset-inline-start: 1rem;
}
.sop-lb-nav.next {
  inset-inline-end: 1rem;
}
.sop-lb-caption {
  padding: 0.65rem 1rem;
  text-align: center;
  background: rgba(15, 23, 42, 0.95);
  border-top: 1px solid rgba(148, 163, 184, 0.2);
}
.sop-lb-caption p {
  margin: 0 0 0.2rem;
  font-size: 0.92rem;
}
.sop-lb-caption small {
  opacity: 0.7;
  font-size: 0.78rem;
}
</style>
