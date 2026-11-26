<script setup lang="ts">
import {ref,computed,watch,onMounted} from 'vue'
import {useRoute} from 'vue-router'
import {api,allows,requestKey,type Workspace} from './api'
import {locale, message, t} from './i18n'
import FieldHint from './FieldHint.vue'
import TableToolbar from './TableToolbar.vue'
const props=defineProps<{workspace:Workspace}>()
const route=useRoute()
const l=(en:string,ar:string)=>locale.value==='ar'?ar:en
type Choice={id:number;name:string}
type Options={employees:Choice[];projects:Choice[];centers:Choice[];currency:string}
type Line={id?:number;gross?:string;category:string;date:string;description:string;net:string;tax:string;recoverable:boolean;receipt?:boolean}
type Payment={id:number;reference:string;date:string;amount:string;entry_id:number}
type Claim={actions?:string[];id?:number;employee_id:number|null;employee?:string;site_id:number;project_id:number|null;cost_center_id:number|null;currency:string;method:string;status:string;revision:number;gross?:string;balance_due?:string;amount_paid?:string;entry_id?:number;lines:Line[];settlements?:Payment[];review_note?:string}
const labels:Record<string,[string,string]>={
 site:['Site','الموقع'],employee:['Employee','الموظف'],project:['Project','المشروع'],center:['Cost center','مركز التكلفة'],currency:['Currency','العملة'],method:['Paid by','جهة الدفع'],category:['Category','الفئة'],date:['Expense date','تاريخ المصروف'],description:['Description','الوصف'],net:['Net','الصافي'],tax:['Tax','الضريبة'],recoverable:['Recoverable tax','ضريبة قابلة للاسترداد'],file:['Receipt','الإيصال'],posting_date:['Posting date','تاريخ الترحيل'],reference:['Bank / cash reference','مرجع البنك أو الصندوق'],amount:['Payment amount','مبلغ الدفعة'],reason:['Reason for rejection','سبب الرفض'],
 travel:['Travel','تنقلات'],meals:['Meals','وجبات'],lodging:['Lodging','إقامة'],supplies:['Supplies','لوازم'],other:['Other','أخرى'],out_of_pocket:['Employee','الموظف'],company_paid:['Company','الشركة'],draft:['Draft','مسودة'],submitted:['Submitted','بانتظار الاعتماد'],approved:['Approved','معتمدة'],rejected:['Rejected','مرفوضة'],posted:['Posted','مرحّلة'],partially_reimbursed:['Partially reimbursed','مسددة جزئياً'],reimbursed:['Reimbursed','مسددة'],cancelled:['Cancelled','ملغاة'],submit:['Submit','إرسال'],approve:['Approve','اعتماد'],reject:['Reject','رفض'],post:['Post to ledger','ترحيل للأستاذ'],reimburse:['Record reimbursement','تسجيل دفعة للموظف'],cancel:['Cancel claim','إلغاء المطالبة']}
