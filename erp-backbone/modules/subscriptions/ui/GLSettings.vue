<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api,createRequest} from '../../../frontend/src/api'
import {locale} from '../../../frontend/src/i18n'
const props=defineProps<{site:number,space?:number,editable:boolean}>()
const l=(en:string,ar:string)=>locale.value==='ar'?ar:en
const fields=[['receivable','asset','Accounts receivable','ذمم العملاء'],['deferred','liability','Deferred revenue','إيراد مؤجل'],['revenue','revenue','Subscription revenue','إيراد الاشتراكات'],['setup','revenue','Setup fee revenue','إيراد رسوم التأسيس'],['vat','liability','Output VAT payable','ضريبة المخرجات المستحقة'],['bank','asset','Cash / bank clearing','تسوية النقدية والبنك']]
const data=ref<any>(),msg=ref(''),send=createRequest()
async function load(){data.value=await api('/subscriptions/gl-settings?site_id='+props.site+'&space_id='+(props.space??''))}
onMounted(()=>load().catch(e=>msg.value=e.message))
async function save(){try{await send('/subscriptions/gl-settings',{site_id:props.site,revision:data.value.revision,...data.value.values});await load();msg.value=l('Saved','تم الحفظ')}catch(e){msg.value=(e as Error).message}}
</script>
<template><section class="panel"><p role="status">{{msg}}</p><template v-if="data"><p v-if="!data.revision">{{l('Posting pending until site accounts are configured.','الترحيل معلق حتى تحديد حسابات الموقع.')}}</p><form class="record-form" @submit.prevent="save"><label v-for="[key,kind,en,ar] in fields" :key="key">{{l(en,ar)}}<select v-model="data.values[key]" required :disabled="!editable"><option v-for="a in data.accounts.filter((x:any)=>x.kind===kind)" :value="a.id">{{a.code}} · {{a.name}}</option></select></label><button v-if="editable">{{l('Save site mapping','حفظ حسابات الموقع')}}</button></form><p>{{l('Pending cycles','الدورات المعلقة')}} · {{data.pending_count}}</p><ul><li v-for="c in data.pending">{{c.invoice__number??c.id}} · {{c.schedule__warning||l('Awaiting processing','بانتظار المعالجة')}}</li></ul></template></section></template>
