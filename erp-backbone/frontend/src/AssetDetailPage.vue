<script setup lang="ts">
import HistoryTable from "./HistoryTable.vue"
import {computed, ref, watch} from 'vue'
import {useRoute} from 'vue-router'
import {allows,api,requestKey,type Workspace} from './api'
import {message,t} from './i18n'
type Money={purchased_on:string|null;purchase_cost:string;purchase_currency:string;purchase_reference:string}
type History={source_site__name:string|null;destination_site__name:string;transferred_on:string;recorded_at:string;reason:string;actor_id:number}
type RecordData={id:number;name:string;tag:string;site_id:number|null;revision:number;financial?:Money;transfers:History[]}
const {workspace}=defineProps<{workspace:Workspace}>()
const route=useRoute(), row=ref<RecordData>(), error=ref(''), notice=ref(''), busy=ref(false)
const target=ref<number>(), reason=ref(''), day=ref('')
const destination=computed(()=>workspace.sites.filter(s=>s.active && s.id!==row.value?.site_id && allows(workspace,'assets.view',s.id) && allows(workspace,'assets.transfer',s.id)))
const canEdit=computed(()=>row.value && allows(workspace,'assets.edit',row.value.site_id))
const canCost=computed(()=>canEdit.value && row.value?.financial && allows(workspace,'assets.cost.edit',row.value.site_id))
let seq=0
let transferKey='', transferPayload=''
async function load(){
  const token=++seq
  try{const record=await api<RecordData>('/assets/'+route.params.id+'?history=paged'); if(token===seq)row.value=record}
  catch(e){if(token===seq)error.value=message(e)}
}
watch(()=>route.params.id,()=>{row.value=undefined; error.value=''; notice.value=''; target.value=undefined; void load()},{immediate:true})
async function submit(transfer:boolean){
  const current=row.value
  if(!current || busy.value)return
  busy.value=true;error.value='';notice.value=''
  try{
    if(transfer){
      const data={destination_site_id:target.value,transferred_on:day.value,reason:reason.value,revision:current.revision}
      const keyPayload=JSON.stringify([current.id,data])
      if(keyPayload!==transferPayload){transferPayload=keyPayload;transferKey=requestKey()}
      await api('/assets/'+current.id+'/transfer','POST',data,transferKey)
      reason.value='';target.value=undefined
      await load()
    }else{
      row.value=await api<RecordData>('/assets/'+current.id,'PATCH',{name:current.name,revision:current.revision,...(canCost.value?{financial:current.financial}:{})})
    }
    notice.value='Changes saved.'
  }catch(e){error.value=message(e)}finally{busy.value=false}
}
import DateInput from './DateInput.vue'
</script>

<template>
  <RouterLink class="back-link" to="/assets">{{t('Assets')}}</RouterLink>
  <p v-if="error" class="alert" role="alert">{{t(error)}}</p><p v-if="notice" class="success">{{t(notice)}}</p>
  <template v-if="row">
    <h1><bdi>{{row.tag}}</bdi> · {{row.name}}</h1>
    <form v-validated-form class="panel record-form" @submit.prevent="submit(false)">
      <div class="field-wrap"><label>{{t('Name')}}<input data-field="name" :placeholder="t('e.g., John Doe')" v-model="row.name" required maxlength="120" :readonly="!canEdit"></label><FieldHint :label="t('Name')" required :readonly="!canEdit" /></div>
      <p>{{t('Site')}} · {{workspace.sites.find(s=>s.id===row?.site_id)?.name ?? '—'}}</p>
      <template v-if="row.financial">
        <DateInput :label="t('Purchase date')" v-model="row.financial.purchased_on" type="date" :readonly="!canCost" />
        <div class="field-wrap"><label>{{t('Purchase cost')}}<input :placeholder="t('e.g., 125.50')" v-model="row.financial.purchase_cost" inputmode="decimal" :readonly="!canCost"></label><FieldHint :label="t('Purchase cost')" :readonly="!canCost" /></div>
        <div class="field-wrap"><label>{{t('Currency')}}<input :placeholder="t('Enter {field}').replace('{field}', t('Currency'))" v-model="row.financial.purchase_currency" maxlength="3" :readonly="!canCost"></label><FieldHint :label="t('Currency')" :readonly="!canCost" /></div>
        <div class="field-wrap"><label>{{t('Purchase reference')}}<input :placeholder="t('Enter {field}').replace('{field}', t('Purchase reference'))" v-model="row.financial.purchase_reference" maxlength="120" :readonly="!canCost"></label><FieldHint :label="t('Purchase reference')" :readonly="!canCost" /></div>
      </template>
      <button v-if="canEdit" class="primary" :disabled="busy">{{t('Save')}}</button>
      <RouterLink v-if="row.site_id && allows(workspace,'maintenance.view',row.site_id) && allows(workspace,'workorders.view',row.site_id) && workspace.modules.some(m=>m.id==='workorders' && m.enabled)" :to="'/maintenance/assets/'+row.id">{{t('Maintenance')}}</RouterLink>
    </form>
    <form v-validated-form v-if="allows(workspace,'assets.transfer',row.site_id) && destination.length" class="panel record-form" @submit.prevent="submit(true)">
      <h2>{{t('Transfer asset')}}</h2>
      <div class="field-wrap"><label>{{t('Destination site')}}<select data-field="target" v-model="target" required><option v-for="site in destination" :key="site.id" :value="site.id">{{site.name}}</option></select></label><FieldHint :label="t('Destination site')" required /></div>
      <DateInput data-field="day" :label="t('Transfer date')" v-model="day" type="date" required />
      <div class="field-wrap"><label>{{t('Reason')}}<input data-field="reason" :placeholder="t('Explain the reason')" v-model="reason" maxlength="500" required></label><FieldHint :label="t('Reason')" required /></div>
      <button class="primary" :disabled="busy">{{t('Transfer asset')}}</button>
    </form>
    <h2>{{t('Transfer history')}}</h2>
    <HistoryTable :url="'/assets/'+row.id+'/transfers'" :version="row.revision" v-slot="{rows}" class="panel table-panel">
      <table><thead><tr><th>{{t('Transfer date')}}</th><th>{{t('Source site')}}</th><th>{{t('Destination site')}}</th><th>{{t('Reason')}}</th><th>{{t('Actor')}}</th></tr></thead>
        <tbody><tr v-for="h in rows" :key="h.recorded_at"><td>{{h.transferred_on}}</td><td>{{h.source_site__name ?? '—'}}</td><td>{{h.destination_site__name}}</td><td>{{h.reason}}</td><td>#{{h.actor_id}}</td></tr></tbody>
      </table>
    </HistoryTable>
  </template>
</template>
