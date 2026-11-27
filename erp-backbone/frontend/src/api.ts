import { notifyApiMutation } from './useUnifiedNotice'
import { captureFormRequest } from './formValidation'

export type Language = string
export type Theme = 'system' | 'light' | 'dark'
export type Entry = { id: number; name: string }
export type SiteEntry = Entry & { active: boolean }
export type Scope = number | null

export interface Module {
  category?: string
  permissions?: string[]
  id: string
  group?: string
  version: string
  compatible?: boolean
  compatibility_issue?: string
  core_version?: string | null
  platform_contract?: string
  missing_dependencies?: string[]
  enabled: boolean
  visible: boolean
  show_in_navigation: boolean
  navigation_allowed: boolean
  ui_entries: string[]
  required_dependencies: string[]
  optional_dependencies: string[]
  name: Record<string, string>
  description: Record<string, string>
}

export type ModuleView = 'list' | 'grid' | 'card'
export interface UserProfile {
  notice_mode_override?: 'system' | 'popup' | 'toast'
  username: string
  display_name: string
  first_name: string
  middle_name: string
  last_name: string
  employee_number: string | null
  employee_id: number | null
  picture_url: string | null
}

export type CompanyBrand = {name:string;tax_registration_number?:string;commercial_registration?:string;default_currency?:string;address?:string;logo_url:string|null;deletion_policy?:'soft'|'permanent';revision:number}

export interface Workspace {
  username: string
  display_name: string
  module_view: ModuleView
  picture_url: string | null
  company: CompanyBrand
  hidden_screens: string[]
  admin: boolean
  platform_version: string
  mode: 'development' | 'production'
  language: Language
  theme: Theme
  modules: Module[]
  sites: SiteEntry[]
  permissions: Record<string, Scope[]>
}

export interface Asset extends Entry {
  tag: string
  site_id: Scope
}

export interface ApprovalRun {
  version: number
  outcome: 'pending' | 'approved' | 'returned'
  submitted_by: string
  submitted_at: string
  steps: string[]
  next_step: number
  decisions: { step: number; actor: string; action: 'approve' | 'return'; reason: string; decided_at: string; delegation_id?: number | null }[]
}

export interface WorkOrder {
  id: number
  title: string
  description: string
  site_id: Scope
  asset_reference: number | null
  asset_snapshot: string
  revision: number
  status: 'draft' | 'submitted' | 'in_review' | 'approved'
  can_review?: boolean
  maintenance_stage?: string
  review_routing?: {role_id: number; role_name: string; eligible_reviewers: string[]}
  approval_runs?: ApprovalRun[]
}

export class ApiError extends Error {
  fieldsShown = false
  constructor(public code: string, public status: number,
    public fieldErrors: Record<string, string[]> = {}, public fieldErrorCodes: Record<string, string[]> = {}) {
    super(code)
  }
}

export async function api<T>(url: string, method = 'GET', data?: object, key?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (method !== 'GET') {
    const cookie = document.cookie.split('; ').find(value => value.startsWith('csrftoken='))
    if (cookie) headers['X-CSRFToken'] = decodeURIComponent(cookie.slice('csrftoken='.length))
  }
  const payload = data instanceof FormData ? data : data ? JSON.stringify(data) : undefined
  if (typeof payload === 'string') headers['Content-Type'] = 'application/json'
  if (key) headers['Idempotency-Key'] = key
  const formRequest = method === 'GET' ? undefined : captureFormRequest()
  const response = await fetch('/api' + url, { method, headers, body: payload })
  const result = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401 && !url.startsWith('/login')) window.dispatchEvent(new Event('erp:session-expired'))
    const failure = new ApiError(result.error ?? 'request_failed', response.status, result.field_errors, result.field_error_codes)
    if (Object.keys(failure.fieldErrors).length) formRequest?.(failure)
    throw failure
  }
  if (method !== 'GET') {
    notifyApiMutation(url, method, data)
    if (!/^\/(login|logout|preferences|profile|saved-views)(\/|$)/.test(url)) {
      window.dispatchEvent(new Event('erp:work-changed'))
    }
  }
  return result as T
}

export function allows(workspace: Workspace, permission: string, site?: Scope): boolean {
  const values = workspace.permissions[permission] ?? []
  return site === undefined ? values.length > 0 : values.includes(null) || values.includes(site)
}

export function requestKey(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(18)), value => value.toString(16).padStart(2, '0')).join('')
}

export function createRequest() {
  let previous: string | undefined
  let key: string | undefined
  return <T>(url: string, payload: object) => {
    const current = JSON.stringify([url,payload])
    if (current !== previous) {
      previous = current
      key = requestKey()
    }
    return api<T>(url, 'POST', payload, key)
  }
}
