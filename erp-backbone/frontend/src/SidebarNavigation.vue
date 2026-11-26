<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {useRoute} from 'vue-router'
import {api,ApiError,type Workspace} from './api'
import {t,locale,message} from './i18n'
import {activeScreen,groups,screenVisible} from './navigation'
type Layout={order?:string[];children?:Record<string,string[]>;hidden_screens?:string[];hidden_groups?:string[]}
type Pref={layout:Layout;revision:number}
const props=defineProps<{workspace:Workspace;reports?:string}>()
const emit=defineEmits<{resetLayout:[]}>()
const route=useRoute(), editing=ref(false),busy=ref(false),ready=ref(false),notice=ref('')
const pref=ref<Pref>({layout:{},revision:0}),expanded=ref<Record<string,boolean>>({})
const key=computed(()=>'erp:sidebar:'+props.workspace.username)
const l=(a:string,b:string)=>locale.value==='ar'?b:a
function read(k:string){try{return JSON.parse(localStorage.getItem(k)||'{}')}catch{return {}}}
function store(k:string,v:unknown){try{localStorage.setItem(k,JSON.stringify(v))}catch{/* server remains authoritative */}}
const defaults=groups.map(g=>g.id)
function ordered(saved:string[]|undefined,all:string[]){return [...new Set([...(saved||[]).filter(k=>all.includes(k)),...all])]}
const layout=computed(()=>pref.value.layout)
const order=computed(()=>ordered(layout.value.order,defaults))
const allScreens=groups.flatMap(g=>g.screens)
const eligible=computed(()=>allScreens.filter(s=>screenVisible(props.workspace,s)&&(s.id!=='reports'||props.reports)))
function children(id:string){const c=layout.value.children||{},assigned=new Set(Object.values(c).flat())
 return [...(c[id]||[]),...(groups.find(g=>g.id===id)?.screens||[]).map(s=>s.id).filter(k=>!assigned.has(k))]}
