import {computed, onMounted, ref, type ComputedRef} from 'vue'
import {api,ApiError} from './api'
import {locale,message} from './i18n'

export const local=(en:string,ar:string)=>locale.value==='ar'?ar:en
export type Size='compact'|'medium'|'full'
export type Widget={id:string;size:Size}
type Board={en:string;ar:string;widgets:Widget[]}
type Layout={default:string;boards:Record<string,Board>}
type Stored={layout:Layout;revision:number;pending?:boolean}
const make=(en:string,ar:string,ids:string):Board=>({en,ar,widgets:ids.split(' ').map(id=>({id,size: id==='activities'||id==='sales'?'full':['ap-aging','ar-aging','pm-on-time','billing','payments','late-list'].includes(id)?'medium':'compact'}))})
export const presets:Record<string,Board>={
 general:make('General Overview','نظرة عامة','inbox purchase crm-open crm-pipeline net-posted-sales grni sales ap-aging ar-aging pm-on-time activities'),
 finance:make('Documents & Finance','المستندات والمالية','billing payments pending-postings ap-aging ar-aging grni'),
 sales:make('Sales & CRM','المبيعات والعملاء','crm-open crm-pipeline sales unbilled-sales customers-activity'),
 procurement:make('Procurement & Inventory','المشتريات والمخزون','requests purchase open-purchases overdue-purchases late-list stock-value receipts transit'),
 operations:make('Operations & Support','العمليات والدعم','maintenance-open pm-on-time pm-due tickets projects')
}
export function useStudio(username:string, eligible:ComputedRef<string[]>) {
 const cache='erp:widget-studio:'+username
 const layout=ref<Layout>({default:'general',boards:{}}),revision=ref(0)
 const active=ref('general'),editing=ref(false),busy=ref(false),ready=ref(false),note=ref('')
 const boards=computed(()=>({...presets,...layout.value.boards}))
 const current=computed(()=>boards.value[active.value]??presets.general)
 const options=computed(()=>Object.keys(boards.value).filter(key=>key==='general'||!(key in presets)||presets[key].widgets.some(w=>eligible.value.includes(w.id))))
 const widgets=computed(()=>current.value.widgets.filter(w=>eligible.value.includes(w.id)))
 function store(pending:boolean){try{localStorage.setItem(cache,JSON.stringify({layout:layout.value,revision:revision.value,pending}))}catch{}}
 function apply(record:Stored){revision.value=record.revision;layout.value=record.layout.boards?record.layout:{default:'general',boards:{}}}
 async function sync(){
  busy.value=true;note.value=''
  try{apply(await api<Stored>('/preferences/dashboards','PATCH',{revision:revision.value,layout:layout.value}));store(false)}
  catch(e){if(e instanceof ApiError && e.status===409){
   apply(await api<Stored>('/preferences/dashboards'));store(false);note.value=local('A newer layout was loaded. Repeat your edit.','تم تحميل ترتيب أحدث. أعد التعديل.')
  }else{note.value=local('Saved on this device.','تم الحفظ على هذا الجهاز.');store(true)}}
  finally{busy.value=false}
 }
 onMounted(async()=>{
  let cached:Stored|undefined
  try{cached=JSON.parse(localStorage.getItem(cache)||'null');if(cached)apply(cached)}catch{}
  try{if(cached?.pending)await sync();else{apply(await api<Stored>('/preferences/dashboards'));store(false)}}catch{note.value=local('Using device preferences','تُستخدم تفضيلات الجهاز')}
  if(!revision.value&&!layout.value.boards.general){
   try{
    const hidden=JSON.parse(localStorage.getItem('erp:dashboard-widgets:'+username)||'[]')
    if(Array.isArray(hidden)&&hidden.length){
     layout.value.boards.general={...presets.general,widgets:presets.general.widgets.filter(w=>!hidden.includes(w.id))}
     await sync()
    }
   }catch{}
  }
  active.value=layout.value.default;ready.value=true
 })
 function editBoard(fn:(b:Board)=>void){
  if(busy.value)return
  const b=JSON.parse(JSON.stringify(current.value)) as Board
  fn(b);layout.value.boards[active.value]=b;void sync()
 }
 function move(from:string,to:string){
  if(from===to)return
  editBoard(b=>{const i=b.widgets.findIndex(w=>w.id===from),j=b.widgets.findIndex(w=>w.id===to);if(i>=0&&j>=0)b.widgets.splice(j,0,b.widgets.splice(i,1)[0])})
 }
 function create(en:string,ar:string,duplicate:boolean){
  const key='custom-'+Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16).padStart(8,'0')).join('');layout.value.boards[key]={en:en.trim(),ar:ar.trim(),widgets:duplicate?current.value.widgets.map(w=>({...w})):[]}
  active.value=key;editing.value=true;void sync()
 }
 function reset(){if(!confirm(local('Reset this dashboard?','استعادة إعدادات هذه اللوحة؟')))return
  if(active.value in presets)delete layout.value.boards[active.value];else layout.value.boards[active.value].widgets=[]
  void sync()
 }
 function remove(){if(active.value in presets||!confirm(local('Delete this dashboard?','حذف هذه اللوحة؟')))return
  delete layout.value.boards[active.value];if(layout.value.default===active.value)layout.value.default='general';active.value='general';void sync()
 }
 return {layout,active,editing,busy,ready,note,boards,current,options,widgets,editBoard,move,create,reset,remove,sync}
}
