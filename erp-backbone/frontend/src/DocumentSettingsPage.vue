<script setup lang="ts">
import { ref, watch } from 'vue'
import { api, type Workspace } from './api'
import { message, t } from './i18n'

type Rule = {kind: string; allow_site_numbering: boolean; prefix: string; per_site: boolean; start: number; revision: number; locked: boolean}
type Layout = {heading: string; company_name: string; footer: string; logo: string; revision: number}
const {workspace}=defineProps<{workspace:Workspace}>()
const tab=ref<'number'|'print'>('number')
const site=ref<number>()
const numberPreview=ref('')
const printPreview=ref('')
const numbering = ref<Rule>()
const layout = ref<Layout>()
const language = ref('en')
const documentKind = ref('pr')
watch(tab, () => { if (tab.value === 'print' && documentKind.value === 'si') documentKind.value = 'pr' })
const error = ref('')
const notice = ref('')
const busy = ref(false)
let logoSequence = 0

watch([documentKind, language], async ([kind, code]) => {
  const request = ++logoSequence
  if (kind === "si") return
  layout.value = undefined
  error.value = ''
  try {
    const updated = await api<Layout>('/print-templates/' + kind + '/' + code)
    if (request === logoSequence) layout.value = updated
  } catch (failure) { if (request === logoSequence) error.value = message(failure) }
}, { immediate: true })

watch(documentKind, async kind => {
  numbering.value = undefined
  try {
    const data = await api<{items: Rule[]}>('/numbering-rules')
    if (kind === documentKind.value) numbering.value = data.items.find(rule => rule.kind === kind)
  } catch (failure) { if (kind === documentKind.value) error.value = message(failure) }
}, { immediate: true })

watch([documentKind, language], () => {numberPreview.value=''; printPreview.value=''})
async function preview(kind:'number'|'print'){
  if(busy.value)return
  busy.value=true
  error.value=''
  if(kind==='number')numberPreview.value=''
  else printPreview.value=''
  try{
    if(kind==='number'){
      numberPreview.value=(await api<{number:string}>('/numbering-rules/'+documentKind.value+'/preview','POST',{...numbering.value,site_id:site.value})).number
    }else{
      const csrf=document.cookie.split('; ').find(value=>value.startsWith('csrftoken='))?.slice(10) ?? ''
      const response=await fetch('/api/print-templates/'+documentKind.value+'/'+language.value+'/preview',{
        method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':decodeURIComponent(csrf)},body:JSON.stringify(layout.value),
      })
      if(!response.ok)throw new Error('Could not load print preview.')
      printPreview.value=await response.text()
    }
  }catch(e){error.value=message(e)}finally{busy.value=false}
}
async function save(kind: 'number' | 'print') {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    if (kind === 'number') numbering.value = await api<Rule>('/numbering-rules/' + documentKind.value, 'PATCH', {...numbering.value,apply_to_future:true})
    else layout.value = await api<Layout>('/print-templates/' + documentKind.value + '/' + language.value, 'PATCH', layout.value)
    notice.value = 'Changes saved.'
  } catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}

function logo(event: Event) {
  const field = event.target as HTMLInputElement
  const file = field.files?.[0]
  const current = layout.value
  if (!file || !current) return
  const sequence = ++logoSequence
  if (file.size > 256 * 1024 || !['image/png', 'image/jpeg'].includes(file.type)) {
    error.value = 'Choose a PNG or JPEG logo up to 256 KB.'
    field.value = ''
    return
  }
  error.value = ''
  const reader = new FileReader()
  reader.onload = () => {
    if (sequence === logoSequence && current === layout.value && typeof reader.result === 'string')
      current.logo = reader.result
  }
  reader.readAsDataURL(file)
}
</script>

