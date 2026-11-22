<script setup lang="ts">
import {computed, reactive, ref, watch} from 'vue'
import {api, allows, createRequest, type Workspace} from '../../../frontend/src/api'
import {locale,message,t} from '../../../frontend/src/i18n'
import GLSettings from './GLSettings.vue'
import TableToolbar from '../../../frontend/src/TableToolbar.vue'
import FieldHint from '../../../frontend/src/FieldHint.vue'
const props=defineProps<{workspace:Workspace}>()
type Row=Record<string,any>
const labels:Record<string,[string,string]>={
 gl_status:['GL status','حالة الترحيل'],posted:['Posted','مرحّل'],awaiting_gl:['Awaiting GL processing','بانتظار الترحيل'],subscription_gl_mapping_missing:['Set up site GL accounts','يلزم تحديد حسابات الموقع'],finance_period_missing:['Accounting period missing','الفترة المحاسبية غير موجودة'],finance_period_closed:['Accounting period closed','الفترة المحاسبية مغلقة'],
 contracts:['Contracts','العقود'],plans:['Plans','الخطط'],cycles:['Billing cycles','دورات الفوترة'],notices:['Reminders','التذكيرات'],requests:['Cancellation requests','طلبات الإلغاء'],policy:['Site collection policy','سياسة التحصيل بالموقع'],
 code:['Plan code','كود الخطة'],name_en:['English name','الاسم الإنجليزي'],name_ar:['Arabic name','الاسم العربي'],price:['Price before tax','السعر قبل الضريبة'],tax:['Tax (%)','الضريبة (%)'],setup:['Setup fee','رسم التأسيس'],trial_days:['Trial days','أيام التجربة'],interval:['Billing interval','دورية الفوترة'],days:['Interval in days','الفترة بالأيام'],version_id:['Plan version','نسخة الخطة'],party_id:['Customer','العميل'],start:['Start','البداية'],end:['End','النهاية'],due:['Due date','الاستحقاق'],status:['Status','الحالة'],next_date:['Next billing date','الفوترة التالية'],cancel_on:['Cancellation date','تاريخ الإلغاء'],id:['ID','المعرف'],revision:['Revision','المراجعة'],contract_id:['Contract','العقد'],stage:['Notice stage','مرحلة الإشعار'],delivered_at:['Sent at','وقت الإرسال'],reason:['Reason','السبب'],credit_used:['Credit applied','الرصيد المستخدم'],invoice__number:['Invoice','الفاتورة'],invoice__total:['Billed total','إجمالي الفاتورة'],invoice__paid:['Paid','المدفوع'],version__plan__code:['Plan','الخطة'],party__name_en:['Party','الطرف'],party__name_ar:['Party','الطرف'],active:['Active','نشط'],first:['First notice (days overdue)','الإشعار الأول (أيام التأخر)'],second:['Second notice','الإشعار الثاني'],final:['Final notice','الإشعار النهائي'],suspend:['Suspend','تعليق'],resume:['Resume','استئناف'],cancel:['Cancel contract','إلغاء العقد'],bill:['Bill due cycle','فوترة الدورة المستحقة'],'change-plan':['Change plan','تغيير الخطة'],approve:['Approve','اعتماد'],reject:['Reject','رفض'],monthly:['Monthly','شهري'],quarterly:['Quarterly','ربع سنوي'],annual:['Annual','سنوي'],custom:['Custom days','أيام مخصصة'],trial:['Trial','تجربة'],past_due:['Past due','متأخر'],suspended:['Suspended','معلق'],cancelled:['Cancelled','ملغى'],pending:['Pending','قيد المراجعة'],approved:['Approved','معتمد'],rejected:['Rejected','مرفوض']}
