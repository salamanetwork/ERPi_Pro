<script setup lang="ts">
import TableToolbar from "./TableToolbar.vue"
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DateInput from './DateInput.vue'
import { allows, api, createRequest, type Workspace } from './api'
import { duration } from './attendance'
import { message, t } from './i18n'

type Card = {id:number;name:string;code:string;site_id:number;start:string;end:string;state:string;revision:number}
type Person = {id:number;user_id:number;username:string;role:string;active:boolean}
type Task = {id:number;title:string;due:string;state:string;planned_hours:string;owner_id?:number|null;parent_id?:number|null;milestone:boolean;revision:number}
type Linked = {id:number;line_id?:number;reference:string;task:string;state?:string}
type Detail = Card & {linked:{purchase:Linked[];work:Linked[]};can_manage:boolean;can_cost:boolean;membership_id:number;members:Person[];tasks:Task[];candidates?:{id:number;username:string}[];
  baselines?:{version:number;hours:string;categories:Record<string,string>;reason:string}[];rates?:{role:string;rate:string|number}[];
  sources?:{purchase?:{id:number;number:string;title:string;lines:{id:number;description:string}[]}[];work?:{id:number;title:string}[];
  expenses?:{id:number;entry_id:number;entry__description:string;amount:string}[]}}
type Report = {baseline:number|null;planned:string;committed:string;gl_actual:string;labour:string;actual:string;variance:string;available:string;over_budget:boolean;
  planned_hours:string;actual_hours:string;categories:{category:string;planned:string;actual:string}[];
  purchases:{source_id:number;line_id:number;reference:string;task:string;committed:string;billed_stock:string}[];
  entries:{entry_id:number;reference:string;task:string;amount:string;order_id?:number}[];
  time:{sheet_id:number;timer_id?:number;task:string;amount:string;seconds:number}[]}
type Page = {count:number;results:Card[];page:number;next:string|null;previous:string|null}
const props=defineProps<{workspace:Workspace}>()
const route=useRoute(),router=useRouter(),request=createRequest()
const entry=computed(()=>String(route.params.entry||''))
const screen=ref<Page>()
const project=ref<Detail>()
const budget=ref<Report>()
const tab=ref('tasks')
const form=ref('')
const view=ref('list')
const error=ref('')
const notice=ref(false)
const busy=ref(false)
const site=ref<number>()
const name=ref(''),code=ref(''),start=ref(''),end=ref(''),reason=ref('')
const task=ref<Partial<Task>>({})
const person=ref<number>(),role=ref('member'),active=ref(true)
const hours=ref('0'),rate=ref('0')
const categories=ref({materials:'0',expense:'0',labour:'0'})
const linkType=ref('purchase'),linkSource=ref<number>(),linkTask=ref<number>(),linkLine=ref<number>()
const action=ref('')
const filteredSites=computed(()=>props.workspace.sites.filter(row=>row.active&&allows(props.workspace,'projects.manage',row.id)&&allows(props.workspace,'projects.view',row.id)))
const sourceLines=computed(()=>project.value?.sources?.purchase?.find(row=>row.id===linkSource.value)?.lines??[])
const tabs:Record<string,string>={tasks:'Tasks',members:'Project members',budget:'Project budget',links:'Linked documents'}
const labels:Record<string,string>={draft:'Draft',active:'Active',hold:'On hold',closed:'Closed',todo:'To do',progress:'In progress',done:'Done',
  manager:'Project manager',member:'Project member',finance:'Finance reviewer',activate:'Activate',resume:'Resume project',
  close:'Close project',reopen:'Reopen project',materials:'Stock materials',expense:'Posted expenses',labour:'Administrative labour'}
const label=(key:string)=>t(labels[key]||key)
const siteName=(id:number)=>props.workspace.sites.find(row=>row.id===id)?.name||String(id)
const editable=computed(()=>project.value?.can_manage&&['draft','active'].includes(project.value.state))
const linksPanel=computed(()=>editable.value&&Boolean(project.value?.sources))
const taskStates=['todo','progress','done']
const roles=['manager','member','finance']
const costKeys=['materials','expense','labour'] as const
let loadId=0

