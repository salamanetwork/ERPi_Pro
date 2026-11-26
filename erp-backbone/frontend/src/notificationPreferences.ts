import { ref } from 'vue'
import { api } from './api'

export type NoticeDisplayMode = 'popup' | 'toast'
export type UserNoticeOverrideMode = 'system' | 'popup' | 'toast'

const SYSTEM_MODE_KEY = 'erpi.system.notice_mode'
const USER_OVERRIDE_PREFIX = 'erpi.user.notice_override.'

function loadSystemMode(): NoticeDisplayMode {
  try {
    const val = localStorage.getItem(SYSTEM_MODE_KEY)
    return val === 'toast' ? 'toast' : 'popup'
  } catch {
    return 'popup'
  }
}

export const systemNoticeMode = ref<NoticeDisplayMode>(loadSystemMode())
export const activeNoticeUsername = ref<string>('default')
export const userNoticeOverride = ref<UserNoticeOverrideMode>('system')

function loadUserOverride(username: string): UserNoticeOverrideMode {
  try {
    const val = localStorage.getItem(USER_OVERRIDE_PREFIX + (username || 'default'))
    if (val === 'popup' || val === 'toast' || val === 'system') return val
    return 'system'
  } catch {
    return 'system'
  }
}

export function syncNoticeUser(
  username?: string | null,
  serverState?: { system_notice_mode?: NoticeDisplayMode; notice_mode_override?: UserNoticeOverrideMode }
) {
  const user = (username || '').trim() || 'default'
  activeNoticeUsername.value = user
  if (serverState?.system_notice_mode === 'popup' || serverState?.system_notice_mode === 'toast') {
    systemNoticeMode.value = serverState.system_notice_mode
    try { localStorage.setItem(SYSTEM_MODE_KEY, serverState.system_notice_mode) } catch {}
  }
  if (
    serverState?.notice_mode_override === 'system' ||
    serverState?.notice_mode_override === 'popup' ||
    serverState?.notice_mode_override === 'toast'
  ) {
    userNoticeOverride.value = serverState.notice_mode_override
    try { localStorage.setItem(USER_OVERRIDE_PREFIX + user, serverState.notice_mode_override) } catch {}
  } else {
    userNoticeOverride.value = loadUserOverride(user)
  }
}

export function setSystemNoticeMode(mode: NoticeDisplayMode, persist = true) {
  systemNoticeMode.value = mode
  try {
    localStorage.setItem(SYSTEM_MODE_KEY, mode)
  } catch {}
  if (persist) {
    void api('/preferences', 'PATCH', { system_notice_mode: mode }).catch(() => {})
  }
}

export function setUserNoticeOverride(mode: UserNoticeOverrideMode, persist = true) {
  userNoticeOverride.value = mode
  try {
    localStorage.setItem(USER_OVERRIDE_PREFIX + activeNoticeUsername.value, mode)
  } catch {}
  if (persist) {
    void api('/preferences', 'PATCH', { notice_mode_override: mode }).catch(() => {})
  }
}

export function getEffectiveNoticeMode(): NoticeDisplayMode {
  if (userNoticeOverride.value === 'popup' || userNoticeOverride.value === 'toast') {
    return userNoticeOverride.value
  }
  return systemNoticeMode.value
}