const l=(en:string,ar:string)=>locale.value==='ar'?ar:en
const label=(k:string)=>labels[k]?.[locale.value==='ar'?1:0]??k
const tab=ref('contracts'),site=ref(props.workspace.sites.find(s=>s.active&&allows(props.workspace,'subscriptions.view',s.id))?.id),space=ref<number|undefined>(),spaces=ref<Row[]>([])
const rows=ref<Row[]>([]),versions=ref<Row[]>([]),parties=ref<Row[]>([]),count=ref(0),page=ref(1),size=ref(25),q=ref(''),mode=ref(''),selected=ref<Row>(),busy=ref(false),error=ref(''),ok=ref(false)
const f=reactive<Row>({}),send=createRequest(),scope=computed(()=>({site_id:site.value,space_id:space.value??null}))
const permit=(x:string)=>allows(props.workspace,'subscriptions.'+x,site.value)
const tabs=['contracts','plans','cycles','notices','requests','policy']
async function query(kind:string,p=1,sz=50,search=''){return api<Row>('/subscriptions/'+kind+'?'+new URLSearchParams({site_id:String(site.value),space_id:String(space.value??''),page:String(p),page_size:String(sz),q:search}))}
async function choices(kind:string){let all:Row[]=[];let n=1;while(n){const data=await query(kind,n);all=all.concat(data.results);n=data.next}return all}
let seq=0
async function load(){const id=++seq;if(!site.value||tab.value==='gl-settings')return
 try{const result=await query(tab.value,page.value,size.value,q.value);if(id!==seq)return
 if(tab.value==='policy'){Object.assign(f,result);rows.value=[]}else{rows.value=result.results;count.value=result.count}
 }catch(e){if(id===seq){rows.value=[];error.value=t(message(e))}}}
watch([site,space,tab,page,size,q],()=>{mode.value='';error.value='';void load()},{immediate:true})
watch(site,async()=>{space.value=undefined;spaces.value=site.value?await choices('spaces'):[]},{immediate:true})
async function open(kind:string,row?:Row){try{error.value='';ok.value=false;selected.value=row;mode.value=kind;Object.keys(f).forEach(k=>delete f[k]);Object.assign(f,{price:'',setup:'0',tax:'0',trial_days:0,days:30,interval:'monthly'})
 if(kind==='contracts'||kind==='change-plan'){versions.value=await choices('versions');if(kind==='contracts')parties.value=await choices('parties')}
 }catch(e){error.value=t(message(e))}}
const estimate=ref<Row>()
async function preview(id:number){try{const scopeQuery=new URLSearchParams({site_id:String(site.value),space_id:String(space.value??'')});estimate.value=await api<Row>('/subscriptions/contracts/'+id+'/billing-preview?'+scopeQuery)}catch(e){error.value=t(message(e))}}
watch([site,space,tab],()=>{estimate.value=undefined})
const fields=computed(()=>mode.value==='contracts'?['party_id','version_id','start']:mode.value==='change-plan'?['version_id']:mode.value==='plans'||mode.value==='versions'?[...(mode.value==='plans'?['code','name_en','name_ar']:[]),'price','tax','setup','trial_days','interval',...(f.interval==='custom'?['days']:[])]:tab.value==='policy'?['first','second','final','suspend']:[])
async function save(action:string,row?:Row){if(busy.value)return;busy.value=true;error.value='';ok.value=false
 try{const isAction=['change-plan','bill','cancel','suspend','resume','approve','reject'].includes(action)
 const r=row??selected.value;const payload={...scope.value,...(mode.value||action==='policy'?f:{}),...(r?{revision:r.contract__revision??r.revision,request_id:r.id,plan_id:r.id}:{})}
 if(['suspend','cancel','reject','approve'].includes(action)&&!confirm(label(action)+'?'))return
 await send('/subscriptions/'+(isAction?'contracts/'+(r?.contract_id??r?.id)+'/'+action:action),payload);mode.value='';ok.value=true;await load()
  }catch(e){const code=e instanceof Error?e.message:message(e);error.value=code==='cycle_not_due'?l('Cycle is not due. Next billing date: ','لم يحِن موعد الدورة. تاريخ الفوترة القادم: ')+(row?.next_date??''):t(message(e))+' ('+code+')'}finally{busy.value=false}}
