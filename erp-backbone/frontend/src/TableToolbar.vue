<script setup lang="ts">
import {computed} from 'vue'
import {locale} from './i18n'
const p=defineProps<{page:number;size:number;count:number;query?:string}>()
const emit=defineEmits<{page:[number];size:[number];search:[string]}>()
const last=computed(()=>Math.max(1,Math.ceil(p.count/p.size)))
const l=(en:string,ar:string)=>locale.value==='ar'?ar:en
</script>
<template><nav class="erp-toolbar" :aria-label="l('Table controls','أدوات الجدول')"><label v-if="query!==undefined">{{l('Search','بحث')}}<input type="search" :value="query" @input="emit('search',($event.target as HTMLInputElement).value)"></label><slot/><label>{{l('Rows per page','صفوف الصفحة')}}<select :value="size" @change="emit('size',Number(($event.target as HTMLSelectElement).value))"><option v-for="n in [10,25,50]" :key="n">{{n}}</option></select></label><span>{{l('Total records','إجمالي السجلات')}} · {{count}}</span><button type="button" :disabled="page<=1" @click="emit('page',1)">{{l('First','الأولى')}}</button><button type="button" :disabled="page<=1" @click="emit('page',page-1)">{{l('Previous','السابق')}}</button><span aria-live="polite">{{l('Page','صفحة')}} {{page}} {{l('of','من')}} {{last}}</span><button type="button" :disabled="page>=last" @click="emit('page',page+1)">{{l('Next','التالي')}}</button><button type="button" :disabled="page>=last" @click="emit('page',last)">{{l('Last','الأخيرة')}}</button></nav></template>
