<script setup lang="ts">
import CopyId from "./CopyId.vue"
import TableToolbar from './TableToolbar.vue'
import PublicationEditor from './PublicationEditor.vue'
import {say} from './iam'
import {ref,onMounted,computed} from 'vue'
import {useRoute,useRouter,onBeforeRouteUpdate,onBeforeRouteLeave} from 'vue-router'
import {api,createRequest,allows,type Workspace} from './api'
import {t,message} from './i18n'
const props=defineProps<{workspace:Workspace}>()
const post=createRequest()
const route=useRoute(),router=useRouter()
const creating=computed(()=>route.params.entry==='new')
type Space={id:number;code:string;name:string;site_id:number;active:boolean;revision:number;default_language:string}
const rows=ref<Space[]>([]),name=ref(''),code=ref(''),lang=ref('en'),site=ref(props.workspace.sites.find(s=>s.active&&allows(props.workspace,'publication.manage',s.id))?.id)
const error=ref(''),notice=ref(''),busy=ref(false),selected=ref<Space>(),tab=ref('spaces')
const accounts=ref<{id:string;display_name:string;email:string;status:string;revision:number}[]>([])
const media=ref<{id:string;name:string;visibility:string;size:number}[]>([])
const spacePage=ref(1),accountPage=ref(1),mediaPage=ref(1),spaceMore=ref(false),accountMore=ref(false),mediaMore=ref(false)
const spaceSize=ref(25),spaceCount=ref(0),spaceTerm=ref("")
const accountSize=ref(25),accountCount=ref(0),accountTerm=ref("")
const mediaSize=ref(25),mediaCount=ref(0),mediaTerm=ref("")
const can=(permission:string,scope?:number|null)=>allows(props.workspace,permission,scope===undefined?site.value??null:scope)
async function run(fn:()=>Promise<unknown>){busy.value=true;error.value='';notice.value='';try{await fn()}catch(e){error.value=message(e)}finally{busy.value=false}}
async function load(){const data=await api<{results:Space[];count:number;next:string|null}>('/platform/v1/spaces?page='+spacePage.value+"&page_size="+spaceSize.value+"&q="+encodeURIComponent(spaceTerm.value));rows.value=data.results;spaceCount.value=data.count}
const initialSite=site.value
const dirty=computed(()=>!!name.value||!!code.value||lang.value!=='en'||site.value!==initialSite)
function leaving(){if(!busy.value&&creating.value&&dirty.value&&!window.confirm(t('Discard unsaved changes?')))return false}
onBeforeRouteLeave(leaving)
onBeforeRouteUpdate(leaving)
async function save(){await run(async()=>{await post('/platform/v1/spaces',{name:name.value,code:code.value,site_id:site.value,default_language:lang.value});name.value='';code.value='';await load();await router.push('/publication');notice.value=t('Public site saved')})}
async function toggle(row:Space){await run(async()=>{await api('/platform/v1/spaces/'+row.id,'PATCH',{revision:row.revision,active:!row.active});await load()})}
async function details(row:Space,section:string){selected.value=row;tab.value=section;accountPage.value=1;mediaPage.value=1;await run(loadDetails)}
async function loadDetails(){if(!selected.value)return;const id=selected.value.id
if(tab.value==='accounts'){const d=await api<{results:typeof accounts.value;count:number;next:string|null}>('/platform/v1/identities?space_id='+id+'&page='+accountPage.value+"&page_size="+accountSize.value+"&q="+encodeURIComponent(accountTerm.value));accounts.value=d.results;accountCount.value=d.count}
if(tab.value==='media'){const d=await api<{results:typeof media.value;count:number;next:string|null}>('/platform/v1/media?space_id='+id+'&page='+mediaPage.value+"&page_size="+mediaSize.value+"&q="+encodeURIComponent(mediaTerm.value));media.value=d.results;mediaCount.value=d.count}}
async function status(row:typeof accounts.value[number]){await run(async()=>{await api('/platform/v1/identities/'+row.id,'PATCH',{revision:row.revision,status:row.status==='disabled'?'active':'disabled'});await loadDetails()})}
onMounted(()=>run(load))
</script>
<template><main class="stack"><h1>{{t('Platform Services')}}</h1><p>{{t('Customer accounts are separate from employee accounts.')}}</p>
<p v-if="error" class="alert" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
<section v-if="tab==='spaces'" class="panel"><h2>{{t('Public Sites')}}</h2>
<template v-if="!creating"><table><thead><tr><th>{{t('Code')}}</th><th>{{t('Name')}}</th><th>{{t('Status')}}</th><th>{{t('Actions')}}</th></tr></thead><tbody>
<tr v-for="row in rows" :key="row.id"><td>{{row.code}}</td><td>{{row.name}}</td><td>{{t(row.active?'Active':'Inactive')}}</td><td class="actions">
<button v-if="can('publication.view',row.site_id)" @click="details(row,'content')">{{say('Content & menus','المحتوى والقوائم')}}</button>
<button v-if="can('publication.manage',row.site_id)" class="secondary" :disabled="busy" @click="toggle(row)">{{t(row.active?'Deactivate':'Activate')}}</button>
<button v-if="can('public_identity.view',row.site_id)" class="secondary" @click="details(row,'accounts')">{{t('Customer Accounts')}}</button>
<button v-if="can('media_service.view',row.site_id)" class="secondary" @click="details(row,'media')">{{t('Media Service')}}</button></td></tr></tbody></table>
<TableToolbar :page="spacePage" :size="spaceSize" :count="spaceCount" :query="spaceTerm" @page="spacePage=$event;run(load)" @size="spaceSize=$event;spacePage=1;run(load)" @search="spaceTerm=$event;spacePage=1;run(load)"/>
<p><RouterLink v-if="can('publication.manage')" to="/publication/new">{{t('New public site')}}</RouterLink></p>
<p><a href="/public/account" target="_blank" rel="noopener">{{t('Open customer account page')}}</a></p></template>
<template v-else><h2>{{t('New public site')}}</h2><form class="record-form" @submit.prevent="save">
<label>{{t('Name')}}<input v-model="name" required maxlength="120" :placeholder="t('Public site name')"></label>
<label>{{t('Code')}}<input v-model="code" required maxlength="50" pattern="[a-z0-9_-]+" placeholder="main-store"></label>
<label>{{t('Site')}}<select v-model="site" required><option v-for="s in workspace.sites.filter(s=>s.active&&allows(workspace,'publication.manage',s.id))" :key="s.id" :value="s.id">{{s.name}}</option></select></label>
<label>{{t('Language')}}<select v-model="lang"><option value="en">English</option><option value="ar">العربية</option></select></label>
<div class="actions"><button class="primary" :disabled="busy">{{t('Save')}}</button><RouterLink to="/publication">{{t('Cancel')}}</RouterLink></div></form></template></section>
<section v-else-if="tab==='content' && selected"><button @click="tab='spaces'">{{t('Back')}}</button><PublicationEditor :key="selected.id" :space="selected" :workspace="workspace"/></section>
<section v-else class="panel"><button class="secondary" @click="tab='spaces'">{{t('Back')}}</button><h2>{{selected?.name}} · {{t(tab==='accounts'?'Customer Accounts':'Media Service')}}</h2>
<table v-if="tab==='accounts'"><thead><tr><th>ID</th><th>{{t('Name')}}</th><th>{{t('Email')}}</th><th>{{t('Status')}}</th><th>{{t('Actions')}}</th></tr></thead><tbody><tr v-for="row in accounts" :key="row.id"><td><CopyId :value="row.id"/></td><td>{{row.display_name}}</td><td>{{row.email}}</td><td>{{row.status}}</td><td><button v-if="row.status!=='pending'&&can('public_identity.manage',null)" :disabled="busy" @click="status(row)">{{t(row.status==='disabled'?'Activate':'Deactivate')}}</button></td></tr></tbody></table>
<table v-else><thead><tr><th>{{t('Name')}}</th><th>{{t('Visibility')}}</th><th>{{t('Size')}}</th></tr></thead><tbody><tr v-for="row in media" :key="row.id"><td><a :href="'/api/platform/v1/media/'+row.id+'/download'">{{row.name}}</a></td><td>{{row.visibility}}</td><td>{{row.size}}</td></tr></tbody></table>
<TableToolbar v-if="tab==='accounts'" :page="accountPage" :size="accountSize" :count="accountCount" :query="accountTerm" @page="accountPage=$event;run(loadDetails)" @size="accountSize=$event;accountPage=1;run(loadDetails)" @search="accountTerm=$event;accountPage=1;run(loadDetails)"/><TableToolbar v-else :page="mediaPage" :size="mediaSize" :count="mediaCount" :query="mediaTerm" @page="mediaPage=$event;run(loadDetails)" @size="mediaSize=$event;mediaPage=1;run(loadDetails)" @search="mediaTerm=$event;mediaPage=1;run(loadDetails)"/>
</section></main></template>