const cols=computed(()=>Object.keys(rows.value[0]??{}).filter(k=>!['can_bill','revision','contract__revision',locale.value==='ar'?'party__name_en':'party__name_ar'].includes(k)))
</script>
<template><main class="stack"><h1>{{l('Subscriptions','الاشتراكات')}}</h1>
<div class="panel"><label>{{l('Site','الموقع')}} <FieldHint :label="l('Site','الموقع')"/><select v-model="site"><option v-for="s in workspace.sites.filter(x=>x.active&&allows(workspace,'subscriptions.view',x.id))" :value="s.id">{{s.name}}</option></select></label>
<label>{{l('Public site','موقع النشر')}} <FieldHint :label="l('Public site','موقع النشر')"/><select v-model="space"><option :value="undefined">{{l('Internal','داخلي')}}</option><option v-for="s in spaces" :value="s.id">{{s.name}}</option></select></label></div>
<nav class="actions"><button v-for="k in tabs" :aria-pressed="tab===k" @click="tab=k;page=1;q='';selected=undefined">{{label(k)}}</button><button v-if="allows(workspace,'accounting.post',site)" @click="tab='gl-settings'">{{l('GL accounts','حسابات الأستاذ')}}</button></nav>
<GLSettings v-if="site&&tab==='gl-settings'" :key="String(site)+'-'+String(space)" :site="site" :space="space" :editable="permit('override')"/>
<p v-if="estimate" class="panel">{{l('Next cycle estimate','معاينة الدورة التالية')}} · {{estimate.start}} → {{estimate.end}} · {{estimate.total}} {{estimate.currency}} · {{l('Credit used','الرصيد المستخدم')}} {{estimate.credit_used}}</p>
<p v-if="error" role="alert">{{error}}</p><p v-if="ok" role="status">{{l('Saved','تم الحفظ')}}</p>
<button v-if="!mode&&['plans','contracts'].includes(tab)&&permit('manage')" @click="open(tab)">+ {{label(tab)}}</button>
<form v-validated-form v-if="mode||tab==='policy'" class="panel record-form" @submit.prevent="save(mode||'policy')">
<label v-for="k in fields"><span>{{label(k)}} <FieldHint  :hint-key="'subscriptions.'+k" :label="label(k)" :required="true"/></span>
<select v-if="k==='party_id'" v-model="f[k]" required><option v-for="p in parties" :value="p.id">{{locale==='ar'?p.name_ar:p.name_en}}</option></select>
<select v-else-if="k==='version_id'" v-model="f[k]" required><option v-for="v in versions" :value="v.id">{{v.plan__code}} · {{v.version}} · {{v.price}} {{v.currency}}</option></select>
<select v-else-if="k==='interval'" v-model="f[k]"><option v-for="v in ['monthly','quarterly','annual','custom']" :value="v">{{label(v)}}</option></select>
<input :data-field="k" v-else-if="['first','second','final','suspend','days','trial_days'].includes(k)" type="number" min="0" v-model.number="f[k]" required>
<input v-else :data-field="k" v-model="f[k]" :type="k==='start'?'date':'text'" required></label>
<button v-if="tab!=='policy'||permit('override')" :disabled="busy">{{l('Save','حفظ')}}</button><button v-if="mode" type="button" @click="mode=''">{{l('Back','رجوع')}}</button></form>
<template v-if="!['policy','gl-settings'].includes(tab)"><TableToolbar :page="page" :count="count" :size="size" :query="q" @page="page=$event" @size="size=$event;page=1" @search="q=$event;page=1"/>
<div style="overflow:auto"><table><thead><tr><th v-for="k in cols">{{label(k)}}</th><th>{{l('Actions','الإجراءات')}}</th></tr></thead><tbody>
<tr v-for="r in rows" :key="r.id"><td v-for="k in cols"><bdi v-if="k==='version__plan__code'||k==='code'" dir="ltr" style="white-space:nowrap">{{r[k]}}</bdi><template v-else>{{['status','gl_status'].includes(k)?label(r[k]):r[k]??'—'}}</template></td><td>
<button v-if="tab==='plans'&&permit('manage')" @click="open('versions',r)">{{l('New version','نسخة جديدة')}}</button>
<template v-if="tab==='contracts'&&r.status!=='cancelled'"><button @click="preview(r.id)">{{l('Preview','معاينة')}}</button><button v-if="permit('manage')" @click="open('change-plan',r)">{{label('change-plan')}}</button><button v-if="permit('bill')&&r.status!=='suspended'" :disabled="!r.can_bill||busy" :title="r.can_bill?'':l('Cycle is not due','لم يحِن موعد الدورة')" @click="save('bill',r)">{{label('bill')}}</button><template v-if="permit('override')"><button v-for="a in [r.status==='suspended'?'resume':'suspend','cancel']" @click="save(a,r)">{{label(a)}}</button></template></template>
<template v-if="tab==='requests'&&r.status==='pending'&&permit('override')"><button v-for="a in ['approve','reject']" @click="save(a,r)">{{label(a)}}</button></template></td></tr></tbody></table></div></template></main></template>