async function load(){
  const id=++loadId
  try{
    if(entry.value==='new'){
      site.value=filteredSites.value[0]?.id
      return
    }
    if(!entry.value){
      const loaded=await api<Page>('/projects?page='+(String(route.query.page||1))+'&page_size='+String(route.query.page_size||25)+'&q='+encodeURIComponent(String(route.query.q||'')))
      if(loadId===id)screen.value=loaded
      return
    }
    const loaded=await api<Detail>('/projects/'+entry.value)
    if(loadId!==id)return
    project.value=loaded
    if(loaded.can_cost&&tab.value==='budget'){
      const current=await api<Report>('/projects/'+entry.value+'/budget')
      if(loadId===id)budget.value=current
    }
  }catch(failure){if(loadId===id)error.value=message(failure)}
}
watch(()=>route.fullPath,()=>{project.value=screen.value=budget.value=undefined;form.value=error.value='';notice.value=false;tab.value='tasks';void load()},{immediate:true})
watch(tab,()=>{form.value=error.value='';void load()})
async function send(path:string,data:object){
  if(busy.value)return
  busy.value=true
  error.value=''
  notice.value=false
  try{
    const saved=await request<Card>(path,{revision:project.value?.revision,...data})
    if(!project.value) await router.push('/projects/'+saved.id)
    else {form.value='';await load()}
    notice.value=true
  }catch(failure){error.value=message(failure)}
  finally{busy.value=false}
}
const mutate=(op:string,data:object)=>send('/projects/'+project.value?.id+'/'+op,data)
function editProject(){
  if(!project.value)return
  name.value=project.value.name
  start.value=project.value.start
  end.value=project.value.end
  form.value='project'
}
function editTask(row?:Task){
  task.value=row?{...row}:{state:'todo',planned_hours:'0',due:project.value?.end,milestone:false}
  form.value='task'
}
function changeState(value:string){
  action.value=value
  reason.value=''
  form.value='state'
}
function move(row:Task,state:string){
  if(row.state!==state) void mutate('tasks',{id:row.id,revision:row.revision,state,action:'move'})
}
function allowMove(row:Task){return editable.value||(project.value?.state==='active'&&row.owner_id===project.value.membership_id&&allows(props.workspace,'projects.time',project.value.site_id))}
const draggedTask = ref<number>()
function beginDrag(event: DragEvent, row: Task) {
  if (!allowMove(row) || !event.dataTransfer) return
  draggedTask.value = row.id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', String(row.id))
}
function drop(event:DragEvent,state:string){
  const row=project.value?.tasks.find(value=>value.id===Number(event.dataTransfer?.getData('text/plain')))
  draggedTask.value=undefined
  if(row&&allowMove(row))move(row,state)
}
function editMember(row?:Person){
  person.value=row?.user_id
  role.value=row?.role||'member'
  active.value=row?.active??true
  form.value='member'
}
function editCosts(which:string){
  const current=project.value?.baselines?.[0]
  if(which==='baseline'){hours.value=current?.hours||'0';for(const key of costKeys)categories.value[key]=current?.categories[key]||'0'}
  reason.value='';role.value='member';rate.value='0';form.value=which
}
</script>
<template>
  <section class="project-page">
    <h1>{{ t('Projects') }}</h1>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-if="notice" class="success" role="status">{{ t('Saved successfully.') }}</p>
    <template v-if="!entry&&screen">
      <div class="actions" style="margin-block-end: 14px;"><RouterLink v-if="filteredSites.length" class="button primary" to="/projects/new">{{ t('New project') }}</RouterLink></div>
      <div class="panel table-panel"><table><thead><tr><th>{{ t('Code') }}</th><th>{{ t('Name') }}</th><th>{{ t('Site') }}</th><th>{{ t('Status') }}</th></tr></thead>
        <tbody><tr v-for="row in screen.results" :key="row.id"><td><RouterLink :to="'/projects/'+row.id">{{ row.code }}</RouterLink></td>
          <td>{{ row.name }}</td><td>{{ siteName(row.site_id) }}</td><td>{{ label(row.state) }}</td></tr></tbody></table>
        <p v-if="!screen.results.length">{{ t('No projects available.') }}</p>
        <TableToolbar :page="screen.page" :query="String(route.query.q||'')" @search="router.replace({query:{...route.query,q:$event,page:'1'}})" :count="screen.count" :size="Number(route.query.page_size)||25" @page="router.replace({query:{...route.query,page:String($event)}})" @size="router.replace({query:{...route.query,page_size:String($event),page:'1'}})"/>
      </div>
    </template>
    <RouterLink v-if="entry" to="/projects">{{ t('Projects') }}</RouterLink>
    <form v-if="entry==='new'||form==='project'" v-validated-form class="panel record-form"
          @submit.prevent="project?mutate('edit',{name,start,end}):send('/projects',{site_id:site,name,code,start,end})">
      <h2>{{ t(project?'Edit project':'New project') }}</h2>
      <div class="project-fields">
        <label v-if="!project">{{ t('Site') }}<select v-model.number="site" data-field="site_id" required><option v-for="row in filteredSites" :key="row.id" :value="row.id">{{ row.name }}</option></select></label>
        <label v-if="!project">{{ t('Code') }}<input v-model="code" data-field="code" placeholder="PRJ-01" required maxlength="32"></label>
        <label>{{ t('Name') }}<input v-model="name" data-field="name" :placeholder="t('Project name')" required maxlength="120"></label>
        <DateInput v-model="start" :label="t('Start date')" data-field="start" type="date" required/>
        <DateInput v-model="end" :label="t('End date')" data-field="end" type="date" required/>
      </div>
      <button class="primary" :disabled="busy">{{ t('Save') }}</button>
    </form>
    <template v-if="project">
      <header class="panel">
        <h2>{{ project.code }} · {{ project.name }} · {{ label(project.state) }}</h2>
        <p>{{ siteName(project.site_id) }} · {{ project.start }} → {{ project.end }}</p>
        <div v-if="project.can_manage" class="actions">
          <button v-if="editable" class="secondary" @click="editProject">{{ t('Edit project') }}</button>
          <button v-if="project.state==='draft'" class="primary" @click="changeState('activate')">{{ label('activate') }}</button>
          <button v-if="project.state==='active'" class="secondary" @click="changeState('hold')">{{ label('hold') }}</button>
          <button v-if="project.state==='active'" class="primary" @click="changeState('close')">{{ label('close') }}</button>
          <button v-if="project.state==='hold'" class="primary" @click="changeState('resume')">{{ label('resume') }}</button>
          <button v-if="project.state==='closed'" class="secondary" @click="changeState('reopen')">{{ label('reopen') }}</button>
        </div>
      </header>
      <form v-if="form==='state'" v-validated-form class="panel record-form" @submit.prevent="mutate('state',{action,reason})">
        <label>{{ t('Reason') }}<textarea v-model="reason" data-field="reason" :required="['hold','close','reopen'].includes(action)"/></label>
        <button class="primary" :disabled="busy">{{ label(action) }}</button>
      </form>
      <nav class="actions project-tabs"><button v-for="key in Object.keys(tabs).filter(key=>key!=='budget'||project?.can_cost).filter(key=>key!=='links'||linksPanel||project?.linked.purchase.length||project?.linked.work.length)"
        :key="key" class="tab-link" :aria-current="tab===key?'page':undefined" @click="tab=key">{{ t(tabs[key]) }}</button></nav>

      <template v-if="tab==='tasks'">
        <div class="actions">
          <button v-if="editable" class="primary" @click="editTask()">{{ t('New task') }}</button>
          <button class="secondary" :aria-pressed="view==='list'" @click="view='list'">{{ t('List view') }}</button>
          <button class="secondary" :aria-pressed="view==='board'" @click="view='board'">{{ t('Kanban view') }}</button>
        </div>
        <section v-if="form==='task'" class="panel">
          <form v-validated-form class="record-form" @submit.prevent="mutate('tasks',task)">
            <div class="project-fields">
              <label>{{ t('Title') }}<input v-model="task.title" data-field="title" required></label>
              <DateInput v-model="task.due" :label="t('Due date')" data-field="due" type="date" required/>
              <label>{{ t('Planned hours') }}<input v-model="task.planned_hours" data-field="planned_hours" inputmode="decimal" required></label>
              <EntityAutocomplete :label="t('Assignee')" lookup="users" :choices="project.members.filter(row=>row.active).map(row=>({ id: row.id, name: row.username }))" :placeholder="'—'" :empty-value="null" v-model="task.owner_id" />
              <label>{{ t('Parent task') }}<select v-model.number="task.parent_id"><option :value="null">—</option><option v-for="row in project.tasks.filter(row=>row.id!==task.id)" :key="row.id" :value="row.id">{{ row.title }}</option></select></label>
              <label><input v-model="task.milestone" type="checkbox">{{ t('Milestone') }}</label>
            </div><button class="primary" :disabled="busy">{{ t('Save task') }}</button>
          </form>
        </section>
        <section v-if="view==='list'" class="panel table-panel">
          <table><thead><tr><th>{{ t('Title') }}</th><th>{{ t('Due date') }}</th><th>{{ t('Planned hours') }}</th><th>{{ t('Assignee') }}</th><th>{{ t('Status') }}</th></tr></thead>
            <tbody><tr v-for="row in project.tasks" :key="row.id"><td>{{ row.title }} <span v-if="row.milestone">· {{ t('Milestone') }}</span></td><td>{{ row.due }}</td><td>{{ row.planned_hours }}</td>
              <td>{{ project.members.find(member=>member.id===row.owner_id)?.username||'—' }}</td>
              <td><select v-if="allowMove(row)" :value="row.state" :aria-label="row.title+' '+t('Status')" @change="move(row,($event.target as HTMLSelectElement).value)"><option v-for="key in taskStates" :key="key" :value="key">{{ label(key) }}</option></select>
                <span v-else>{{ label(row.state) }}</span>
                <button v-if="editable" class="secondary" @click="editTask(row)">{{ t('Edit') }}</button></td></tr></tbody></table>
        </section>
        <div v-else class="project-board">
          <section v-for="key in taskStates" :key="key" class="panel project-column" @dragover.prevent @drop.prevent="drop($event,key)">
            <h3>{{ label(key) }}</h3>
            <article v-for="row in project.tasks.filter(item=>item.state===key)" :key="row.id" class="project-card"
                 :class="{ 'is-movable': allowMove(row), 'is-dragging': draggedTask===row.id }" :draggable="allowMove(row)"
                 @dragstart="beginDrag($event,row)" @dragend="draggedTask=undefined">
              <header class="project-card-header">
                <h4>{{ row.title }}</h4>
                <span v-if="allowMove(row)" class="project-grip" :title="t('Drag to change status')">
                  <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                    <circle v-for="dot in [[5,4],[11,4],[5,8],[11,8],[5,12],[11,12]]" :key="dot.join('-')" :cx="dot[0]" :cy="dot[1]" r="1.2"/>
                  </svg>
                </span>
              </header>
              <p class="project-card-meta"><time :datetime="row.due">{{ row.due }}</time><span v-if="row.milestone" class="badge">{{ t('Milestone') }}</span></p>
              <select v-if="allowMove(row)" :value="row.state" :aria-label="row.title+' '+t('Status')" @change="move(row,($event.target as HTMLSelectElement).value)"><option v-for="state in taskStates" :key="state" :value="state">{{ label(state) }}</option></select>
            </article>
          </section>
        </div>
      </template>

      <section v-if="tab==='members'" class="panel record-form">
        <h3>{{ t('Project members') }}</h3>
        <ul><li v-for="row in project.members" :key="row.id">{{ row.username }} · {{ label(row.role) }} <span v-if="!row.active">· {{ t('Inactive') }}</span>
          <button v-if="editable" class="secondary" @click="editMember(row)">{{ t('Edit') }}</button></li></ul>
        <button v-if="editable&&form!=='member'" class="primary" @click="editMember()">{{ t('Add member') }}</button>
        <form v-if="form==='member'" v-validated-form class="record-form" @submit.prevent="mutate('member',{user_id:person,role,active})">
          <EntityAutocomplete :label="t('User')" lookup="users" :choices="(project.candidates ?? []).map(row=>({ id: row.id, name: row.username }))" data-field="user_id" :empty-value="undefined" required v-model="person" />
          <label>{{ t('Role') }}<select v-model="role"><option v-for="key in roles" :key="key" :value="key">{{ label(key) }}</option></select></label>
          <label><input v-model="active" type="checkbox">{{ t('Active') }}</label>
          <button class="primary" :disabled="busy">{{ t('Save member') }}</button>
        </form>
      </section>

      <section v-if="tab==='budget'&&budget" class="panel record-form">
        <h3>{{ t('Budget vs Actual') }}</h3>
        <p>{{ t('Baseline version') }} {{ budget.baseline??'—' }} · {{ budget.planned_hours }} / {{ budget.actual_hours }} {{ t('Hours') }}</p>
        <p v-if="budget.over_budget" class="alert">{{ t('Project budget exceeded.') }}</p>
        <dl class="project-fields">
          <div><dt>{{ t('Planned') }}</dt><dd>{{ budget.planned }} AED</dd></div>
          <div><dt>{{ t('Committed') }}</dt><dd>{{ budget.committed }} AED</dd></div>
          <div><dt>{{ t('Actual') }}</dt><dd>{{ budget.actual }} AED</dd></div>
          <div><dt>{{ t('Variance') }}</dt><dd>{{ budget.variance }} AED</dd></div>
          <div><dt>{{ t('Available budget') }}</dt><dd>{{ budget.available }} AED</dd></div>
          <div><dt>{{ t('GL actual') }}</dt><dd>{{ budget.gl_actual }} AED</dd></div>
          <div><dt>{{ label('labour') }}</dt><dd>{{ budget.labour }} AED</dd></div>
        </dl>
        <div class="table-panel"><table><thead><tr><th>{{ t('Category') }}</th><th>{{ t('Planned') }}</th><th>{{ t('Actual') }}</th></tr></thead>
          <tbody><tr v-for="row in budget.categories" :key="row.category"><td>{{ label(row.category) }}</td><td>{{ row.planned }}</td><td>{{ row.actual }}</td></tr></tbody></table></div>
        <div v-if="budget.purchases.length"><h4>{{ t('Purchase commitments') }}</h4>
          <ul><li v-for="row in budget.purchases" :key="row.source_id+'-'+row.line_id"><RouterLink :to="'/purchase-orders/'+row.source_id">{{ row.reference }}</RouterLink> · {{ row.task }} · {{ t('Committed') }} {{ row.committed }} AED · {{ t('Invoiced stock') }} {{ row.billed_stock }} AED</li></ul></div>
        <div v-if="budget.entries.length"><h4>{{ t('GL actual') }}</h4>
          <ul><li v-for="(row,index) in budget.entries" :key="index"><RouterLink v-if="allows(workspace,'accounting.view',null)" :to="{path:'/accounting/journals',query:{entry:row.entry_id}}">{{ row.reference }}</RouterLink><span v-else>{{ row.reference }}</span> · {{ row.task }} · {{ row.amount }} AED <RouterLink v-if="row.order_id" :to="'/work-orders/'+row.order_id">WO-{{ row.order_id }}</RouterLink></li></ul></div>
        <div v-if="budget.time.length"><h4>{{ label('labour') }}</h4>
          <ul><li v-for="(row,index) in budget.time" :key="index"><RouterLink :to="'/hr/attendance/'+row.sheet_id">TS-{{ row.sheet_id }}</RouterLink> · {{ row.task }} · {{ duration(row.seconds) }} · {{ row.amount }} AED</li></ul></div>
        <div v-if="editable" class="actions">
          <button class="secondary" @click="editCosts('baseline')">{{ t('Approve baseline') }}</button>
          <button class="secondary" @click="editCosts('rate')">{{ t('Set labour rate') }}</button>
        </div>
        <form v-if="form==='baseline'" v-validated-form class="record-form" @submit.prevent="mutate('baseline',{hours,categories,reason})">
          <label>{{ t('Planned hours') }}<input v-model="hours" data-field="hours" required inputmode="decimal"></label>
          <label v-for="key in costKeys" :key="key">{{ label(key) }}<input v-model="categories[key]" :data-field="key" required inputmode="decimal"></label>
          <label>{{ t('Reason') }}<textarea v-model="reason" data-field="reason" required/></label>
          <button class="primary" :disabled="busy">{{ t('Approve baseline') }}</button>
        </form>
        <form v-if="form==='rate'" v-validated-form class="record-form" @submit.prevent="mutate('rate',{role,rate})">
          <label>{{ t('Role') }}<select v-model="role"><option v-for="key in roles" :key="key" :value="key">{{ label(key) }}</option></select></label>
          <label>{{ t('Hourly cost rate') }}<input v-model="rate" data-field="rate" required inputmode="decimal"></label>
          <button class="primary" :disabled="busy">{{ t('Set labour rate') }}</button>
        </form>
        <details v-if="project.baselines?.length"><summary>{{ t('Baseline history') }}</summary>
          <ul><li v-for="row in project.baselines" :key="row.version">#{{ row.version }} · {{ row.hours }} {{ t('Hours') }} · {{ row.reason }}<span v-for="key in costKeys" :key="key"> · {{ label(key) }} {{ row.categories[key] }} AED</span></li></ul>
        </details>
        <details v-if="project.rates?.length"><summary>{{ t('Labour rates') }}</summary>
          <ul><li v-for="row in project.rates" :key="row.role">{{ label(row.role) }} · {{ row.rate }} AED</li></ul></details>
      </section>

      <section v-if="tab==='links'" class="panel record-form">
        <h3>{{ t('Linked documents') }}</h3>
        <ul><li v-for="row in project.linked.purchase" :key="'po-'+row.id+'-'+row.line_id">
          <RouterLink v-if="allows(workspace,'purchasing.view',project.site_id)" :to="'/purchase-orders/'+row.id">{{ row.reference }}</RouterLink>
          <span v-else>{{ row.reference }}</span> · #{{ row.line_id }} · {{ row.task }}</li>
          <li v-for="row in project.linked.work" :key="'wo-'+row.id">
            <RouterLink v-if="allows(workspace,'workorders.view',project.site_id)" :to="'/work-orders/'+row.id">{{ row.reference }}</RouterLink>
            <span v-else>{{ row.reference }}</span> · {{ row.task }}</li></ul>
        <form v-if="linksPanel" v-validated-form class="record-form" @submit.prevent="mutate('link',{type:linkType,source_id:linkSource,line_id:linkLine,task_id:linkTask})">
          <label>{{ t('Source type') }}<select v-model="linkType" @change="linkSource=linkLine=undefined"><option value="purchase">{{ t('Purchase order line') }}</option><option value="work">{{ t('Work order') }}</option><option v-if="project.can_cost" value="expense">{{ t('Posted expense line') }}</option></select></label>
          <label>{{ t('Source record') }}<select v-model.number="linkSource" data-field="source_id" required @change="linkLine=undefined">
            <template v-if="linkType==='purchase'"><option v-for="row in project.sources?.purchase" :key="row.id" :value="row.id">{{ row.number }} · {{ row.title }}</option></template>
            <template v-else-if="linkType==='work'"><option v-for="row in project.sources?.work" :key="row.id" :value="row.id">WO-{{ row.id }} · {{ row.title }}</option></template>
            <template v-else><option v-for="row in project.sources?.expenses" :key="row.id" :value="row.id">#{{ row.entry_id }} · {{ row.entry__description }} · {{ row.amount }}</option></template>
          </select></label>
          <label v-if="linkType==='purchase'">{{ t('Line item') }}<select v-model.number="linkLine" data-field="line_id" required><option v-for="row in sourceLines" :key="row.id" :value="row.id">{{ row.description }}</option></select></label>
          <label>{{ t('Project task') }}<select v-model.number="linkTask" data-field="task_id" required><option v-for="row in project.tasks.filter(row=>!row.milestone)" :key="row.id" :value="row.id">{{ row.title }}</option></select></label>
          <button class="primary" :disabled="busy">{{ t('Link document') }}</button>
        </form>
      </section>
    </template>
    <button v-if="form&&project" type="button" class="secondary" @click="form=''">{{ t('Cancel') }}</button>
  </section>
