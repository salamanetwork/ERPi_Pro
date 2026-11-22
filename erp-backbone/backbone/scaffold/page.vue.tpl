<script setup lang="ts">
import {ref,onMounted,computed} from 'vue'
import {api,createRequest,allows,type Workspace} from '../../frontend/src/api'
import {locale,message} from '../../frontend/src/i18n'
import TableToolbar from '../../frontend/src/TableToolbar.vue'
import manifest from '../manifest.json'
const props=defineProps<{workspace:Workspace}>()
const route='/'+manifest.id+'/records',saveRequest=createRequest()
const rows=ref<{id:number;name:string}[]>([]),name=ref(''),target=ref<number>(),page=ref(1),count=ref(0),size=ref(25),q=ref(""),error=ref(''),busy=ref(false)
const spaces=ref<{id:number;internal_site_id:number;name:string}[]>([])
const scope:string='__SCOPE__', permission=manifest.id+'.create'
const targets=computed(()=>scope==='site'?props.workspace.sites.filter(x=>x.active&&allows(props.workspace,permission,x.id)):spaces.value)
const writable=computed(()=>allows(props.workspace,permission,scope==='global'?null:(scope==='space'?spaces.value.find(x=>x.id===target.value)?.internal_site_id:undefined)))
async function load(){if(scope==='space'&&!target.value){rows.value=[];return}const d=await api<{results:typeof rows.value;count:number}>(route+'?page='+page.value+'&page_size='+size.value+'&q='+encodeURIComponent(q.value)+(scope==='space'?'&space_id='+target.value:''));rows.value=d.results;count.value=d.count}
async function run(action:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';try{await action()}catch(e){error.value=message(e)}finally{busy.value=false}}
async function create(){await saveRequest(route,{name:name.value,...(scope==='global'?{}:{[scope+'_id']:target.value})});name.value='';await load()}
onMounted(()=>run(async()=>{if(scope==='space'){spaces.value=(await api<{results:typeof spaces.value}>('/'+manifest.id+'/scopes')).results;target.value=spaces.value[0]?.id}await load()}))
</script>
<template><main class="stack"><h1>{{manifest.name[locale]}}</h1><p v-if="error" role="alert">{{error}}</p><label v-if="scope==='space'">{{locale==='ar'?'موقع النشر':'Public site'}}<select v-model="target" @change="page=1;run(load)"><option v-for="t in spaces" :key="t.id" :value="t.id">{{t.name}}</option></select></label><form v-if="writable" class="panel record-form" @submit.prevent="run(create)"><label>{{locale==='ar'?'الاسم':'Name'}}<input v-model="name" required maxlength="160"></label><label v-if="scope==='site'">{{scope==='site'?(locale==='ar'?'الموقع':'Site'):(locale==='ar'?'موقع النشر':'Public site')}}<select required v-model="target"><option v-for="t in targets" :key="t.id" :value="t.id">{{t.name}}</option></select></label><button :disabled="busy">{{locale==='ar'?'إنشاء':'Create'}}</button></form><ul><li v-for="r in rows" :key="r.id">{{r.name}}</li></ul><TableToolbar :page="page" :count="count" :size="size" :query="q" @page="page=$event;load()" @size="size=$event;page=1;load()" @search="q=$event;page=1;load()"/></main></template>