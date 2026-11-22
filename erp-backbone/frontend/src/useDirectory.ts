import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from './api'
import { message, t } from './i18n'

export type Page<T> = { count: number; next: string | null; previous: string | null; page: number; page_size: number; results: T[] }

export function useDirectory<T, Extra extends object = object>(
  endpoint: () => string, enabled: () => boolean = () => true, errorMessage = message,
) {
  const route = useRoute()
  const router = useRouter()
  const page = ref<Page<T> & Extra>()
  const loading = ref(false)
  const error = ref('')
  let serial = 0
  const filter = (key: string) => typeof route.query[key] === 'string' ? route.query[key] as string : ''
  const filters = computed(() => Object.fromEntries(Object.keys(route.query)
    .filter(key => !['page', 'page_size', 'fromInbox'].includes(key) && filter(key)).map(key => [key, filter(key)])))
  function change(key: string, value: string) {
    const query = { ...route.query, [key]: value || undefined }
    if (key !== 'page') delete query.page
    void router.replace({ query })
  }
  function filterBy(event: Event, key: string) {
    change(key, (event.target as HTMLInputElement).value.trim())
  }
  async function load() {
    const token = ++serial
    if (!enabled()) return
    loading.value = true
    error.value = ''
    try {
      const result = await api<Page<T> & Extra>(endpoint() + (endpoint().includes('?') ? '&' : '?') + new URLSearchParams({
        ...filters.value, page: filter('page') || '1', page_size: filter('page_size') || '25',
      }))
      if (!Array.isArray(result?.results) || !Number.isSafeInteger(result.count)) {
        throw new ApiError('invalid_directory_response', 502)
      }
      if (serial === token) page.value = result
    } catch (failure) {
      if (serial === token) {
        error.value = errorMessage(failure)
        page.value = undefined
      }
    } finally {
      if (serial === token) loading.value = false
    }
  }
  watch([() => route.fullPath, endpoint, enabled], load, { immediate: true })
  return { page, loading, error, filter, filters, change, filterBy, load }
}

export function useDirtyForm(fields: () => unknown) {
  const baseline = ref<string>()
  const dirty = computed(() => baseline.value !== undefined && JSON.stringify(fields()) !== baseline.value)
  const reset = () => { baseline.value = JSON.stringify(fields()) }
  const confirm = () => !dirty.value || window.confirm(t('Discard unsaved changes?'))
  const unload = (event: BeforeUnloadEvent) => {
    if (dirty.value) { event.preventDefault(); event.returnValue = '' }
  }
  const router = useRouter()
  let removeGuard: (() => void) | undefined
  onMounted(() => {
    removeGuard = router.beforeEach((to, from) => to.path === from.path || confirm())
    window.addEventListener('beforeunload', unload)
  })
  onUnmounted(() => {
    removeGuard?.()
    window.removeEventListener('beforeunload', unload)
  })
  return { dirty, reset }
}
