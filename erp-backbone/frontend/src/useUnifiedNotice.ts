import { computed, ref } from 'vue'
import { t } from './i18n'
import { getEffectiveNoticeMode, setUserNoticeOverride } from './notificationPreferences'

export type NoticeType = 'error' | 'fault' | 'warning' | 'confirmation' | 'information' | 'saved'

export interface UnifiedNotice {
  id: number
  type: NoticeType
  badge: string
  title: string
  rawCode?: string
  explanation: string
  hint?: string
}

export const ERROR_CATALOG: Record<string, { titleKey: string; explanationKey: string; hintKey: string; type?: NoticeType }> = {
  invalid_quantity: {
    titleKey: 'Invalid Quantity',
    explanationKey: 'The submitted quantity is zero or invalid.',
    hintKey: 'Enter a non-zero number (positive to add, negative to deduct) and click Apply.',
  },
  insufficient_stock: {
    titleKey: 'Insufficient Stock',
    explanationKey: 'Requested quantity exceeds available stock at this location.',
    hintKey: 'Check available stock levels or receive inventory first.',
  },
  pos_terminal_location_required: {
    titleKey: 'Terminal Location Required',
    explanationKey: 'The selected POS terminal is not linked to a stock location.',
    hintKey: 'Link the terminal to a warehouse location in terminal settings.',
  },
  inventory_barcode_exists: {
    titleKey: 'Duplicate Barcode',
    explanationKey: 'This barcode is already assigned to another inventory item.',
    hintKey: 'Use a unique barcode or search for the existing item.',
  },
  permission_denied: {
    titleKey: 'Access Denied',
    explanationKey: 'You do not have permission to perform this action.',
    hintKey: 'Contact your system administrator to request the required role.',
  },
  validation_error: {
    titleKey: 'Validation Error',
    explanationKey: 'Failed to save. Review the form values.',
    hintKey: 'Check highlighted fields and ensure all required values are valid.',
  },
}

const badgeKeyMap: Record<NoticeType, string> = {
  error: 'ERROR',
  fault: 'FAULT',
  warning: 'WARNING',
  confirmation: 'CONFIRMED',
  information: 'INFO',
  saved: 'SAVED',
}

export const effectiveNoticeMode = computed(() => getEffectiveNoticeMode())
export const currentUnifiedNotice = ref<UnifiedNotice | null>(null)
export const latestInlineNotice = ref<UnifiedNotice | null>(null)
export const unifiedToastQueue = ref<UnifiedNotice[]>([])

let noticeSeq = 1
let lastDedupKey = ''
let lastDedupAt = 0
let lastAutoNoticeId = 0
let lastAutoNoticeAt = 0

export function togglePersonalNoticeMode(mode: 'popup' | 'toast') {
  setUserNoticeOverride(mode)
  if (mode === 'toast' && currentUnifiedNotice.value) {
    unifiedToastQueue.value.push(currentUnifiedNotice.value)
    const pushedId = currentUnifiedNotice.value.id
    currentUnifiedNotice.value = null
    setTimeout(() => removeUnifiedToast(pushedId), 6500)
  }
}

export function dismissUnifiedNotice() {
  currentUnifiedNotice.value = null
}

export function removeUnifiedToast(id: number) {
  unifiedToastQueue.value = unifiedToastQueue.value.filter((x) => x.id !== id)
}