const label=(k:string)=>labels[k]?l(...labels[k]):k
const today=()=>{const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10)}
const line=():Line=>({category:'travel',date:today(),description:'',net:'',tax:'0.00',recoverable:false})
const fields=['category','date','description','net','tax','recoverable']
const root='/expenses/claims',sites=computed(()=>props.workspace.sites.filter(s=>s.active&&allows(props.workspace,'expenses.view',s.id)))
const site=ref(sites.value[0]?.id||0),page=ref(1),size=ref(25),q=ref(''),count=ref(0),rows=ref<Claim[]>([]),row=ref<Claim>(),edit=ref(false),busy=ref(false),error=ref(''),note=ref('')
const opts=ref<Options>({employees:[],centers:[],projects:[],currency:''})
const posting=ref(today()),amount=ref(''),reference=ref(''),reason=ref('')
const can=(p:string)=>allows(props.workspace,'expenses.'+p,row.value?.site_id||site.value)
const payloadKey=new Map<string,string>()
function key(signature:string){if(!payloadKey.has(signature))payloadKey.set(signature,requestKey());return payloadKey.get(signature)!}
let seq=0
async function load(){
 const tick=++seq
 try{const data=await api<{results:Claim[];count:number}>(root+'?'+new URLSearchParams({site:String(site.value),page:String(page.value),page_size:String(size.value),q:q.value}));if(seq===tick){rows.value=data.results;count.value=data.count}}
 catch(e){if(tick===seq)error.value=message(e)}
}
watch([page,size,q,site],load,{immediate:true})
watch(site,()=>{row.value=undefined;page.value=1})
async function job(fn:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value=note.value='';try{await fn()}catch(e){error.value=message(e)}finally{busy.value=false}}
async function choices(siteId:number){opts.value=await api<Options>('/expenses/choices?site='+siteId)}
function fresh(){void job(async()=>{await choices(site.value);row.value={employee_id:opts.value.employees[0]?.id||null,site_id:site.value,project_id:null,cost_center_id:null,currency:opts.value.currency,method:'out_of_pocket',status:'draft',revision:0,lines:[line()]};edit.value=true})}
async function open(id:number){await job(async()=>{row.value=await api<Claim>(root+'/'+id);edit.value=false;reference.value=reason.value=amount.value='';posting.value=today()})}
onMounted(()=>{if(/^\d+$/.test(String(route.query.claim)))void open(Number(route.query.claim))})
function editDraft(){void job(async()=>{await choices(row.value!.site_id);edit.value=true})}
function save(){void job(async()=>{const r=row.value!,url=root+(r.id?'/'+r.id:'');const sig=url+JSON.stringify(r);row.value=await api<Claim>(url,r.id?'PATCH':'POST',r,key(sig));payloadKey.delete(sig);edit.value=false;note.value=t('Draft saved');await load()})}
const actions=computed(()=>row.value?.actions||[])
function advance(a:string){void job(async()=>{const r=row.value!,url=root+'/'+r.id+'/'+a;const data={revision:r.revision,posting_date:posting.value,amount:amount.value,reference:reference.value,reason:reason.value};const sig=url+JSON.stringify(data);row.value=await api<Claim>(url,'POST',data,key(sig));payloadKey.delete(sig);note.value=t('Claim updated');amount.value=reference.value=reason.value='';await load()})}
function upload(x:Line,e:Event){const file=(e.target as HTMLInputElement).files?.[0];if(!file)return;void job(async()=>{const r=row.value!,url=root+'/'+r.id+'/lines/'+x.id+'/receipt';const data=new FormData();data.set('file',file);data.set('revision',String(r.revision));row.value=await api<Claim>(url,'POST',data,key(url+':'+r.revision+':'+file.name+':'+file.lastModified));note.value=t('Receipt attached')})}
</script>
<template><section class="stack exp-page">
<header class="actions"><h1>{{t('Employee expenses')}}</h1><button v-if="row" @click="row=undefined;edit=false">{{t('Claims directory')}}</button><button v-else-if="can('create')" class="primary" :disabled="busy" @click="fresh">{{t('New expense claim')}}</button></header>
<p v-if="error" class="alert" role="alert">{{error}}</p><p v-if="note" role="status">{{note}}</p>
<template v-if="!row"><label>{{label('site')}} <FieldHint :label="label('site')" hint-key="expense.site"/><select v-model.number="site"><option v-for="s in sites" :value="s.id">{{s.name}}</option></select></label>
<TableToolbar :page="page" :size="size" :count="count" :query="q" @page="page=$event" @size="size=$event;page=1" @search="q=$event;page=1"/>
<div class="table-scroll"><table><thead><tr><th>{{t('Claim')}}</th><th>{{label('employee')}}</th><th>{{t('Status')}}</th><th>{{t('Gross')}}</th><th>{{t('Balance')}}</th></tr></thead><tbody><tr v-for="r in rows" :key="r.id"><td><button @click="open(r.id!)">EC-{{r.id}}</button></td><td>{{r.employee}}</td><td>{{label(r.status)}}</td><td>{{r.gross}} {{r.currency}}</td><td>{{r.balance_due}}</td></tr></tbody></table></div></template>
<form v-else-if="edit" class="panel stack" v-validated-form @submit.prevent="save">
<div class="exp-grid"><template v-for="f in ['employee','project','center','method','currency']"><EntityAutocomplete v-if="f==='employee'" :key="f" :label="label(f)" lookup="employees" :choices="opts.employees" :disabled="!!row.id" :empty-value="undefined" required data-field="employee_id" v-model="row.employee_id" /><EntityAutocomplete v-else-if="f==='project'" :key="f+'-p'" :label="label(f)" lookup="projects" :choices="opts.projects" :placeholder="'—'" :empty-value="null" v-model="row.project_id" /><EntityAutocomplete v-else-if="f==='center'" :key="f+'-c'" :label="label(f)" lookup="cost_centers" :choices="opts.centers" :placeholder="'—'" :empty-value="null" v-model="row.cost_center_id" /><label v-else :key="f+'-rest'"><span>{{label(f)}} <FieldHint :label="label(f)" :hint-key="'expense.'+f" :required="['method','currency'].includes(f)"/></span>
<select v-if="f==='method'" v-model="row.method"><option v-for="m in ['out_of_pocket','company_paid']" :value="m">{{label(m)}}</option></select><input v-else readonly :value="row.currency"></label></template></div>
<fieldset v-for="(x,i) in row.lines" :key="x.id||i" class="panel"><legend>{{t('Expense line')}} {{i+1}}</legend>
<div class="exp-grid"><label v-for="f in fields"><span>{{label(f)}} <FieldHint :label="label(f)" :hint-key="'expense.'+f" :required="['date','description','net','category'].includes(f)"/></span>
<select v-if="f==='category'" v-model="x.category"><option v-for="k in ['travel','meals','lodging','supplies','other']" :value="k">{{label(k)}}</option></select>
<input v-else-if="f==='date'" v-model="x.date" type="date" required :data-field="'lines.'+i+'.date'"><input v-else-if="f==='description'" v-model="x.description" required maxlength="240" :data-field="'lines.'+i+'.description'">
<input v-else-if="f==='net'||f==='tax'" v-model="x[f]" inputmode="decimal" required :data-field="'lines.'+i+'.'+f"><input v-else v-model="x.recoverable" type="checkbox"></label></div><button v-if="row.lines.length>1&&!x.receipt" type="button" @click="row.lines.splice(i,1)">{{t('Remove line')}}</button></fieldset>
<div class="actions"><button type="button" @click="row.lines.push(line())">{{t('Add line')}}</button><button class="primary" :disabled="busy">{{t('Save draft')}}</button></div></form>
<template v-else><header class="panel"><h2>EC-{{row.id}} · {{row.employee}}</h2><p>{{label(row.status)}} · {{label(row.method)}} · {{t('Total')}} {{row.gross}} {{row.currency}} · {{t('Paid to employee')}} {{row.amount_paid}} · {{t('Balance')}} {{row.balance_due}}</p><p v-if="row.entry_id">JE-{{row.entry_id}}</p><p v-if="row.review_note">{{row.review_note}}</p></header>
<section v-for="x in row.lines" :key="x.id" class="panel"><strong>{{label(x.category)}} · {{x.description}}</strong><p>{{x.date}} · {{label('net')}} {{x.net}} + {{label('tax')}} {{x.tax}} = {{x.gross}} {{row.currency}}</p><a v-if="x.receipt" :href="'/api'+root+'/'+row.id+'/lines/'+x.id+'/receipt'">{{t('Download receipt')}}</a><label v-else-if="row.status==='draft'&&actions.includes('submit')">{{label('file')}} <FieldHint :label="label('file')" hint-key="expense.file"/><input :disabled="busy" type="file" accept="application/pdf,image/png,image/jpeg,image/webp" @change="upload(x,$event)"></label></section>
<section class="panel stack" v-if="actions.length"><button v-if="row.status==='draft'&&actions.includes('submit')" @click="editDraft">{{t('Edit draft')}}</button>
<div class="exp-grid"><label v-for="f in [...(actions.includes('post')||actions.includes('reimburse')?['posting_date']:[]),...(actions.includes('reimburse')?['reference','amount']:[]),...(actions.includes('reject')?['reason']:[])]">{{label(f)}} <FieldHint :label="label(f)" :hint-key="'expense.'+f"/><input v-if="f==='posting_date'" type="date" v-model="posting"><input v-else-if="f==='reference'" v-model="reference"><input v-else-if="f==='amount'" v-model="amount" inputmode="decimal"><input v-else v-model="reason"></label></div>
<div class="actions"><button v-for="a in actions" :disabled="busy" @click="advance(a)">{{label(a)}}</button></div></section>
<section v-if="row.settlements?.length" class="panel"><h3>{{t('Settlements')}}</h3><p v-for="p in row.settlements" :key="p.id">#{{p.id}} · {{p.date}} · {{p.reference}} · {{p.amount}} {{row.currency}} · JE-{{p.entry_id}}</p></section></template></section></template>
<style scoped>.exp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:1rem}.exp-page label{position:relative}.exp-page :deep(.field-hint){position:relative;display:inline-flex;inset:auto}.exp-page input[type=checkbox]{width:auto}</style>
