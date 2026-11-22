<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, createRequest } from './api'
import { message, t } from './i18n'

type Item = {id:number;code:string;name:string}
const props = defineProps<{opportunity:number;revision:number}>()
const emit = defineEmits<{cancel:[];created:[id:number]}>()
const items = ref<Item[]>([])
const lines = ref([{item_id:0,quantity:'1',price:''}])
const date = ref(new Date().toISOString().slice(0,10))
const expires = ref('')
const busy = ref(false)
const error = ref('')
const create = createRequest()
onMounted(async () => {
  try { items.value = (await api<{items:Item[]}>('/sales/catalog')).items }
  catch (failure) { error.value = message(failure) }
})
async function save() {
  if (busy.value) return
  error.value = ''
  busy.value = true
  try {
    const result = await create<{id:number}>('/crm/opportunities/' + props.opportunity + '/quotation',
      {revision:props.revision,date:date.value,expires:expires.value,lines:lines.value})
    emit('created',result.id)
  } catch (failure) { error.value = message(failure) }
  finally { busy.value = false }
}
</script>

<template>
  <form v-validated-form class="record-form" @submit.prevent="save">
    <h3>{{ t('Create quotation') }}</h3>
    <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
    <div class="crm-fields">
      <label>{{ t('Quotation date') }}<input v-model="date" data-field="date" type="date" required></label>
      <label>{{ t('Expiry date') }}<input v-model="expires" data-field="expires" type="date" :min="date" required></label>
    </div>
    <fieldset v-for="(line,index) in lines" :key="index" class="purchase-line">
      <legend>{{ t('Line item') }} {{ index+1 }}</legend>
      <label>{{ t('Item') }}<EntityAutocomplete v-model="line.item_id" lookup="items" :local-items="items.map(item => ({ id: item.id, label: item.code + ' · ' + item.name }))" :empty-value="0" :placeholder="t('Select item')" :data-field="'lines.'+index+'.item_id'" required /></label>
      <label>{{ t('Quantity') }}<input v-model="line.quantity" :data-field="'lines.'+index+'.quantity'" inputmode="decimal" required :placeholder="t('e.g., 2.500')"></label>
      <label>{{ t('Unit price') }}<input v-model="line.price" :data-field="'lines.'+index+'.price'" inputmode="decimal" required :placeholder="t('e.g., 125.50')"></label>
      <button v-if="lines.length>1" class="secondary" type="button" @click="lines.splice(index,1)">{{ t('Remove item') }}</button>
    </fieldset>
    <div class="actions">
      <button type="button" class="secondary" :disabled="lines.length>=100" @click="lines.push({item_id:0,quantity:'1',price:''})">{{ t('Add item') }}</button>
      <button class="primary" :disabled="busy">{{ t('Create quotation') }}</button>
      <button type="button" class="secondary" @click="emit('cancel')">{{ t('Cancel') }}</button>
    </div>
  </form>
</template>
