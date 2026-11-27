<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type CompanyBrand } from './api'
import { message, t } from './i18n'
import { useDirtyForm } from './useDirectory'

const emit = defineEmits<{saved:[brand:CompanyBrand]}>()
const brand = ref<CompanyBrand>()
const name = ref('')
const taxRegistrationNumber = ref('')
const commercialRegistration = ref('')
const defaultCurrency = ref('AED')
const address = ref('')
const deletionPolicy = ref<'soft' | 'permanent'>('soft')
const upload = ref<File>()
const preview = ref('')
const remove = ref(false)
const busy = ref(false)
const failure = ref('')
const notice = ref(false)
const fileInput = ref<HTMLInputElement>()
const {reset} = useDirtyForm(() => [
  name.value,
  taxRegistrationNumber.value,
  commercialRegistration.value,
  defaultCurrency.value,
  address.value,
  deletionPolicy.value,
  remove.value,
  preview.value,
])
const logo = computed(() => remove.value ? '' : preview.value || brand.value?.logo_url || '')
let mounted = true

function clearPreview() {
  if (preview.value) URL.revokeObjectURL(preview.value)
  preview.value = ''
  upload.value = undefined
}
function apply(result: CompanyBrand) {
  clearPreview()
  brand.value = result
  name.value = result.name
  taxRegistrationNumber.value = result.tax_registration_number ?? ''
  commercialRegistration.value = result.commercial_registration ?? ''
  defaultCurrency.value = result.default_currency || 'AED'
  address.value = result.address ?? ''
  deletionPolicy.value = result.deletion_policy === 'permanent' ? 'permanent' : 'soft'
  remove.value = false
  if (fileInput.value) fileInput.value.value = ''
  reset()
}
async function load() {
  busy.value = true
  failure.value = ''
  try {
    const result = await api<CompanyBrand>('/company-profile')
    if (mounted) apply(result)
  } catch (error) { if (mounted) failure.value = message(error) }
  finally { if (mounted) busy.value = false }
}
function pick(event: Event) {
  const control = event.currentTarget as HTMLInputElement
  const selected = control.files?.[0]
  if (!selected) return
  failure.value = ''
  notice.value = false
  if (selected.size > 2 * 1024 * 1024 || !/\.(png|jpe?g|svg)$/i.test(selected.name)) {
    failure.value = selected.size > 2 * 1024 * 1024
      ? 'The company logo must be 2 MB or smaller.' : 'Choose a valid PNG, JPG, or SVG logo.'
    control.value = ''
    return
  }
  clearPreview()
  remove.value = false
  upload.value = selected
  preview.value = URL.createObjectURL(selected)
}
function removeLogo() {
  clearPreview()
  remove.value = Boolean(brand.value?.logo_url)
  if (fileInput.value) fileInput.value.value = ''
  notice.value = false
  failure.value = ''
}
async function save() {
  if (!brand.value || busy.value) return
  busy.value = true
  notice.value = false
  failure.value = ''
  const currency = defaultCurrency.value.trim().toUpperCase() || 'AED'
  defaultCurrency.value = currency
  const fields = new FormData()
  fields.set('name', name.value)
  fields.set('tax_registration_number', taxRegistrationNumber.value)
  fields.set('commercial_registration', commercialRegistration.value)
  fields.set('default_currency', currency)
  fields.set('address', address.value)
  fields.set('deletion_policy', deletionPolicy.value)
  fields.set('revision', String(brand.value.revision))
  if (upload.value) fields.set('logo', upload.value)
  if (remove.value) fields.set('remove_logo', '1')
  try {
    const result = await api<CompanyBrand>('/company-profile','POST',fields)
    if (!mounted) return
    apply(result)
    emit('saved',result)
    notice.value = true
  } catch (error) { if (mounted) failure.value = message(error) }
  finally { if (mounted) busy.value = false }
}
onMounted(load)
onUnmounted(() => { mounted = false; clearPreview() })
</script>

<template>
  <RouterLink class="back-link" to="/settings">{{ t('Settings') }}</RouterLink>
  <h1>{{ t('Company Profile') }}</h1>
  <p v-if="failure" class="alert" role="alert">{{ t(failure) }}</p>
  <p v-if="notice" class="success" role="status">{{ t('Changes saved.') }}</p>
  <form v-validated-form v-if="brand" class="panel settings-form company-settings" @submit.prevent="save">
    <div class="field-wrap">
      <label>{{ t('Company name') }}<input data-field="name" v-model="name" maxlength="120" placeholder="ERPi Pro" dir="auto" :disabled="busy"></label>
      <FieldHint :label="t('Company name')" />
    </div>
    <div class="field-wrap">
      <label>{{ t('Tax registration number') }}<input data-field="tax_registration_number" v-model="taxRegistrationNumber" maxlength="64" dir="auto" :disabled="busy"></label>
      <FieldHint :label="t('Tax registration number')" />
    </div>
    <div class="field-wrap">
      <label>{{ t('Commercial registration') }}<input data-field="commercial_registration" v-model="commercialRegistration" maxlength="64" dir="auto" :disabled="busy"></label>
      <FieldHint :label="t('Commercial registration')" />
    </div>
    <div class="field-wrap">
      <label>{{ t('Default currency') }}<input data-field="default_currency" v-model.trim="defaultCurrency" maxlength="3" pattern="[A-Za-z]{3}" placeholder="AED" required :disabled="busy"></label>
      <FieldHint :label="t('Default currency')" required />
    </div>
    <div class="field-wrap">
      <label>{{ t('Address') }}<textarea data-field="address" v-model="address" maxlength="500" rows="3" dir="auto" :disabled="busy"></textarea></label>
      <FieldHint :label="t('Address')" />
    </div>
    <div class="field-wrap">
      <label>{{ t('Company logo') }}<input data-field="logo" ref="fileInput" type="file" accept=".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml"
        :disabled="busy" @change="pick"></label>
      <FieldHint :label="t('Company logo')" />
    </div>
    <div class="field-wrap">
      <label>{{ t('Record deletion policy') }}
        <select data-field="deletion_policy" v-model="deletionPolicy" :disabled="busy">
          <option value="soft">{{ t('Soft delete (archive records)') }}</option>
          <option value="permanent">{{ t('Permanent delete (hard delete)') }}</option>
        </select>
      </label>
      <small class="muted">{{ t('Controls whether deleting master records archives them or removes them permanently across the system.') }}</small>
    </div>
    <div class="company-preview">
      <strong>{{ t('Brand preview') }}</strong>
      <img v-if="logo" :src="logo" alt="" @error="failure = 'The logo preview could not be displayed.'">
      <span v-else class="brand-mark" aria-hidden="true">E</span>
      <bdi>{{ name.trim() || 'ERPi Pro' }}</bdi>
    </div>
    <div class="actions">
      <button class="primary" :disabled="busy">{{ t('Save') }}</button>
      <button v-if="logo" type="button" class="secondary" :disabled="busy" @click="removeLogo">{{ t('Remove logo') }}</button>
      <button v-if="remove" type="button" class="secondary" :disabled="busy" @click="remove = false">{{ t('Undo') }}</button>
      <RouterLink to="/settings/trash" class="button secondary">{{ t('Trash & Restore') }}</RouterLink>
    </div>
  </form>
  <button v-else-if="failure" type="button" class="secondary" :disabled="busy" @click="load">{{ t('Retry') }}</button>
</template>
