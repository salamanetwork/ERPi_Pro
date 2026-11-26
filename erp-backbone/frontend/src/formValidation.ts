import type { Directive } from 'vue'
import { t } from './i18n'
import { notify } from './useUnifiedNotice'
import validationMessages from './validationMessages.json'

type Input = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement
type FieldErrors = Record<string, string[]>
export type FormFailure = {
  fieldErrors: FieldErrors
  fieldErrorCodes: FieldErrors
  fieldsShown?: boolean
}

type InlineError = { message: HTMLElement; previousDescription: string | null }
type FormState = { errors: Map<Input, InlineError>; attempt: number; banner?: HTMLElement }
let currentRequest: ((failure: FormFailure) => void) | undefined
let errorId = 0

export function captureFormRequest() { return currentRequest }

const selector = 'input, select, textarea, button[data-validation-control]'

function controls(form: HTMLFormElement, includeDisabled = false) {
  return Array.from(form.querySelectorAll<Input>(selector)).filter(el =>
    el.form === form && (includeDisabled || !el.disabled) && !el.matches('[readonly], [type=hidden], input[type=button]'))
}

function fieldName(el: Input) {
  if (el.type === 'search' && !el.dataset.field) return ''
  return el.dataset.field || el.closest<HTMLElement>('[data-field]')?.dataset.field || ''
}

function fieldLabel(el: Input) {
  const label = el.labels?.[0]?.cloneNode(true) as HTMLElement | undefined
  if (label) {
    label.querySelectorAll('input, select, textarea, button, .field-hint').forEach(node => node.remove())
    if (label.textContent?.trim()) return label.textContent.trim()
  }
  const reference = el.getAttribute('aria-labelledby')?.split(' ').map(id => document.getElementById(id)?.textContent?.trim()).filter(Boolean).join(' ')
  return reference || el.getAttribute('aria-label') || ''
}

function withLabel(key: string, el: Input) {
  const label = fieldLabel(el)
  return t(label ? key : 'This field is required.').replace('{field}', label)
}

function translated(code: string, el: Input, fallback?: string) {
  if (code === 'required') return withLabel('{field} is required.', el)
  const key = (validationMessages as Record<string, string>)[code]
  return key ? t(key) : fallback || t('Enter a valid value.')
}

function localError(el: Input) {
  if (el instanceof HTMLButtonElement) return undefined
  const value = el.validity
  if (value.badInput) {
    return translated(el.type.startsWith('date') || ['time', 'month'].includes(el.type) ? 'invalid_date' : 'invalid_number', el)
  }
  if (value.valueMissing || el.required && (el.matches('textarea, input:not([type=checkbox]):not([type=radio])') && !el.value.trim() ||
      el instanceof HTMLSelectElement && el.selectedOptions[0]?.disabled)) return translated('required', el)
  if (value.patternMismatch) return translated(el.getAttribute('inputmode') === 'decimal' ? 'invalid_number' : 'invalid', el)
  if (value.rangeUnderflow || value.rangeOverflow) {
    const bound = el.getAttribute(value.rangeUnderflow ? 'min' : 'max') ?? ''
    return t(value.rangeUnderflow ? 'Enter a value on or after {limit}.' : 'Enter a value on or before {limit}.').replace('{limit}', bound)
  }
  if (value.tooLong) return translated('max_length', el)
  if (value.stepMismatch) return translated('invalid_number', el)
  if (value.typeMismatch && el.type === 'email') return t('Enter a valid email address.')
  return value.valid ? undefined : translated('invalid', el)
}

function invalidControl(el: Input, state: FormState, text: string[]) {
  const message = document.createElement('small')
  message.className = 'validation-error'
  message.id = 'form-validation-error-' + ++errorId
  message.textContent = text.join(' ')
  const previousDescription = el.getAttribute('aria-describedby')
  state.errors.set(el, {message, previousDescription})
  el.setAttribute('aria-invalid', 'true')
  el.setAttribute('aria-describedby', [previousDescription, message.id].filter(Boolean).join(' '))
  const anchor = el.closest('label') ?? el.closest('.date-controls') ?? el
  // Keep the error immediately after its control and within its group.
  if (el.closest('form.shared-form') && el.closest('label')) el.after(message)
  else anchor.after(message)
}

function clearControl(el: Input, state: FormState) {
  const error = state.errors.get(el)
  if (!error) return
  if (error.previousDescription === null) el.removeAttribute('aria-describedby')
  else el.setAttribute('aria-describedby', error.previousDescription)
  el.removeAttribute('aria-invalid')
  error.message.remove()
  state.errors.delete(el)
}

function banner(form: HTMLFormElement, state: FormState, hasUnmapped = false) {
  state.banner?.remove()
  state.banner = undefined
  if (!state.errors.size && !hasUnmapped) return
  const summaryKey = hasUnmapped ? 'Failed to save. Review the form values.' : 'Failed to save. Check the highlighted fields.'
  const node = document.createElement('p')
  node.className = 'alert validation-summary'
  node.setAttribute('role', 'alert')
  node.textContent = t(summaryKey)
  form.prepend(node)
  state.banner = node
  const details = Array.from(state.errors.values())
    .map(err => err.message.textContent?.trim())
    .filter(Boolean)
    .slice(0, 3)
    .join(' · ')
  notify('error', 'Validation Error', summaryKey, {
    rawCode: 'validation_error',
    hintKey: 'Check highlighted fields and ensure all required values are valid.',
    suffix: details || undefined,
    dedup: true,
  })
}

