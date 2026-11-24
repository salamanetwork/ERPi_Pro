<script setup lang="ts">
import {computed,reactive,ref,watch} from 'vue'
import {api,allows,requestKey,ApiError,type Workspace} from '../../../frontend/src/api'
import {locale} from '../../../frontend/src/i18n'
import TableToolbar from '../../../frontend/src/TableToolbar.vue'
import FieldHint from '../../../frontend/src/FieldHint.vue'
import EntityAutocomplete from '../../../frontend/src/EntityAutocomplete.vue'

const props=defineProps<{workspace:Workspace}>()
const l=(en:string,ar:string)=>locale.value==='ar'?ar:en
const titles:Record<string,[string,string]>={
 site:['Site','الموقع'],profile:['Bank profile','ملف البنك'],name:['Bank name','اسم البنك'],last_four:['Last four characters','آخر أربعة أحرف أو أرقام'],account_id:['Bank or cash GL account','حساب البنك أو الصندوق'],charges_id:['Bank charges account','حساب الرسوم'],interest_id:['Interest income account','حساب إيراد الفوائد'],tolerance:['Suggestion / rounding tolerance','سماحية الاقتراح والتقريب'],start:['Start date','بداية الفترة'],end:['End date','نهاية الفترة'],opening:['Opening balance','رصيد الافتتاح'],closing:['Statement closing','رصيد الإقفال بالكشف'],date:['Transaction date','تاريخ الحركة'],description:['Narration','البيان'],reference:['Reference','المرجع'],amount:['Signed amount','المبلغ بإشارته'],running_balance:['Running balance','الرصيد الجاري'],debit:['Bank debit (out)','مدين البنك (صادر)'],credit:['Bank credit (in)','دائن البنك (وارد)'],file:['CSV file','ملف CSV'],attach:['Keep original file','حفظ الملف الأصلي'],draft:['Draft','مسودة'],in_progress:['In progress','قيد التسوية'],reconciled:['Reconciled','تمت التسوية'],cleared:['Cleared movement','صافي الحركات المطابقة'],difference:['Unreconciled difference','الفرق غير المطابق'],integrity_difference:['Statement arithmetic difference','فرق اتزان الكشف'],computed_ending_balance:['Computed closing','رصيد الإقفال المحسوب'],fee:['Bank fee','رسوم بنكية'],interest:['Interest','فائدة'],rounding:['Rounding difference','فرق تقريب']}
const label=(k:string)=>titles[k]?l(...titles[k]):k
type Row=Record<string,any>
const base='/bank_reconciliation',sites=computed(()=>props.workspace.sites.filter(s=>s.active&&allows(props.workspace,'bank_reconciliation.view',s.id)))
const site=ref(sites.value[0]?.id||0),can=(p:string)=>allows(props.workspace,'bank_reconciliation.'+p,site.value)
const mainTab=ref<'statements'|'profiles'>('statements')
const profileView=ref<'list'|'grid'>('list')
const profileSearch=ref('')
const profiles=ref<Row[]>([]),accounts=ref<Row[]>([]),profile=ref<number|null>(null),rows=ref<Row[]>([]),s=ref<Row>(),gl=ref<Row[]>([])
const page=ref(1),size=ref(25),q=ref(''),count=ref(0),bp=ref(1),gp=ref(1),bs=ref(10),gs=ref(10),bq=ref(''),gq=ref(''),gc=ref(0)
const bankIds=ref<number[]>([]),glIds=ref<number[]>([]),busy=ref(false),error=ref(''),mode=ref(''),kind=ref('fee')
const form=reactive<Row>({name:'',last_four:'',account_id:0,charges_id:0,interest_id:0,tolerance:'0.00',start:'',end:'',opening:'0.00',closing:'0.00',date:'',description:'',reference:'',amount:'',running_balance:''})
const active=computed(()=>s.value?.status==='in_progress'),editable=computed(()=>s.value&&s.value.status!=='reconciled')
const file=ref<File>(),columns=ref<string[]>([]),mapping=reactive<Record<string,string>>({}),preview=ref<Row[]>([]),attach=ref(true)
const mapFields=['date','description','reference','amount','debit','credit','running_balance']
const fileInput=ref<HTMLInputElement>()
const statementURL=()=>base+'/statements/'+s.value!.id
const keys=new Map<string,string>()

