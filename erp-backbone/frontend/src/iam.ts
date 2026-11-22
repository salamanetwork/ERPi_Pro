import { api, ApiError, type Entry } from './api'
import { locale, t } from './i18n'

export interface Role extends Entry {
  permissions: string[]
  revision: number
  user_count: number
}
export interface Grant {
  id: number
  role_id: number
  role: string
  site_id: number | null
  site: string | null
}
export interface Person {
  id: number
  username: string
  display_name: string
  active: boolean
  admin: boolean
  revision: number
  assignments: Grant[]
}
export interface Assigned {
  id: number
  user_id: number
  role_id: number
  site_id: number | null
  user__username: string
  role__name: string
  site__name: string | null
}
export interface Catalog {
  users: { id: number; username: string }[]
  sites: Entry[]
  assignments: Assigned[]
  permission_metadata: Record<
    string,
    {
      label: { en: string; ar: string }
      description: { en: string; ar: string }
      type: string
    }
  >
  permission_catalog: string[]
}
export const catalog = () => api<Catalog>('/access')
export const roleList = async () =>
  (await api<{ results: Role[] }>('/admin/roles')).results
export const say = (en: string, ar: string) => (locale.value === 'ar' ? ar : en)
export function failure(e: unknown) {
  const code = e instanceof ApiError ? e.code : ''
  const errors: Record<string, string> = {
    self_deactivation: say(
      'You cannot deactivate your own account.',
      'لا يمكنك تعطيل حسابك الحالي.',
    ),
    revision_conflict: say(
      'Another change was saved. Reload before editing again.',
      'حُفظ تعديل آخر. أعد التحميل قبل التعديل.',
    ),
    duplicate_assignment: say(
      'This role is already assigned at this scope.',
      'الدور معين بالفعل في هذا النطاق.',
    ),
    mfa_role_name: say(
      'This role name is used by the MFA configuration.',
      'اسم هذا الدور مستخدم في إعدادات التحقق متعدد العوامل.',
    ),
    invalid_password: say(
      'Choose a longer, less predictable password.',
      'اختر كلمة مرور أطول ويصعب تخمينها.',
    ),
    duplicate_name: say('This name is already in use.', 'الاسم مستخدم بالفعل.'),
    invalid_account: say(
      'Check the username and password.',
      'راجع اسم المستخدم وكلمة المرور.',
    ),
    invalid_permissions: say(
      'Select at least one valid permission.',
      'اختر صلاحية صالحة واحدة على الأقل.',
    ),
    forbidden: say(
      'Administrator access required.',
      'هذه العملية تتطلب صلاحيات المسؤول.',
    ),
  }
  return (
    errors[code] ||
    say(
      'Could not save or load the requested data.',
      'تعذر حفظ البيانات أو تحميلها.',
    )
  )
}
export const allSites = () => t('All sites')
