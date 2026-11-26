<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { allows, api, ApiError, createRequest, type Workspace } from './api'
import { message, t } from './i18n'
import DataTable from './DataTable.vue'
import CsvExport from './CsvExport.vue'
import { useDirectory, useDirtyForm } from './useDirectory'

type DirectoryRow = { id: number; revision: number; code: string; name: string; active: boolean;
  currency_default?: string; legal_name?: string; contact?: string; tax_id?: string; billing?: string;
  shipping?: string; terms?: number; base_unit_id?: number; unit?: string; barcode?: string; default_price?: string }
type Info = { currencies?: string[]; units?: {id:number;code:string;name:string}[];
  conversions?: {id:number;unit_id:number;unit:string;factor:string}[] }
const props = defineProps<{workspace: Workspace; kind: string}>()
const route = useRoute()
const router = useRouter()
const configs = {
  supplier: {path:'/suppliers', api:'/suppliers', title: t('Suppliers'), create:'New supplier', permission:'suppliers.manage', deletePermission:'suppliers.manage', export:'supplier'},
  customer: {path:'/sales/customers', api:'/sales/customers', title: t('Customers'), create:'New customer', permission:'sales.customer', deletePermission:'sales.customer', export:'customer'},
  item: {path:'/inventory/items', api:'/inventory/items', title: t('Inventory items'), create:'New item', permission:'inventory.setup', deletePermission:'inventory.setup', export:'item'},
  departments: {path:'/hr/setup/departments', api:'/hr/setup/departments', title: t('Departments'), create:'New department', permission:'hr.manage', deletePermission:'hr.manage', export:''},
  'job-titles': {path:'/hr/setup/job-titles', api:'/hr/setup/job-titles', title: t('Job titles'), create:'New job title', permission:'hr.manage', deletePermission:'hr.manage', export:''},
  grades: {path:'/hr/setup/grades', api:'/hr/setup/grades', title: t('Grades'), create:'New grade', permission:'hr.manage', deletePermission:'hr.manage', export:''},
}
const config = computed(() => configs[props.kind as keyof typeof configs] ?? configs.departments)
const allowed = computed(() => allows(props.workspace, config.value.permission, null))
const canDelete = computed(() => Boolean(config.value.deletePermission) && allows(props.workspace, config.value.deletePermission, null))
const formPage = computed(() => Boolean(route.params.entry))
const id = computed(() => Number(route.params.entry) || 0)
const list = useDirectory<DirectoryRow, Info>(() => config.value.api + (props.kind === 'supplier' ? '?directory=1' : ''),
  () => !formPage.value)
const {page, loading, error, filter, filters, change, filterBy} = list
const form = ref<DirectoryRow>()
const info = ref<Info>({})
const formError = ref('')
const busy = ref(false)
const codeError = ref('')
const autoCode = ref(false)
const suggestedCode = ref('')
let createReference = createRequest()
const automaticReference = computed(() => ['grades','job-titles'].includes(props.kind) && formPage.value && !id.value)
watch([autoCode, () => form.value?.name, automaticReference, () => props.kind], ([automatic, name, supported], _, cleanup) => {
  suggestedCode.value = ''
  if (!automatic || !supported || !name?.trim()) return
  let current = true
  const timer = window.setTimeout(async () => {
    try {
      const params = new URLSearchParams({name})
      const response = await api<{code:string}>(config.value.api + '/code-preview?' + params)
      if (current) suggestedCode.value = response.code
    } catch { /* The create request assigns the final code and reports validation failures. */ }
  }, 180)
  cleanup(() => { current = false; window.clearTimeout(timer) })
})
function setAutomatic(event: Event) {
  autoCode.value = (event.target as HTMLInputElement).checked
  if (!form.value) return
  codeError.value = ''
  form.value.code = autoCode.value ? '' : suggestedCode.value
}
const conversion = ref({unit_id:0,factor:''})
const { reset } = useDirtyForm(() => [form.value, autoCode.value])
const { reset: resetConversion } = useDirtyForm(() => conversion.value)
const back = computed(() => ({path:config.value.path,query:route.query}))
let serial = 0

