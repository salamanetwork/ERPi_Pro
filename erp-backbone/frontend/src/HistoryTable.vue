<script setup lang="ts">
import {ref,watch} from 'vue'
import TableToolbar from './TableToolbar.vue'
import {api} from './api'
import {message} from './i18n'
const p=defineProps<{url:string;version?:number}>()
const rows=ref<any[]>([]),count=ref(0),page=ref(1),size=ref(10),q=ref(''),error=ref('')
let ticket=0
watch([q,size,()=>p.url],()=>page.value=1)
watch([()=>p.url,()=>p.version,q,size,page],async()=>{const k=++ticket;try{const d=await api<{results:any[];count:number;page:number}>(p.url+'?'+new URLSearchParams({q:q.value,page:String(page.value),page_size:String(size.value)}));if(ticket===k){rows.value=d.results;count.value=d.count;page.value=d.page;error.value=''}}catch(e){if(k===ticket){rows.value=[];error.value=message(e)}}},{immediate:true})
</script><template><section><TableToolbar :page="page" :count="count" :size="size" :query="q" @page="page=$event" @size="size=$event" @search="q=$event"/><p v-if="error" role="alert">{{error}}</p><slot :rows="rows"/></section></template>
