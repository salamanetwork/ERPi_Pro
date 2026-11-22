import { api, ApiError, requestKey } from './api'

export type Span = { id: number; started_at: string; ended_at: string | null }
export type Timer = Span & { order_id: number }
export type AttendanceStatus = {
  site_id: number; timezone: string; session: Span | null; break: Span | null; timer: Timer | null
}

export function duration(seconds: number) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor(seconds % 3600 / 60)
  return hours + ':' + String(minutes).padStart(2, '0')
}

export function timestamp(value: string, timezone: string, locale: string) {
  return new Intl.DateTimeFormat(locale, { timeZone: timezone, dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
}

export function attendanceAction() {
  let pending: { fingerprint: string; key: string } | undefined
  return async <T>(url: string, payload: object) => {
    const fingerprint = JSON.stringify([url, payload])
    if (pending?.fingerprint !== fingerprint) pending = {fingerprint, key: requestKey()}
    try {
      const result = await api<T>(url, 'POST', payload, pending.key)
      pending = undefined
      return result
    } catch (error) {
      if (error instanceof ApiError && error.status < 500) pending = undefined
      throw error
    }
  }
}
