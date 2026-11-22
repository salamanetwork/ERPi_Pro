<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api,type Workspace} from './api'
import {locale,message,t} from './i18n'
import { screens } from './navigation'
type Role={id:number; name:string; hidden_modules:string[]}

const {workspace}=defineProps<{workspace:Workspace}>()
const roles=ref<Role[]>([]), role=ref<Role>(), error=ref(''), busy=ref(false), saved=ref(false)
onMounted(async()=>{try{roles.value=(await api<{roles:Role[]}>('/access')).roles}catch(e){error.value=message(e)}})
async function save(){
  if(!role.value || busy.value)return
  busy.value=true;error.value='';saved.value=false
  try{await api('/access/roles/'+role.value.id+'/visibility','PATCH',{hidden_modules:role.value.hidden_modules});saved.value=true}
  catch(e){error.value=message(e)}finally{busy.value=false}
}
</script>
<template>
  <h1>{{t('Module visibility')}}</h1>
  <p v-if="error" class="alert" role="alert">{{t(error)}}</p><p v-if="saved" class="success">{{t('Changes saved.')}}</p>
  <form v-validated-form class="panel record-form" @submit.prevent="save">
    <div class="field-wrap"><label>{{t('Role')}}<select data-field="role" v-model="role" :disabled="busy" @change="saved=false"><option v-for="r in roles" :key="r.id" :value="r">{{r.name}}</option></select></label><FieldHint :label="t('Role')" /></div>
    <template v-if="role">
      <div class="field-wrap" v-for="mod in workspace.modules" :key="mod.id"><label class="check">
        <input data-field="hidden_modules" v-model="role.hidden_modules" type="checkbox" :value="mod.id" :disabled="busy">
        {{t('Hide in navigation')}} · {{mod.name[locale]}}
        <span class="badge">{{t(mod.enabled?'Enabled':'Disabled')}}</span>
      </label><FieldHint :label="t('Hide in navigation')" /></div>
      <fieldset>
        <legend>{{ t('Screens') }}</legend>
        <div class="field-wrap" v-for="screen in screens" :key="screen.id"><label class="check">
          <input data-field="hidden_modules" v-model="role.hidden_modules" type="checkbox" :value="screen.id" :disabled="busy">
          {{ t('Hide in navigation') }} · {{ t(screen.label) }}
        </label><FieldHint :label="t('Hide in navigation')" /></div>
      </fieldset>
      <button class="primary" :disabled="busy">{{t('Save')}}</button>
    </template>
  </form>
</template>
