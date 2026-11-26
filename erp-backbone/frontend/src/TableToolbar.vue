<script setup lang="ts">
import {computed} from 'vue'
import {locale, t} from './i18n'
const p=defineProps<{page:number;size:number;count:number;query?:string}>()
const emit=defineEmits<{page:[number];size:[number];search:[string]}>()
const last=computed(()=>Math.max(1,Math.ceil(p.count/p.size)))
const l=(en:string,ar:string)=>locale.value==='ar'?ar:en
</script>
<template><nav class="erp-toolbar" :aria-label="t('Table controls')"><label v-if="query!==undefined">{{t('Search')}}<input type="search" :value="query" @input="emit('search',($event.target as HTMLInputElement).value)"></label><slot/><label>{{t('Rows per page')}}<select :value="size" @change="emit('size',Number(($event.target as HTMLSelectElement).value))"><option v-for="n in [10,25,50]" :key="n">{{n}}</option></select></label><span>{{t('Total records')}} · {{count}}</span><button type="button" :disabled="page<=1" @click="emit('page',1)">{{t('First')}}</button><button type="button" :disabled="page<=1" @click="emit('page',page-1)">{{t('Previous')}}</button><span aria-live="polite">{{t('Page')}} {{page}} {{t('of')}} {{last}}</span><button type="button" :disabled="page>=last" @click="emit('page',page+1)">{{t('Next')}}</button><button type="button" :disabled="page>=last" @click="emit('page',last)">{{t('Last')}}</button></nav></template>