function reveal(el: Input) {
  for (let parent = el.parentElement; parent; parent = parent.parentElement) {
    if (parent instanceof HTMLDetailsElement) parent.open = true
  }
  requestAnimationFrame(() => {
    if (!el.isConnected || el.getAttribute('aria-invalid') !== 'true') return
    el.focus({preventScroll: true})
    // Grid reserves the footer and sidebar. Center in each scroll container
    // so sticky headers or an open sidebar cannot cover the target.
    el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'})
    const scroller = el.closest('.main-shell')
    const header = scroller?.querySelector('.topbar')
    if (!scroller || !header) return
    const headerBottom = header.getBoundingClientRect().bottom
    const box = scroller.getBoundingClientRect()
    const top = el.getBoundingClientRect().top
    if (headerBottom > box.top && headerBottom < box.bottom && top < headerBottom + 12) {
      scroller.scrollTop -= headerBottom + 12 - top
    }
  })
}

const aliases: Record<string, string[]> = {
  site: ['site_id'], user: ['user_id', 'username'], center: ['center_id', 'cost_center_id'],
  location: ['location_id'], supplier: ['supplier_id'], customer: ['customer_id'],
  item: ['item_id'], unit: ['unit_id', 'base_unit_id'], employee: ['employee_id'],
  order: ['order_id', 'purchase_order_id'], account: ['account_id'],
  department: ['department_ref_id'], grade: ['grade_ref_id'],
  posting_date: ['date'], date: ['posting_date', 'business_date'],
  purchase_line: ['line_id', 'purchase_line_id'], movement: ['movement_id'],
  supplier_code: ['code'], warehouse: ['warehouse_id'], order_line: ['order_line_id', 'line_id'],
}
function keys(field: string) {
  const index = field.lastIndexOf('.') + 1
  const prefix = field.slice(0, index)
  const name = field.slice(index)
  return [field, ...(aliases[name] ?? [name + '_id']).map(key => prefix + key)]
}

function signature(el: Input) {
  return el instanceof HTMLInputElement && ['radio', 'checkbox'].includes(el.type) ? String(el.checked) : el.value
}

const mountedForms = new WeakSet<HTMLFormElement>()

export function bindValidatedForm(form: HTMLFormElement) {
  if (mountedForms.has(form)) return
  mountedForms.add(form)
  {
    const state: FormState = { errors: new Map(), attempt: 0 }
    form.noValidate = true
    function modified(event: Event) {
      if (!(event.target instanceof HTMLElement)) return
      const el = event.target.closest<Input>(selector)
      const name = el ? fieldName(el) : event.target.closest<HTMLElement>('[data-field]')?.dataset.field
      for (const [invalid] of state.errors) {
        if (invalid === el || name && name === fieldName(invalid)) clearControl(invalid, state)
      }
      if (!state.errors.size) {
        state.banner?.remove()
        state.banner = undefined
      }
    }
    form.addEventListener('input', modified)
    form.addEventListener('change', modified)
    form.addEventListener('validation-edit', modified)
    form.addEventListener('submit', event => {
      const attempt = ++state.attempt
      for (const el of state.errors.keys()) clearControl(el, state)
      state.banner?.remove()
      state.banner = undefined
      const fields = controls(form)
      const submitter = event instanceof SubmitEvent ? event.submitter : null
      if (!(submitter instanceof HTMLButtonElement && submitter.formNoValidate)) {
        for (const el of fields) {
          const requiredForAction = el.dataset.requiredAction && el.dataset.requiredAction === submitter?.getAttribute('value')
          const error = requiredForAction && !el.value.trim() ? translated('required', el) : localError(el)
          if (error) invalidControl(el, state, [error])
        }
      }
      if (state.errors.size) {
        event.preventDefault()
        event.stopImmediatePropagation()
        banner(form, state)
        reveal(state.errors.keys().next().value!)
        return
      }
      const initial = new Map(fields.map(el => [el, signature(el)]))
      const captured = (failure: FormFailure) => {
        if (!form.isConnected || state.attempt !== attempt) return
        const active = controls(form, true)
        let unmatched = false
        for (const [name, messages] of Object.entries(failure.fieldErrors)) {
          const names = keys(name)
          const el = names.map(key => active.find(input => fieldName(input) === key)).find(Boolean)
          if (!el) { unmatched = true; continue }
          if (!initial.has(el) || initial.get(el) !== signature(el)) continue
          const codes = failure.fieldErrorCodes[name] ?? []
          clearControl(el, state)
          invalidControl(el, state, messages.map((message, i) => translated(codes[i] ?? '', el, message)))
        }
        const first = active.find(el => state.errors.has(el))
        if (first && !unmatched) {
          failure.fieldsShown = true
          banner(form, state)
        } else if (unmatched && first) {
          failure.fieldsShown = true
          banner(form, state, true)
        } else if (!unmatched) failure.fieldsShown = true
        if (first) reveal(first)
      }
      currentRequest = captured
      // Existing action handlers start their request during submit. api()
      // keeps this particular form and attempt until the response arrives.
      window.setTimeout(() => { if (currentRequest === captured) currentRequest = undefined }, 0)
    }, {capture: true})
  }
}

export const validatedForm: Directive<HTMLFormElement> = {
  mounted(form) {
    bindValidatedForm(form)
  },
}

export function installGlobalFormValidation() {
  if (typeof document === 'undefined') return
  const scan = (root: ParentNode) => {
    if (root instanceof HTMLFormElement) bindValidatedForm(root)
    root.querySelectorAll?.('form').forEach(f => bindValidatedForm(f as HTMLFormElement))
  }
  scan(document)
  const observer = new MutationObserver(mutations => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node instanceof HTMLElement) scan(node)
      }
    }
  })
  observer.observe(document.documentElement, { childList: true, subtree: true })
  document.addEventListener('submit', event => {
    if (event.target instanceof HTMLFormElement && !mountedForms.has(event.target)) {
      bindValidatedForm(event.target)
    }
  }, { capture: true })
}
