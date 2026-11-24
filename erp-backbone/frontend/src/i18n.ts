import { notifyApiError } from './useUnifiedNotice'
import { computed, ref } from 'vue'
import { ApiError, type Language } from './api'
import localeManifest from '../../locales.json'
import errorMessages from './errors.json'

export type LocaleMeta = {
  code: string
  name: string
  dir: 'ltr' | 'rtl'
  numberLocale: string
}

export const SUPPORTED_LOCALES: LocaleMeta[] = localeManifest.locales as LocaleMeta[]
export const DEFAULT_LOCALE: string = localeManifest.default || 'en'
export const FALLBACK_LOCALE: string = localeManifest.fallback || 'en'

const localeMap = new Map<string, LocaleMeta>(SUPPORTED_LOCALES.map(item => [item.code, item]))

const rawBundles = import.meta.glob('./locales/*.json', { eager: true }) as Record<string, { default: Record<string, string> }>
const messages: Record<string, Record<string, string>> = {}
for (const [path, mod] of Object.entries(rawBundles)) {
  const match = path.match(/\/([^/]+)\.json$/)
  if (match) messages[match[1]] = mod.default || {}
}

const errors: Record<string, string> = errorMessages

export const locale = ref<Language>(DEFAULT_LOCALE)

export const currentLocaleMeta = computed<LocaleMeta>(() =>
  localeMap.get(locale.value) ?? localeMap.get(FALLBACK_LOCALE) ?? { code: 'en', name: 'English', dir: 'ltr', numberLocale: 'en-US' }
)

export const isRtl = computed<boolean>(() => currentLocaleMeta.value.dir === 'rtl')

export function getLocaleDir(code?: string): 'ltr' | 'rtl' {
  return (code && localeMap.get(code)?.dir) || 'ltr'
}

export function localizedPick<T>(map: Record<string, T | undefined> | undefined | null, lang: string = locale.value): T | undefined {
  if (!map) return undefined
  return map[lang] ?? map[FALLBACK_LOCALE] ?? map.en ?? Object.values(map)[0]
}

export function localizedText(map: Record<string, string | undefined> | undefined | null, lang: string = locale.value): string {
  return localizedPick<string>(map, lang) || ''
}

export const t = (key: string): string => {
  const activeDict = messages[locale.value]
  if (activeDict && key in activeDict) return activeDict[key]
  const fallbackDict = messages[FALLBACK_LOCALE]
  if (fallbackDict && key in fallbackDict) return fallbackDict[key]
  return key
}

export function message(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.fieldsShown) {
      notifyApiError({ rawCode: error.code || 'validation_error', status: error.status, message: t('Failed to save. Review the form values.') }, 'Validation Error')
      return ''
    }
    let resolved = ''
    const activeDict = messages[locale.value] || messages[FALLBACK_LOCALE] || {}
    if ((error.code.startsWith('supply_') || error.code.startsWith('mfg_') || error.code.startsWith('project_')) && activeDict[error.code]) {
      resolved = t(error.code)
    } else {
      const raw = errors[error.code] ?? (Object.keys(error.fieldErrors).length
        ? 'Failed to save. Review the form values.'
        : 'The request could not be completed. Try again.')
      resolved = t(raw)
    }
    notifyApiError({ rawCode: error.code, status: error.status, message: resolved })
    return resolved
  }
  const fallback = t('The request could not be completed. Try again.')
  notifyApiError({ message: fallback })
  return fallback
}
