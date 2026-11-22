<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, createRequest } from './api'
import { message, t } from './i18n'
import type { Activity, Owner } from './crm'

const props=defineProps<{site:number;lead?:number;opportunity?:number;activity?:Activity}>()
const emit=defineEmits<{saved:[id:number];cancel:[]}>()
function localTime(value?:string|null) {
  if (!value) return ''
  const date=new Date(value)
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16)
}
const form=ref({title:props.activity?.title??'',kind:props.activity?.kind??'call',notes:props.activity?.notes??'',
  owner_id:props.activity?.owner_id,due_at:localTime(props.activity?.due_at)})
const owners=ref<Owner[]>([])
const busy=ref(false)
const error=ref('')
const create=createRequest()
onMounted(async()=>{
  try {owners.value=(await api<{items:Owner[]}>('/crm/owners?site='+props.site)).items}
  catch(failure){error.value=message(failure)}
})
async function save(){
  if (busy.value) return
  busy.value=true
  error.value=''
  try {
    const local=form.value.due_at
    const due_at=form.value.kind!=='note'&&local ? new Date(local).toISOString():null
    const payload={...form.value,due_at}
    const row=props.activity
      ? await api<Activity>('/crm/activities/'+props.activity.id,'PATCH',{...payload,action:'edit',revision:props.activity.revision})
      : await create<Activity>('/crm/activities',{...payload,lead_id:props.lead,opportunity_id:props.opportunity})
    emit('saved',row.id)
  } catch(failure){error.value=message(failure)}
  finally{busy.value=false}
}
</script>

<template>
  <form v-validated-form class="panel record-form crm-panel" @submit.prevent="save">
    <h2>{{ t(activity ? 'Edit activity' : 'New activity') }}</h2>
    <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
    <div class="crm-fields">
      <label>{{ t('Title') }}<input v-model.trim="form.title" data-field="title" required maxlength="160" :placeholder="t('Enter a short, descriptive title')"></label>
      <label>{{ t('Activity type') }}<select v-model="form.kind" data-field="kind" required>
        <option value="call">{{ t('Call') }}</option><option value="meeting">{{ t('Meeting') }}</option>
        <option value="task">{{ t('Task') }}</option><option value="note">{{ t('Note') }}</option>
      </select></label>
      <label>{{ t('Owner') }}<EntityAutocomplete v-model="form.owner_id" lookup="users" :local-items="owners.map(row => ({ id: row.id, label: row.username }))" :empty-label="t(activity ? 'Keep current owner' : 'Assigned to me')" data-field="owner_id" /></label>
      <label v-if="form.kind!=='note'">{{ t('Due date and time') }}<input v-model="form.due_at" data-field="due_at" type="datetime-local" required>
        <small class="muted">{{ t('Time is shown in your device time zone.') }}</small></label>
    </div>
    <label>{{ t('Notes') }}<textarea v-model.trim="form.notes" data-field="notes" maxlength="1000" :placeholder="t('Add a short note')"></textarea></label>
    <div class="actions"><button class="primary" :disabled="busy">{{ t('Save') }}</button>
      <button type="button" class="secondary" @click="emit('cancel')">{{ t('Cancel') }}</button></div>
  </form>
</template>