async function loadForm() {
  const version = ++serial
  form.value = undefined
  formError.value = ''
  codeError.value = ''
  createReference = createRequest()
  autoCode.value = automaticReference.value
  conversion.value = {unit_id:0,factor:''}
  reset()
  resetConversion()
  if (!formPage.value) return
  try {
    let result: Info & {record?: DirectoryRow} = {}
    if (id.value) {
      const row = await api<DirectoryRow | (Info & {record: DirectoryRow})>(config.value.api + '/' + id.value)
      result = 'record' in row ? row : {record:row}
    } else if (props.kind === 'supplier' || props.kind === 'item') {
      result = await api<Info>(config.value.api + (props.kind === 'supplier' ? '?directory=1&' : '?') + 'page=1&page_size=1')
    }
    if (version !== serial) return
    info.value = result
    form.value = result.record ?? {id:0,revision:0,code:'',name:'',active:true,legal_name:'',contact:'',tax_id:'',
      billing:'',shipping:'',currency_default:result.currencies?.[0],terms:30,base_unit_id:0,barcode:'',default_price:'0.00'}
    reset()
  } catch (failure) { if (version === serial) formError.value = message(failure) }
}
watch([() => route.path, () => props.kind], loadForm, {immediate:true})

function setRecord(row: DirectoryRow, active = row.active) {
  const path = config.value.api + (row.id && props.kind !== 'customer' ? '/' + row.id : '')
  const payload = {...row, id:row.id || undefined, active}
  if (automaticReference.value) return createReference<DirectoryRow>(path, {...payload,auto_code:autoCode.value})
  return api<DirectoryRow>(path, row.id && props.kind !== 'customer' ? 'PATCH' : 'POST', payload)
}
async function save(row: DirectoryRow, active = row.active) {
  if (busy.value || !allowed.value) return
  busy.value = true
  formError.value = ''
  codeError.value = ''
  try {
    await setRecord(row, active)
    if (formPage.value) { reset(); if (props.kind !== 'item' || !id.value) await router.push(back.value) }
    else await list.load()
  } catch (failure) {
    if (automaticReference.value && failure instanceof ApiError &&
        ['invalid_code', 'hr_reference_code_exists', 'hr_reference_codes_exhausted'].includes(failure.code)) {
      codeError.value = message(failure)
    } else formError.value = message(failure)
  } finally { busy.value = false }
}
async function deleteRecord(row: DirectoryRow) {
  if (busy.value || !canDelete.value) return
  const label = `${row.code} · ${row.name}`
  const isPermanent = props.workspace.company?.deletion_policy === 'permanent'
  const promptText = isPermanent
    ? `${t('Permanently delete')} "${label}"? ${t('This action cannot be undone.')}`
    : `${t('Delete')} "${label}"? ${t('The record will be archived according to system deletion policy.')}`
  if (!window.confirm(promptText)) return
  busy.value = true
  formError.value = ''
  try {
    await api(config.value.api + '/' + row.id, 'DELETE')
    await list.load()
  } catch (failure) {
    formError.value = message(failure)
  } finally { busy.value = false }
}
async function saveConversion() {
  if (busy.value || !form.value) return
  busy.value = true
  try {
    await api('/inventory/setup/conversion','POST',{...conversion.value,item_id:id.value})
    const result = await api<Info>(config.value.api + '/' + id.value)
    info.value = result
    conversion.value = {unit_id:0,factor:''}
    resetConversion()
  } catch (failure) { formError.value = message(failure) }
  finally { busy.value = false }
}
</script>

