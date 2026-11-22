import { ref } from 'vue'
import { ApiError, type Language } from './api'

import arabic from './ar.json'
import english from './en.json'
import errorMessages from './errors.json'

const messages: Record<Language, Record<string, string>> = {ar: arabic, en: english}
const errors: Record<string, string> = errorMessages

export const locale = ref<Language>('en')
export const t = (key: string) => messages[locale.value][key] ?? key
export function message(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.fieldsShown) return ''
    if (((error.code.startsWith('supply_') || error.code.startsWith('mfg_') || error.code.startsWith('project_')) || error.code.startsWith('mfg_') || error.code.startsWith('project_')) && messages[locale.value][error.code]) return t(error.code)
    return errors[error.code] ?? (Object.keys(error.fieldErrors).length
      ? 'Failed to save. Review the form values.'
      : 'The request could not be completed. Try again.')
  }
  return 'The request could not be completed. Try again.'
}