const profileChoices=computed(()=>profiles.value.map(p=>({
  id:p.id,
  code:`••••${p.last_four}`,
  name:`${p.name} · ${p.currency}`,
})))

const filteredProfiles=computed(()=>{
  const term=profileSearch.value.trim().toLowerCase()
  if(!term)return profiles.value
  return profiles.value.filter(p=>`${p.name} ${p.last_four} ${p.currency}`.toLowerCase().includes(term))
})

async function send(path:string,data:Row|FormData,tag=''){
 const sig=path+(tag||JSON.stringify(data));if(!keys.has(sig))keys.set(sig,requestKey())
 const response=await api<Row>(path,'POST',data,keys.get(sig));keys.delete(sig);return response
}
async function run(fn:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';try{await fn()}catch(e){report(e)}finally{busy.value=false}}
let serial=0,detailTick=0,candidateTick=0
async function directory(){
 const n=++serial,id=site.value;if(!id)return
 const [a,b]=await Promise.all([api<Row>(base+'/profiles?site='+id),api<Row>(base+'/statements?'+new URLSearchParams({site:String(id),q:q.value,page:String(page.value),page_size:String(size.value)}))])
 if(n!==serial)return
 profiles.value=a.profiles;accounts.value=a.accounts;rows.value=b.results;count.value=b.count
 if(!profiles.value.some(p=>p.id===profile.value))profile.value=profiles.value[0]?.id||null
}
const issues:Record<string,[string,string]>={
 revision_conflict:['The statement changed or is closed. Reopen it to load the latest revision.','تغير الكشف أو أُقفل. افتحه مجدداً لتحميل أحدث إصدار.'],
 match_total_mismatch:['Select equal totals with a single row on at least one side.','اختر مجموعين متساويين مع سطر واحد على الأقل في أحد الجانبين.'],
 statement_not_cleared:['Clear every statement line and both differences before closing.','طابق كل الحركات وصفّر الفرقين قبل الإقفال.'],
 duplicate_import:['This statement file has already been imported.','سبق استيراد هذا الملف إلى الكشف.'],
 running_balance_mismatch:['The running balance differs from opening plus movements.','الرصيد الجاري مختلف عن مجموع الافتتاح والحركات.'],
 invalid_column_mapping:['Map date, narration and either signed amount or debit / credit.','اربط التاريخ والبيان والمبلغ بإشارته أو المدين والدائن.'],
 invalid_continuity:['Check the last reconciled date and balance before opening the next statement.','راجع آخر رصيد وتاريخ إقفال قبل فتح الكشف التالي.'],
 open_statement_exists:['Close the existing statement for this bank profile first.','أقفل الكشف المفتوح لهذا الملف البنكي أولاً.'],
 profile_exists:['This site already has a profile for that account.','يوجد بالفعل ملف لهذا الحساب في الموقع.'],
 date_outside_statement:['Use a date in the period and enter lines in chronological order.','أدخل تاريخاً ضمن الفترة ورتب الحركات زمنياً.'],
 outside_tolerance:['The residual exceeds the configured rounding tolerance or is zero.','الفرق يتجاوز سماحية التقريب أو يساوي صفراً.'],
 finance_period_closed:['The accounting period is closed.','الفترة المحاسبية مغلقة.'],
 line_unavailable:['A selected line is unavailable. Reload the statement.','أحد السطور المحددة غير متاح. أعد فتح الكشف.']}
function failure(e:unknown){
 const pair=e instanceof ApiError?issues[e.code]:undefined
 const text=pair?l(...pair):l('Could not complete the operation. Check the inputs and permissions.','تعذر إتمام العملية. راجع المدخلات والصلاحيات.')
 const fields=e instanceof ApiError?Object.keys(e.fieldErrors).map(k=>k.split('.').map(x=>x==='rows'?l('Row','السطر'):label(x)).join(' · ')):[]
 return fields.length?text+' ('+fields.join(', ')+')':text
}
function report(e:unknown){error.value=failure(e)}
watch([site,page,size,q],()=>directory().catch(report),{immediate:true})
watch(site,()=>{s.value=undefined;mode.value='';page.value=1})
function clearCSV(){columns.value=[];preview.value=[];file.value=undefined;Object.keys(mapping).forEach(k=>delete mapping[k]);if(fileInput.value)fileInput.value.value=''}
function back(){s.value=undefined;mode.value='';clearCSV();void directory().catch(report)}
async function reload(id:number){
 const tick=++detailTick
 const row=await api<Row>(base+'/statements/'+id+'?'+new URLSearchParams({q:bq.value,page:String(bp.value),page_size:String(bs.value)}))
 if(tick===detailTick&&s.value?.id===id)s.value=row
}
async function candidates(){
 const tick=++candidateTick;if(!s.value)return
 const id=s.value.id
 const params=new URLSearchParams({q:gq.value,page:String(gp.value),page_size:String(gs.value)})
 if(bankIds.value.length===1)params.set('line',String(bankIds.value[0]))
 const r=await api<Row>(statementURL()+'/candidates?'+params)
 if(tick===candidateTick&&s.value?.id===id){gl.value=r.results;gc.value=r.count}
}
watch([gp,gs,gq,bankIds],()=>candidates().catch(report))
watch([bp,bs,bq],()=>{if(s.value)void reload(s.value.id).catch(report)})
async function open(id:number){s.value={id};bankIds.value=[];glIds.value=[];bp.value=gp.value=1;bq.value=gq.value='';mode.value='';clearCSV();await reload(id);await candidates()}
function setMode(m:string){mode.value=mode.value===m?'':m;if(m==='statement'){form.opening=profiles.value.find(x=>x.id===profile.value)?.last_balance||'0.00'}if(m==='edit')form.closing=s.value?.closing}
function saveProfile(){return run(async()=>{const r=await send(base+'/profiles',{...form,site_id:site.value});profile.value=r.id;mode.value='';await directory()})}
function saveStatement(){return run(async()=>{const r=await send(base+'/statements',{profile_id:profile.value,start:form.start,end:form.end,opening:form.opening,closing:form.closing});await open(r.id)})}
async function changed(){bankIds.value=[];glIds.value=[];mode.value='';await reload(s.value!.id);await candidates()}
function action(a:string,data:Row={}){return run(async()=>{await send(statementURL()+'/'+a,{revision:s.value!.revision,...data});await changed()})}
function csvData(){const d=new FormData();d.set('file',file.value!);d.set('revision',String(s.value!.revision));d.set('attach',String(attach.value));if(columns.value.length)d.set('mapping',JSON.stringify(Object.fromEntries(Object.entries(mapping).filter(([,v])=>v))));return d}
function pick(e:Event){const chosen=(e.target as HTMLInputElement).files?.[0];if(!chosen)return;columns.value=[];preview.value=[];file.value=chosen;void run(async()=>{const r=await api<Row>(statementURL()+'/preview','POST',csvData());columns.value=r.columns;mapFields.forEach(k=>mapping[k]=r.columns.find((x:string)=>x.trim().toLowerCase()===k)||'')})}
watch(mapping,()=>preview.value=[])
function review(){return run(async()=>{preview.value=(await api<Row>(statementURL()+'/preview','POST',csvData())).preview})}
function importRows(){return run(async()=>{await send(statementURL()+'/import_csv',csvData(),JSON.stringify([s.value!.revision,file.value!.name,file.value!.size,file.value!.lastModified,mapping,attach.value]));clearCSV();await changed()})}
</script>
<template><section class="stack bank">
<header class="actions" style="margin-block-end: 12px;">
  <h1>{{l('Bank reconciliation','التسوية البنكية')}}</h1>
  <button v-if="s" class="secondary" @click="back">{{l('Statements directory','قائمة الكشوف')}}</button>
</header>
<p v-if="error" class="alert" role="alert">{{error}}</p>

<template v-if="!s">
  <div class="panel" style="padding: 14px 16px; margin-block-end: 16px;">
    <label style="max-inline-size: 460px;">
      <span>{{label('site')}} <FieldHint :label="label('site')" hint-key="bank.site"/></span>
      <select v-model.number="site"><option v-for="x in sites" :value="x.id">{{x.name}}</option></select>
    </label>
  </div>

  <nav class="section-tabs">
    <button type="button" class="tab-link" :aria-current="mainTab==='statements'?'page':undefined" @click="mainTab='statements'; mode=''">
      {{l('Statements & Reconciliation','الكشوف والتسوية')}}
    </button>
    <button type="button" class="tab-link" :aria-current="mainTab==='profiles'?'page':undefined" @click="mainTab='profiles'; mode=''">
      {{l('Bank profiles','ملفات البنوك')}} ({{profiles.length}})
    </button>
  </nav>

  <!-- TAB 1: Statements & Reconciliation -->
  <template v-if="mainTab==='statements'">
    <div class="panel" style="padding: 16px; margin-block-end: 16px; display: flex; flex-wrap: wrap; align-items: end; gap: 14px;">
      <div style="flex: 1 1 320px; min-inline-size: 0;">
        <EntityAutocomplete :label="label('profile')" :choices="profileChoices" v-model="profile" :empty-value="null" />
      </div>
      <div class="actions">
        <button v-if="profile&&can('import')" class="primary" @click="setMode('statement')">{{l('New statement','كشف جديد')}}</button>
        <button v-if="can('import')" class="secondary" @click="mainTab='profiles'; setMode('profile')">{{l('New bank profile','ملف بنك جديد')}}</button>
      </div>
    </div>

    <form v-validated-form v-if="mode==='statement'" class="panel grid72" style="margin-block-end: 16px;" @submit.prevent="saveStatement()">
      <label v-for="k in ['start','end','opening','closing']" :key="k"><span>{{label(k)}} <FieldHint :label="label(k)" :hint-key="'bank.'+k" required/></span>
      <input required v-model="form[k]" :type="['start','end'].includes(k)?'date':'text'" maxlength="100"></label>
      <button class="primary" :disabled="busy">{{l('Save','حفظ')}}</button>
    </form>

    <section class="panel table-panel">
      <TableToolbar :page="page" :size="size" :count="count" :query="q" @page="page=$event" @size="size=$event;page=1" @search="q=$event;page=1"/>
      <table><thead><tr><th>{{label('profile')}}</th><th>{{label('end')}}</th><th>{{l('Status','الحالة')}}</th><th>{{label('difference')}}</th></tr></thead>
      <tbody><tr v-for="r in rows" :key="r.id"><td><button class="secondary" @click="run(()=>open(r.id))">#{{r.id}} · {{r.bank}}</button></td><td>{{r.end}}</td><td>{{label(r.status)}}</td><td>{{r.difference}} {{r.currency}}</td></tr></tbody></table>
      <p v-if="!rows.length" style="padding: 14px;">{{l('No statements found.','لا توجد كشوف.')}}</p>
    </section>
  </template>

  <!-- TAB 2: Bank Profiles Management (Mega-Scale Directory + List/Grid toggle + Search) -->
  <template v-else>
    <section class="panel record-form">
      <div class="page-heading">
        <h2>{{l('Bank profiles','ملفات البنوك')}}</h2>
        <div class="actions">
          <button type="button" class="secondary" :aria-pressed="profileView==='list'" @click="profileView='list'">{{l('List view','عرض قائمة')}}</button>
          <button type="button" class="secondary" :aria-pressed="profileView==='grid'" @click="profileView='grid'">{{l('Grid view','عرض بطاقات')}}</button>
          <button v-if="can('import')" type="button" class="primary" @click="setMode('profile')">{{l('New bank profile','ملف بنك جديد')}}</button>
        </div>
      </div>

      <form v-validated-form v-if="mode==='profile'" class="panel grid72" style="background: var(--subtle);" @submit.prevent="saveProfile()">
        <label v-for="k in ['name','last_four','account_id','charges_id','interest_id','tolerance']" :key="k"><span>{{label(k)}} <FieldHint :label="label(k)" :hint-key="'bank.'+k" required/></span>
        <select v-if="k.endsWith('_id')" required v-model.number="form[k]"><option :value="0" disabled>—</option><option v-for="a in accounts.filter(x=>x.kind===({account_id:'asset',charges_id:'expense',interest_id:'revenue'} as Row)[k])" :value="a.id">{{a.code}} · {{a.name}}</option></select>
        <input v-else required v-model="form[k]" type="text" :maxlength="k==='last_four'?4:100"></label>
        <button class="primary" :disabled="busy">{{l('Save','حفظ')}}</button>
      </form>

      <div class="actions">
        <input v-model="profileSearch" type="search" :placeholder="l('Search bank name, last 4 digits, currency...','ابحث باسم البنك أو آخر 4 أرقام أو العملة...')" style="max-inline-size: 380px;">
      </div>

      <div v-if="profileView==='list'" class="table-panel">
        <table>
          <thead>
            <tr>
              <th>{{label('name')}}</th>
              <th>{{label('last_four')}}</th>
              <th>{{l('Currency','العملة')}}</th>
              <th>{{l('Actions','الإجراءات')}}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in filteredProfiles" :key="p.id">
              <td><strong>{{p.name}}</strong></td>
              <td><bdi>••••{{p.last_four}}</bdi></td>
              <td>{{p.currency}}</td>
              <td>
                <button type="button" class="secondary" @click="profile=p.id; mainTab='statements'">{{l('Open statements','فتح الكشوف')}}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-else class="card-grid" style="margin-block-end: 0;">
        <div v-for="p in filteredProfiles" :key="p.id" class="panel" style="padding: 14px; display: grid; gap: 8px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <strong>{{p.name}}</strong>
            <span class="badge on">{{p.currency}}</span>
          </div>
          <small class="muted"><bdi>••••{{p.last_four}}</bdi></small>
          <div>
            <button type="button" class="secondary" @click="profile=p.id; mainTab='statements'">{{l('Open statements','فتح الكشوف')}}</button>
          </div>
        </div>
      </div>
    </section>
  </template>
</template>

<template v-else-if="s.lines"><h2>{{s.bank}} · #{{s.id}} <span class="badge">{{label(s.status)}}</span></h2>
<div class="balances"><div v-for="k in ['opening','closing','computed_ending_balance','cleared','integrity_difference','difference']"><small>{{label(k)}}</small><strong><bdi>{{s[k]}} {{s.currency}}</bdi></strong></div></div>
<div class="actions"><template v-if="editable&&can('import')"><button @click="setMode('line')">{{l('Add statement line','إضافة حركة')}}</button><button @click="setMode('csv')">{{l('Import CSV','استيراد CSV')}}</button><button @click="setMode('edit')">{{l('Edit closing balance','تعديل رصيد الإقفال')}}</button></template>
<button v-if="s.status==='draft'&&can('reconcile')" @click="action('start')">{{l('Start reconciliation','بدء التسوية')}}</button><button v-if="active&&can('close')" @click="setMode('close')">{{l('Close statement','إقفال الكشف')}}</button>
<a v-for="f in s.attachments" :href="'/api'+statementURL()+'/attachments/'+f.id" download>{{f.attachment__name}}</a></div>
<div v-if="mode==='close'" class="panel">{{l('Closing permanently locks the statement and its matches.','الإقفال يمنع تعديل الكشف ومطابقاته نهائياً.')}} <button :disabled="busy" @click="action('close')">{{l('Confirm close','تأكيد الإقفال')}}</button></div>
<form v-validated-form v-if="mode==='line'||mode==='edit'" class="panel grid72" @submit.prevent="action(mode,{...form})"><label v-for="k in mode==='line'?['date','description','reference','amount','running_balance']:['closing']"><span>{{label(k)}} <FieldHint :label="label(k)" :hint-key="'bank.'+k" :required="!['running_balance','reference'].includes(k)"/></span><input v-model="form[k]" :type="k==='date'?'date':'text'" :required="!['running_balance','reference'].includes(k)"></label><button :disabled="busy">{{l('Save','حفظ')}}</button></form>
<div v-if="mode==='csv'" class="panel stack"><label>{{label('file')}} <FieldHint :label="label('file')" hint-key="bank.file" required/><input ref="fileInput" type="file" accept=".csv,text/csv" @change="pick"></label>
<div v-if="columns.length" class="grid72"><label v-for="k in mapFields"><span>{{label(k)}} <FieldHint :label="label(k)" :hint-key="'bank.'+k"/></span><select v-model="mapping[k]"><option value="">—</option><option v-for="col in columns">{{col}}</option></select></label></div>
<label><input v-model="attach" type="checkbox"> {{label('attach')}} <FieldHint :label="label('attach')" hint-key="bank.attach"/></label>
<button v-if="columns.length" @click="review">{{l('Validate and preview','تحقق ومعاينة')}}</button><template v-if="preview.length"><div v-for="r in preview">{{r.date}} · {{r.description}} · <bdi>{{r.amount}}</bdi></div><button :disabled="busy" @click="importRows">{{l('Import all validated rows','استيراد كل السطور المتحقق منها')}}</button></template></div>
<div class="panes"><article class="panel stack"><h3>{{l('Bank statement lines','حركات كشف البنك')}}</h3><TableToolbar :count="s.lines.count" :page="bp" :size="bs" :query="bq" @search="bq=$event;bp=1" @page="bp=$event" @size="bs=$event;bp=1"/>
<div v-for="r in s.lines.results" class="record"><label><input v-if="active&&!r.match_id" v-model="bankIds" type="checkbox" :value="r.id"> {{r.description}} · {{r.reference}}</label><small>{{r.date}} · <bdi>{{r.amount}}</bdi> · {{r.match_id?l('Matched','مطابق'):l('Unmatched','غير مطابق')}}</small><button v-if="active&&r.match_id&&can('reconcile')" @click="action('unmatch',{match_id:r.match_id})">{{l('Unmatch group','فك مطابقة المجموعة')}}</button></div></article>
<article class="panel stack"><h3>{{l('Unreconciled ledger transactions','حركات الأستاذ غير المطابقة')}}</h3><TableToolbar :count="gc" :page="gp" :size="gs" :query="gq" @search="gq=$event;gp=1" @page="gp=$event" @size="gs=$event;gp=1"/>
<div v-for="r in gl" class="record"><label><input v-if="active" v-model="glIds" type="checkbox" :value="r.id"> JE-{{r.entry}} · {{r.reference}} {{r.party}}</label><small>{{r.date}} · <bdi>{{r.amount}}</bdi> · {{r.description}}</small>
<span v-if="r.suggested" class="badge">{{r.exact?l('Suggested · exact amount','مقترح · مبلغ مطابق'):l('Suggested · within tolerance','مقترح · ضمن السماحية')}}</span><button v-if="active&&can('reconcile')&&r.exact&&bankIds.length===1" @click="action('match',{bank_ids:bankIds,gl_ids:[r.id]})">{{l('Confirm suggestion','تأكيد الاقتراح')}}</button></div></article></div>
<div v-if="active" class="actions"><button v-if="can('reconcile')" class="primary" :disabled="busy||!bankIds.length||!glIds.length" @click="action('match',{bank_ids:bankIds,gl_ids:glIds})">{{l('Match selected','مطابقة المحدد')}} ({{bankIds.length}} / {{glIds.length}})</button>
<template v-if="can('post_adjustment')"><label>{{l('Adjustment','التسوية')}} <FieldHint :label="l('Adjustment','التسوية')" hint-key="bank.adjust"/><select v-model="kind"><option v-for="k in ['fee','interest','rounding']" :value="k">{{label(k)}}</option></select></label><button :disabled="busy||bankIds.length!==1||(kind==='rounding'?!glIds.length:glIds.length>0)" @click="action('adjust',{kind,line_id:bankIds[0],gl_ids:glIds})">{{l('Post adjustment and match','ترحيل التسوية ومطابقتها')}}</button></template></div></template></section></template>
<style scoped>.panes,.grid72,.balances{display:grid;gap:1rem}.panes{grid-template-columns:repeat(2,minmax(0,1fr))}.grid72,.balances{grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr))}.grid72>button{grid-column:1/-1;justify-self:start}.balances>div,.record{display:grid;gap:.35rem;padding:.65rem;border:1px solid var(--border,#465465);border-radius:.5rem}.record label{display:flex;align-items:center;gap:.5rem}.record input{width:auto}.panes article{min-width:0}.record button{justify-self:start}@media(max-width:900px){.panes{grid-template-columns:1fr}}</style>
