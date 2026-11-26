import { isRtl, locale, localizedPick, localizedText, t } from './i18n'
import content from './help.json'

type Topic = { title: string; purpose: string; fields: string; next: string }
const topics = content.topics as Record<string, Record<'en' | 'ar', Topic>>
const fields = content.fields as Record<string, Record<'en' | 'ar', string>>

const modules=Object.fromEntries(Object.values(import.meta.glob<{id:string;name:{en:string;ar:string}}>('../../*/manifest.json',{eager:true,import:'default'})).map(m=>[m.id,m]))
export function helpTopic(route: string, section: string) {
  const declared=modules[route]
  if(declared&&!topics[route]){
    const ar=isRtl.value,title=localizedText(declared.name, locale.value)
    return {title,purpose:ar?'ابحث في سجلات '+title+' وأدرها ضمن نطاق صلاحياتك.':'Find and manage '+title+' records within your assigned scope.',
            fields:ar?'اختر النطاق وأدخل اسم السجل. تشير النجمة إلى الحقول المطلوبة.':'Choose the scope and enter the record name. Required fields have an asterisk.',
            next:ar?'احفظ ثم ابحث عن السجل في الجدول.':'Save the record and find it in the directory.'}
  }
  return localizedPick(topics[route + '/' + section] ?? topics[route] ?? topics.notFound!, locale.value)!
}

export function fieldHelp(label: string) {
  const key = Object.keys(fields).find(key => t(key) === label || key === label)
  return key ? localizedText(fields[key], locale.value) : t('Enter or select the value indicated by this field.')
}


export function formHelpFields(route: string, path: string) {
  const common = ['Code', 'Name', 'Active']
  const fields: Record<string, string[]> = {
    suppliers: [...common, 'Legal name', 'Contact', 'Tax ID', 'Default currency'],
    customers: [...common, 'Legal name', 'Contact', 'Tax ID', 'Billing address', 'Shipping address', 'Payment terms (days)'],
    items: [...common, 'Base unit', ...(path.endsWith('/new') ? [] : ['From unit', 'Conversion factor'])],
    hrSetup: /\/(grades|job-titles)\/new$/.test(path) ? [...common, 'Auto-generate Code'] : common,
    employeeNumbering: ['Automatic numbering', 'Next employee number'],
    offboarding: ['Request type', 'Last employment day', 'Reason', 'Review note'],
    purchaseOrder: ['Supplier', 'Estimated unit price', 'Unit price', 'Confirmed delivery date', 'Reason'],
  }
  if (route === 'hr' && /^\/hr\/employees\/(new|\d+)$/.test(path))
    return ['Employee number','Name','Site','Department','Job title','Grade','Hire date','Linked user']
  if (['suppliers','customers','items','hrSetup'].includes(route) && !/\/(new|edit|\d+)$/.test(path))
    return []
  return fields[route] ?? []
}
