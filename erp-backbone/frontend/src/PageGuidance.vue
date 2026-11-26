<script setup lang="ts">
import {computed} from 'vue'
import {useRoute} from 'vue-router'
import {helpTopic,formHelpFields,fieldHelp} from './helpText'
import {locale,t} from './i18n'
const route=useRoute()
const topic=computed(()=>helpTopic(String(route.name),String(route.params.section||'')))
const fields=computed(()=>formHelpFields(String(route.name),route.path))
</script>
<template><aside class="purpose" :key="route.path"><p>{{topic.purpose}}</p><details><summary>{{locale==='ar'?'طريقة استخدام الشاشة والمطلوب منك':t('How this screen works')}}</summary><p>{{topic.fields}}</p><dl v-if="fields.length"><template v-for="key in fields" :key="key"><dt>{{t(key)}}</dt><dd>{{fieldHelp(key)}}</dd></template></dl><p>{{topic.next}}</p></details></aside></template>