const groupRows=computed(()=>order.value.map(id=>({id,label:groups.find(g=>g.id===id)!.label,screens:children(id).flatMap(k=>eligible.value.filter(s=>s.id===k))})))
const hiddenGroups=computed(()=>layout.value.hidden_groups||[]),hiddenScreens=computed(()=>layout.value.hidden_screens||[])
const shown=computed(()=>groupRows.value.filter(g=>!hiddenGroups.value.includes(g.id)&&(editing.value||g.screens.some(s=>!hiddenScreens.value.includes(s.id)))))
const hrSection=computed(()=>groups.find(g=>g.id==='hr')?.screens.find(s=>screenVisible(props.workspace,s))?.path.split('/').at(-1))
const active=computed(()=>activeScreen(route.path,route.query.tab==='plans',hrSection.value))
const current=computed(()=>groupRows.value.find(g=>g.screens.some(s=>s.id===active.value))?.id)
const isOpen=(id:string)=>editing.value||(expanded.value[id]??(current.value===id))
function expand(id:string){expanded.value={...expanded.value,[id]:!isOpen(id)};store(key.value+':open',expanded.value)}
watch(()=>route.fullPath,()=>{if(current.value){expanded.value[current.value]=true;store(key.value+':open',expanded.value)}})
function setAll(mode:string){expanded.value=Object.fromEntries(defaults.map(k=>[k,mode==='open'||(mode==='default'&&k==='general')]));store(key.value+':open',expanded.value)}
const url='/preferences/menu'
async function persist(value:Layout){
 if(busy.value||!ready.value)return false
 busy.value=true
 const userKey=key.value
 try{const r=await api<Pref>(url,'PATCH',{layout:value,revision:pref.value.revision});if(userKey!==key.value)return false;pref.value=r;store(userKey+':cache',r);notice.value='';return true}
 catch(e){if(userKey!==key.value)return false;if(e instanceof ApiError&&e.status===409){const r=await api<Pref>(url);if(userKey===key.value){pref.value=r;store(userKey+':cache',r);notice.value=t('Menu changed on another device. Latest order loaded.')}}else notice.value=message(e);return false}
 finally{if(userKey===key.value)busy.value=false}
}
watch(key,async k=>{
 editing.value=false;busy.value=false;ready.value=false;notice.value=''
 const old=read(k),cached=read(k+':cache')
 pref.value=cached.layout?cached:{layout:{},revision:0}
 expanded.value=Object.keys(read(k+':open')).length?read(k+':open'):old.expanded||{}
 try{const result=await api<Pref>(url);if(k!==key.value)return;pref.value=result;ready.value=true
 if(current.value)expanded.value[current.value]=true
 if(!result.revision&&Array.isArray(old.order))await persist({order:ordered(old.order,defaults)})
 else store(k+':cache',result)
 }catch(e){if(k===key.value)notice.value=message(e)}
},{immediate:true})
async function reset(){if(confirm(t('Restore default menu, visibility and panel sizes?'))&&await persist({})){expanded.value={};store(key.value+':open',{});store(key.value,{});emit('resetLayout')}}
function hide(k:string,group:boolean){const field=group?'hidden_groups':'hidden_screens',v=layout.value[field]||[];void persist({...layout.value,[field]:v.includes(k)?v.filter(x=>x!==k):[...v,k]})}
function place(id:string,group:string,target?:string,after=false){
 if(target===id)return
 const childrenMap=Object.fromEntries(defaults.map(g=>[g,children(g).filter(s=>s!==id)]))
 const list=childrenMap[group];if(!list)return
 const i=target?list.indexOf(target):-1;list.splice(i<0?list.length:i+Number(after),0,id)
 void persist({...layout.value,children:childrenMap})
}
function groupMove(id:string,target:string,after:boolean){if(id===target)return;const v=order.value.filter(k=>k!==id);v.splice(v.indexOf(target)+Number(after),0,id);void persist({...layout.value,order:v})}
function arrow(id:string,g:string,d:number){const v=g?groupRows.value.find(r=>r.id===g)!.screens.filter(s=>!hiddenScreens.value.includes(s.id)).map(s=>s.id):shown.value.map(r=>r.id);const dest=v[v.indexOf(id)+d];if(dest){if(g)place(id,g,dest,d>0);else groupMove(id,dest,d>0)}}
let drag:{id:string;group:string;pointer:number}|undefined
function start(e:PointerEvent,id:string,g=''){if(!e.isPrimary||e.button!==0||busy.value)return;e.stopPropagation();(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);drag={id,group:g,pointer:e.pointerId}}
function end(e:PointerEvent){if(!drag||e.pointerId!==drag.pointer)return;const d=drag;drag=undefined
 const el=document.elementFromPoint(e.clientX,e.clientY),g=el?.closest<HTMLElement>('[data-menu-group]'),s=el?.closest<HTMLElement>('[data-menu-screen]')
 if(!g)return
 const group=g.dataset.menuGroup!,box=(d.group&&s?s:g).getBoundingClientRect(),after=e.clientY>box.top+box.height/2
 if(d.group)place(d.id,group,s?.dataset.menuScreen,after);else groupMove(d.id,group,after)
}
const picker=ref('')
</script>
<template><nav :aria-label="t('Workspace')"><div class="sidebar-tools">
<button :aria-label="t('Expand all')" @click="setAll('open')">+</button><button :aria-label="t('Collapse all')" @click="setAll('closed')">−</button><button @click="setAll('default')">{{t('Default UI')}}</button><button :disabled="!ready||busy" @click="reset">{{t('Reset UI')}}</button><button :disabled="!ready||busy" :aria-pressed="editing" @click="editing=!editing">{{editing?t('Done'):t('Edit menu')}}</button></div>
<p v-if="notice" role="status">{{notice}}</p>
<section v-for="g in shown" :key="g.id" class="sidebar-group" :data-menu-group="g.id"><div :class="{'menu-edit-line':editing}"><template v-if="editing"><button class="sidebar-drag-handle" :aria-label="t('Drag')" @pointerdown="start($event,g.id,'')" @pointerup="end" @pointercancel="drag=undefined">⠿</button></template><button class="sidebar-group-toggle" :aria-expanded="isOpen(g.id)" @click="expand(g.id)"><span class="menu-name">{{t(g.label)}}</span> <span>{{isOpen(g.id)?'−':'+'}}</span></button><template v-if="editing"><button type="button" :disabled="busy" :aria-label="t('Move up')" @click="arrow(g.id,'',-1)">↑</button><button type="button" :disabled="busy" :aria-label="t('Move down')" @click="arrow(g.id,'',1)">↓</button><button type="button" :disabled="busy" :aria-label="t('Hide')" @click="hide(g.id,true)">👁</button></template></div>
<div v-show="isOpen(g.id)" class="sidebar-children"><div v-for="s in g.screens.filter(x=>!hiddenScreens.includes(x.id))" :key="s.id" :data-menu-screen="s.id"><div :class="{'menu-edit-line':editing}"><template v-if="editing"><button class="sidebar-drag-handle" :aria-label="t('Drag')" @pointerdown="start($event,s.id,g.id)" @pointerup="end" @pointercancel="drag=undefined">⠿</button></template><RouterLink :to="s.id==='reports'?reports!:s.path" :class="{current:s.id===active}" :aria-current="s.id===active?'page':undefined">{{locale==='ar'&&s.labelAr?s.labelAr:t(s.label)}}</RouterLink><template v-if="editing"><button type="button" :disabled="busy" :aria-label="t('Move up')" @click="arrow(s.id,g.id,-1)">↑</button><button type="button" :disabled="busy" :aria-label="t('Move down')" @click="arrow(s.id,g.id,1)">↓</button><button type="button" :disabled="busy" :aria-label="t('Move to group')" @click="picker=picker===s.id?'':s.id">⇄</button><button type="button" :disabled="busy" :aria-label="t('Hide')" @click="hide(s.id,false)">👁</button></template></div><select v-if="editing && picker===s.id" :aria-label="t('Move to group')" value="" @change="place(s.id,($event.target as HTMLSelectElement).value);picker=''"><option disabled value="">{{t('Move to group…')}}</option><option v-for="dest in groupRows.filter(r=>r.id!==g.id)" :value="dest.id">{{t(dest.label)}}</option></select></div></div></section><details v-if="editing"><summary>{{t('Hidden items')}}</summary><button v-for="g in groupRows.filter(r=>hiddenGroups.includes(r.id))" :disabled="busy" @click="hide(g.id,true)">{{t('Restore')}} · {{t(g.label)}}</button><button v-for="s in eligible.filter(x=>hiddenScreens.includes(x.id))" :disabled="busy" @click="hide(s.id,false)">{{t('Restore')}} · {{locale==='ar'&&s.labelAr?s.labelAr:t(s.label)}}</button></details></nav></template>