</template>
<style scoped>
.project-page {display:grid;gap:1rem;min-inline-size:0}
.project-fields,.project-board {display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(min(100%,14rem),1fr))}
.project-column { padding: 1rem; min-inline-size: 0; background: var(--subtle); }
.project-column h3 { margin: 0 0 .9rem; }
.project-card {
  display: grid; gap: .7rem; padding: 1rem; margin-block: .75rem;
  border: 1px solid var(--border); border-radius: .75rem; background: var(--surface);
  box-shadow: 0 2px 5px rgb(10 21 35 / 10%);
  transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease;
}
.project-card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: .6rem; }
.project-card-header h4 { margin: 0; font-size: 1rem; line-height: 1.35; font-weight: 750; overflow-wrap: anywhere; }
.project-card-meta { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; margin: 0; color: var(--muted); font-size: .875rem; }
.project-card.is-movable { cursor: pointer; }
.project-grip { display: inline-flex; align-items: center; flex: 0 0 auto; color: var(--muted); cursor: grab; }
.project-card.is-dragging, .project-card.is-dragging .project-grip { cursor: grabbing; }
.project-card.is-dragging { opacity: .65; }
.project-card.is-movable:focus-within { border-color: var(--focus); }
@media (hover: hover) {
  .project-card.is-movable:hover { border-color: var(--primary); box-shadow: 0 8px 18px rgb(10 21 35 / 14%); transform: translateY(-2px); }
}
@media (prefers-reduced-motion: reduce) {
  .project-card { transition: none; }
  .project-card.is-movable:hover { transform: none; }
}
.project-tabs .tab-link[aria-current] {border-block-end:2px solid currentColor;font-weight:700}
.project-fields dd {margin-inline-start:0;font-weight:700}
</style>