<template>
  <h1>{{ t('Document settings') }}</h1>
  <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
  <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
  <nav class="section-tabs" :aria-label="t('Document settings')">
    <button type="button" class="tab-link" :aria-current="tab==='number' ? 'page' : undefined" @click="tab='number'">{{t('Numbering')}}</button>
    <button type="button" class="tab-link" :aria-current="tab==='print' ? 'page' : undefined" @click="tab='print'">{{t('Print templates')}}</button>
  </nav>
  <div class="field-wrap"><label>{{ t('Document type') }}
    <select data-field="documentKind" v-model="documentKind" :disabled="busy">
      <option value="pr">{{ t('Purchase requisitions') }}</option>
      <option value="po">{{ t('Purchase orders') }}</option>
      <option value="grn">{{ t('Goods receipts') }}</option>
      <option v-if="tab==='number'" value="si">{{t('Supplier invoices')}}</option>
    </select>
  </label><FieldHint :label="t('Document type')" /></div>
  <form v-validated-form v-if="tab==='number' && numbering" class="panel record-form" @submit.prevent="save('number')">
    <h2>{{ t(documentKind === 'pr' ? 'Purchase requisition numbering' : documentKind === 'po' ? 'Purchase order numbering' : documentKind === 'grn' ? 'Goods receipt numbering' : 'Supplier invoices') }}</h2>
    <p v-if="numbering.locked" class="muted">{{ t('Numbering changes apply to future documents.') }}</p>
    <div class="field-wrap"><label>{{ t('Prefix') }}<input data-field="prefix" :placeholder="t('Enter {field}').replace('{field}', t('Prefix'))" v-model="numbering.prefix" maxlength="16" pattern="[A-Z0-9]{0,16}"></label><FieldHint :label="t('Prefix')" /></div>
    <div class="field-wrap"><label>{{ t('First sequence number') }}<input data-field="start" :placeholder="t('Enter {field}').replace('{field}', t('First sequence number'))" v-model.number="numbering.start" type="number" min="1"
      max="999999999999" required></label><FieldHint :label="t('First sequence number')" required /></div>
    <div class="field-wrap" v-if="numbering.allow_site_numbering"><label class="delegation-check"><input data-field="per_site" v-model="numbering.per_site" type="checkbox">{{ t('Separate sequence per site') }}</label><FieldHint :label="t('Separate sequence per site')" /></div>
    <div class="field-wrap" v-if="numbering.per_site"><label>{{t('Site')}}<select data-field="site" v-model="site"><option v-for="s in workspace.sites" :key="s.id" :value="s.id">{{s.name}}</option></select></label><FieldHint :label="t('Site')" /></div>
    <p v-if="numberPreview"><bdi>{{numberPreview}}</bdi></p>
    <div class="actions"><button type="button" class="secondary" :disabled="busy || (numbering.per_site && !site)" @click="preview('number')">{{t('Preview')}}</button>
    <button class="primary" :disabled="busy">{{ t('Save numbering') }}</button></div>
  </form>
  <form v-validated-form v-if="tab==='print'" class="panel record-form" @submit.prevent="save('print')">
    <h2>{{ t(documentKind === 'pr' ? 'Purchase requisition print layout' : documentKind === 'po' ? 'Purchase order print layout' : 'Goods receipt print layout') }}</h2>
    <div class="field-wrap"><label>{{ t('Language') }}
      <select data-field="language" v-model="language" :disabled="busy"><option value="en">English</option><option value="ar">العربية</option></select>
    </label><FieldHint :label="t('Language')" /></div>
    <template v-if="layout">
      <div class="field-wrap"><label>{{ t('Print heading') }}<input data-field="heading" :placeholder="t('Enter {field}').replace('{field}', t('Print heading'))" v-model="layout.heading" maxlength="120"></label><FieldHint :label="t('Print heading')" /></div>
      <div class="field-wrap"><label>{{ t('Company name') }}<input data-field="company_name" :placeholder="t('Enter {field}').replace('{field}', t('Company name'))" v-model="layout.company_name" maxlength="120"></label><FieldHint :label="t('Company name')" /></div>
      <div class="field-wrap"><label>{{ t('Footer') }}<textarea data-field="footer" :placeholder="t('Enter details')" v-model="layout.footer" maxlength="240" rows="2"></textarea></label><FieldHint :label="t('Footer')" /></div>
      <div class="field-wrap"><label>{{ t('Logo (PNG or JPEG, up to 256 KB)') }}
        <input :key="language" type="file" accept="image/png,image/jpeg" @change="logo">
      </label><FieldHint :label="t('Logo (PNG or JPEG, up to 256 KB)')" /></div>
      <button v-if="layout.logo" type="button" class="secondary" @click="logoSequence++; layout.logo = ''">{{ t('Remove logo') }}</button>
      <div class="actions"><button class="secondary" type="button" :disabled="busy" @click="preview('print')">{{t('Preview')}}</button>
      <button class="primary" :disabled="busy">{{ t('Save print layout') }}</button></div>
      <iframe v-if="printPreview" sandbox="" :title="t('Preview')" :srcdoc="printPreview" style="width:100%;min-height:360px;background:white"></iframe>
    </template>
  </form>
</template>
