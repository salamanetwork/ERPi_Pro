<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { allows, api, ApiError, type Workspace } from './api'
import { message, t } from './i18n'
import DataTable from './DataTable.vue'
import CsvExport from './CsvExport.vue'
import { useDirectory } from './useDirectory'

type Employee = {id:number;number:string;name:string;site_id:number;department:string;title:string;
  archived:boolean;revision:number;ended:string|null}
const {workspace} = defineProps<{workspace:Workspace}>()
const route = useRoute()
const list = useDirectory<Employee, {departments:{id:number;name:string}[]}>(() => '/hr/employees',
  () => true, failure => failure instanceof ApiError && ['invalid_page','invalid_page_size','invalid_site',
    'invalid_department','invalid_status','invalid_q','forbidden','module_disabled'].includes(failure.code)
    ? message(failure) : 'Could not load the employee directory. Retry to refresh the list.')

const {page, loading, error, filter, filters, change, filterBy} = list
const filterId = (key: 'site' | 'department') => filter(key).trim().replace(/[٠-٩۰-۹]/g,
  digit => String('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹'.indexOf(digit) % 10))
const failure = ref('')
const busy = ref(false)
async function archive(row:Employee) {
  if (busy.value || (!row.archived && !window.confirm(t('Archive this employee?')))) return
  busy.value = true
  failure.value = ''
  try {
    await api('/hr/employees/'+row.id+'/archive','POST',{revision:row.revision,active:row.archived})
    await list.load()
  } catch (error) { failure.value = message(error) }
  finally { busy.value = false }
}
</script>

<template>
  <div class="page-heading"><h2>{{ t('Employees') }}</h2>
    <RouterLink v-if="allows(workspace,'hr.manage')" class="button primary"
      :to="{path:'/hr/employees/new',query:route.query}">{{ t('New employee') }}</RouterLink>
  </div>
  <p v-if="failure" class="alert" role="alert">{{ t(failure) }}</p>
  <div class="directory-filters">
    <label>{{ t('Search') }}<input type="search" :value="filter('q')" maxlength="120" @change="filterBy($event,'q')" @keyup.enter="filterBy($event,'q')"></label>
    <label>{{ t('Site') }}<select :value="filterId('site')" @change="filterBy($event,'site')"><option value="">{{ t('All') }}</option>
      <option v-for="site in workspace.sites" :key="site.id" :value="site.id">{{ site.name }}</option></select></label>
    <label>{{ t('Department') }}<select :value="filterId('department')" @change="filterBy($event,'department')"><option value="">{{ t('All') }}</option>
      <option v-for="row in page?.departments" :key="row.id" :value="row.id">{{ row.name }}</option></select></label>
    <label>{{ t('Status') }}<select :value="filter('status')" @change="filterBy($event,'status')">
      <option value="">{{ t('All current records') }}</option><option value="active">{{ t('Active') }}</option><option value="ended">{{ t('Employment ended') }}</option><option value="archived">{{ t('Archived') }}</option>
    </select></label>
    <CsvExport :workspace="workspace" screen="employees" :filters="filters" />
    <button v-if="error" type="button" class="secondary" @click="list.load">{{ t('Retry') }}</button>
  </div>
  <DataTable :page="page" :headings="['Employee number','Name','Site','Department','Job title','Actions']"
    :loading="loading" :error="error" @page="change('page',$event)" v-slot="{row}">
    <td><RouterLink :to="{path:'/hr/employees/'+row.id,query:route.query}"><bdi>{{ row.number }}</bdi></RouterLink></td>
    <td>{{ row.name }}</td><td>{{ workspace.sites.find(site=>site.id===row.site_id)?.name ?? row.site_id }}</td>
    <td>{{ row.department }}</td><td>{{ row.title }}</td>
    <td><div v-if="allows(workspace,'hr.manage',row.site_id)" class="actions">
      <RouterLink :to="{path:'/hr/employees/'+row.id,query:route.query}" class="button secondary">{{ t('Edit') }}</RouterLink>
      <button type="button" :disabled="busy" class="secondary" @click="archive(row)">{{ t(row.archived ? 'Reactivate' : 'Archive') }}</button>
    </div></td>
  </DataTable>
</template>