<template>
  <nav v-if="kind in {departments:1,'job-titles':1,grades:1}" class="section-tabs" :aria-label="t('HR Setup')">
    <RouterLink v-for="key in (['departments','job-titles','grades'] as const)" :key="key" class="tab-link"
      :aria-current="kind === key ? 'page' : undefined" :to="configs[key].path">{{ t(configs[key].title) }}</RouterLink>
    <RouterLink v-if="workspace.admin" class="tab-link" to="/hr/setup/employee-numbering">{{ t('Employee numbering') }}</RouterLink>
  </nav>
  <div class="page-heading">
    <h1>{{ t(formPage ? id ? 'Edit record' : config.create : config.title) }}</h1>
    <RouterLink v-if="!formPage && allowed" class="button primary" :to="{path:config.path+'/new',query:route.query}">{{ t(config.create) }}</RouterLink>
  </div>
  <p v-if="formError" class="alert" role="alert">{{ t(formError) }}</p>
  <template v-if="!formPage">
    <div class="directory-filters">
      <label>{{ t('Search') }}<input type="search" :placeholder="t('Search by code or name')" maxlength="120" :value="filter('q')" @change="filterBy($event, 'q')" @keyup.enter="filterBy($event, 'q')"></label>
      <label>{{ t('Status') }}<select :value="filter('status')" @change="filterBy($event,'status')"><option value="">{{ t('All') }}</option>
        <option value="active">{{ t('Active') }}</option><option value="archived">{{ t('Archived') }}</option></select></label>
      <label v-if="kind === 'item'">{{ t('Base unit') }}<select :value="filter('unit')" @change="filterBy($event,'unit')"><option value="">{{ t('All') }}</option>
        <option v-for="unit in page?.units" :key="unit.id" :value="unit.id">{{ unit.code }} · {{ t(unit.name) }}</option></select></label>
      <CsvExport v-if="config.export" :workspace="workspace" :screen="config.export" :filters="filters" />
    </div>
    <DataTable :page="page" :headings="['Code','Name',...(kind === 'item' ? ['Barcode','Default price','Base unit'] : []),'Status','Actions']"
      :loading="loading" :error="error" @page="change('page',$event)" v-slot="{row}">
      <td><bdi>{{ row.code }}</bdi></td><td>{{ row.name }}</td><template v-if="kind === 'item'"><td><bdi>{{ row.barcode || '—' }}</bdi></td><td>{{ row.default_price ?? '0.00' }}</td><td>{{ row.unit }}</td></template>
      <td>{{ t(row.active ? 'Active' : 'Archived') }}</td>
      <td><div v-if="allowed" class="actions">
        <RouterLink class="button secondary" :to="{path:config.path+'/'+row.id+'/edit',query:route.query}">{{ t('Edit') }}</RouterLink>
        <button class="secondary" type="button" :disabled="busy" @click="save(row, !row.active)">{{ t(row.active ? 'Archive' : 'Reactivate') }}</button>
        <button v-if="canDelete" class="danger" type="button" :disabled="busy" @click="deleteRecord(row)">{{ t('Delete') }}</button>
      </div></td>
    </DataTable>
  </template>
  <template v-else>
    <RouterLink class="back-link" :to="back">‹ {{ t(config.title) }}</RouterLink>
    <form v-validated-form v-if="form && allowed" class="panel record-form aligned-form" @submit.prevent="save(form)">
      <div v-if="automaticReference" class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="form.name" maxlength="120" required dir="auto"></label><FieldHint :label="t('Name')" required /></div>
      <div v-if="automaticReference" class="field-wrap"><label class="check">
        <input type="checkbox" :checked="autoCode" @change="setAutomatic">{{ t('Auto-generate Code') }}
      </label><FieldHint :label="t('Auto-generate Code')" /></div>
      <div class="field-wrap">
        <label>{{ t('Code') }}
          <input data-field="code" :placeholder="t('e.g., CODE-001')" :value="automaticReference && autoCode ? suggestedCode : form.code"
            @input="form.code = ($event.target as HTMLInputElement).value; codeError = ''"
            :maxlength="kind === 'customer' ? 24 : kind === 'item' || kind === 'supplier' ? 32 : 40"
            :required="!(automaticReference && autoCode)" :readonly="!!id || (automaticReference && autoCode)"
            :aria-invalid="codeError ? 'true' : undefined" :aria-describedby="codeError ? 'directory-code-error' : undefined" dir="ltr">
        </label>
        <FieldHint :label="t('Code')" :required="!(automaticReference && autoCode)" :readonly="!!id || (automaticReference && autoCode)" />
        <small v-if="automaticReference && autoCode" class="muted">{{ t('The final code is assigned when you save.') }}</small>
        <p v-if="codeError" id="directory-code-error" class="alert" role="alert">{{ t(codeError) }}</p>
      </div>
      <div v-if="!automaticReference" class="field-wrap"><label>{{ t('Name') }}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="form.name" maxlength="120" required dir="auto"></label><FieldHint :label="t('Name')" required /></div>
      <template v-if="kind === 'supplier' || kind === 'customer'">
        <div class="field-wrap"><label>{{ t('Legal name') }}<input data-field="legal_name" :placeholder="t('Enter {field}').replace('{field}', t('Legal name'))" v-model="form.legal_name" :maxlength="kind === 'supplier' ? 160 : 120"></label><FieldHint :label="t('Legal name')" /></div>
        <div class="field-wrap"><label>{{ t('Contact') }}<input data-field="contact" :placeholder="t('Enter {field}').replace('{field}', t('Contact'))" v-model="form.contact" :maxlength="kind === 'supplier' ? 240 : 120"></label><FieldHint :label="t('Contact')" /></div>
        <div class="field-wrap"><label>{{ t('Tax ID') }}<input data-field="tax_id" :placeholder="t('Enter {field}').replace('{field}', t('Tax ID'))" v-model="form.tax_id" maxlength="60"></label><FieldHint :label="t('Tax ID')" /></div>
      </template>
      <div v-if="kind === 'supplier'" class="field-wrap"><label>{{ t('Default currency') }}<select data-field="currency_default" v-model="form.currency_default" required><option v-for="code in info.currencies" :key="code">{{ code }}</option></select></label><FieldHint :label="t('Default currency')" required /></div>
      <template v-if="kind === 'customer'">
        <div class="field-wrap"><label>{{ t('Billing address') }}<input data-field="billing" :placeholder="t('Enter {field}').replace('{field}', t('Billing address'))" v-model="form.billing" maxlength="240"></label><FieldHint :label="t('Billing address')" /></div>
        <div class="field-wrap"><label>{{ t('Shipping address') }}<input data-field="shipping" :placeholder="t('Enter {field}').replace('{field}', t('Shipping address'))" v-model="form.shipping" maxlength="240"></label><FieldHint :label="t('Shipping address')" /></div>
        <div class="field-wrap"><label>{{ t('Payment terms (days)') }}<input data-field="terms" :placeholder="t('Enter {field}').replace('{field}', t('Payment terms (days)'))" v-model.number="form.terms" type="number" min="0" max="365" required></label><FieldHint :label="t('Payment terms (days)')" required /></div>
      </template>
      <template v-if="kind === 'item'">
        <div class="field-wrap"><label>{{ t('Default price') }}<input data-field="default_price" v-model="form.default_price" inputmode="decimal" placeholder="0.00" dir="ltr"></label><FieldHint :label="t('Default price')" /></div>
        <div class="field-wrap"><label>{{ t('Barcode') }}<input data-field="barcode" v-model="form.barcode" maxlength="64" placeholder="e.g., 6291000001234" dir="ltr"></label><FieldHint :label="t('Barcode')" /></div>
      </template>
      <div v-if="kind === 'item'" class="field-wrap"><label>{{ t('Base unit') }}<select data-field="base_unit_id" v-model="form.base_unit_id" required :disabled="!!id"><option :value="0" disabled>{{ t('Select') }}</option>
        <option v-for="unit in info.units" :key="unit.id" :value="unit.id">{{ unit.code }} · {{ t(unit.name) }}</option></select></label><FieldHint :label="t('Base unit')" required :readonly="!!id" /></div>
      <div v-if="id || automaticReference || kind === 'supplier' || kind === 'customer'" class="field-wrap"><label class="check"><input data-field="active" v-model="form.active" type="checkbox">{{ t('Active') }}</label><FieldHint :label="t('Active')" /></div>
      <div class="actions form-actions"><button class="primary" :disabled="busy">{{ t('Save') }}</button>
        <RouterLink class="button secondary" :to="back">{{ t('Cancel') }}</RouterLink></div>
    </form>
    <form v-validated-form v-if="form && allowed && kind === 'item' && id" class="panel record-form aligned-form"
      @submit.prevent="saveConversion">
      <h2>{{ t('Unit conversions') }}</h2>
      <ul v-if="info.conversions?.length"><li v-for="row in info.conversions" :key="row.id">{{ row.unit }} · {{ row.factor }}</li></ul>
      <div class="field-wrap"><label>{{ t('From unit') }}<select data-field="unit_id" v-model="conversion.unit_id" required>
        <option :value="0" disabled>{{ t('Select') }}</option><option v-for="unit in info.units?.filter(unit => unit.id !== form?.base_unit_id)" :key="unit.id" :value="unit.id">{{ t(unit.name) }}</option>
      </select></label><FieldHint :label="t('From unit')" required /></div>
      <div class="field-wrap"><label>{{ t('Conversion factor') }}<input data-field="factor" :placeholder="t('e.g., 125.50')" v-model="conversion.factor" inputmode="decimal" required></label><FieldHint :label="t('Conversion factor')" required /></div>
      <div class="actions form-actions"><button class="primary" :disabled="busy">{{ t('Save conversion') }}</button></div>
    </form>
  </template>
</template>