export function notify(
  type: NoticeType,
  titleKey: string,
  explanationKey: string,
  options: { rawCode?: string; hintKey?: string; suffix?: string; dedup?: boolean } = {}
): UnifiedNotice {
  const raw = options.rawCode || ''
  let resolvedType: NoticeType = type
  let resolvedTitle = t(titleKey)
  let resolvedExplanation = t(explanationKey)
  let resolvedHint = options.hintKey ? t(options.hintKey) : undefined

  if ((type === 'error' || type === 'fault') && raw && ERROR_CATALOG[raw]) {
    const entryMeta = ERROR_CATALOG[raw]
    if (entryMeta.type) resolvedType = entryMeta.type
    resolvedTitle = t(entryMeta.titleKey)
    resolvedExplanation = t(entryMeta.explanationKey)
    resolvedHint = resolvedHint || t(entryMeta.hintKey)
  }


  const dedupKey = `${resolvedType}|${raw}|${resolvedTitle}|${resolvedExplanation}`
  const now = Date.now()
  if (options.dedup && dedupKey === lastDedupKey && now - lastDedupAt < 1200 && latestInlineNotice.value) {
    return latestInlineNotice.value
  }
  lastDedupKey = dedupKey
  lastDedupAt = now

  if (!options.suffix?.startsWith('__auto__')) {
    // Custom notice replaces any auto-generated mutation notice fired within the same action cycle
    if (lastAutoNoticeId && now - lastAutoNoticeAt < 400) {
      if (currentUnifiedNotice.value?.id === lastAutoNoticeId) currentUnifiedNotice.value = null
      unifiedToastQueue.value = unifiedToastQueue.value.filter((x) => x.id !== lastAutoNoticeId)
    }
  }
  const cleanSuffix = options.suffix?.startsWith('__auto__') ? undefined : options.suffix
  if (cleanSuffix) {
    resolvedExplanation = `${t(explanationKey)} (${cleanSuffix})`
  }
  const entry: UnifiedNotice = {
    id: noticeSeq++,
    type: resolvedType,
    badge: t(badgeKeyMap[resolvedType]),
    title: resolvedTitle,
    rawCode: raw || undefined,
    explanation: resolvedExplanation,
    hint: resolvedHint,
  }

  latestInlineNotice.value = entry

  if (effectiveNoticeMode.value === 'popup') {
    currentUnifiedNotice.value = entry
  } else {
    unifiedToastQueue.value.push(entry)
    setTimeout(() => removeUnifiedToast(entry.id), 6500)
  }

  return entry
}

export function notifyApiError(err: any, fallbackTitleKey = 'Operation Failed'): UnifiedNotice {
  const rawCode = String(err?.rawCode || err?.code || err?.error || '')
  const status = Number(err?.status || 0)
  const isFault = status >= 500 || rawCode.startsWith('HTTP_5')
  const msg =
    typeof err?.message === 'string' && err.message && err.message !== rawCode
      ? err.message
      : 'The request could not be completed. Try again.'
  return notify(isFault ? 'fault' : 'error', fallbackTitleKey, msg, {
    rawCode: rawCode || (status ? `HTTP_${status}` : undefined),
    dedup: true,
  })
}


export function notifyApiMutation(url: string, method: string, data?: unknown): void {
  const m = method.toUpperCase()
  if (m === 'GET') return
  // Skip auth/session/view-only or read-query endpoints
  if (/^\/(login|logout|preferences|saved-views|search|reports)(\/|$)/.test(url)) return
  const cleanUrl = url.split('?')[0].toLowerCase()
  const actionObj = data && typeof data === 'object' && 'action' in data ? String((data as Record<string, unknown>).action || '').toLowerCase() : ''
  const target = `${cleanUrl} ${actionObj}`

  let type: NoticeType = 'saved'
  let titleKey = 'Changes Saved'
  let explanationKey = 'Your updates have been saved successfully.'

  if (m === 'DELETE') {
    type = 'warning'
    titleKey = 'Record Deleted'
    explanationKey = 'The selected record has been removed.'
  } else if (/\/(void|cancel|reject|return|reverse|revoke|archive|deactivate|disable|scrap|stop)(\/|$|\s)/.test(target)) {
    type = 'warning'
    titleKey = 'Action Completed'
    explanationKey = 'The status update or reversal has been applied.'
  } else if (/\/(submit|approve|confirm|post|issue|release|complete|close|convert|publish|activate|enable|start|checkout|pay|receive|transfer)(\/|$|\s)/.test(target)) {
    type = 'confirmation'
    titleKey = 'Action Confirmed'
    explanationKey = 'The operation has been confirmed and applied.'
  } else if (m === 'POST' && !/\/(edit|update|save|settings|profile)(\/|$)/.test(cleanUrl)) {
    type = 'saved'
    titleKey = 'Created Successfully'
    explanationKey = 'The new record has been created and saved.'
  }

  const entry = notify(type, titleKey, explanationKey, { suffix: '__auto__', dedup: true })
  lastAutoNoticeId = entry.id
  lastAutoNoticeAt = Date.now()
}
