<script setup lang="ts">
import {ref} from 'vue'
import {locale} from './i18n'
const p=defineProps<{value:string|number}>(),copied=ref(false)
async function copy(){const s=String(p.value);try{await navigator.clipboard.writeText(s);copied.value=true}catch{const el=document.createElement('textarea');el.value=s;document.body.append(el);el.select();copied.value=document.execCommand('copy');el.remove()}setTimeout(()=>copied.value=false,1500)}
</script><template><span class="copy-id"><code dir="ltr">{{value}}</code> <button type="button" @click.stop="copy">{{copied?(locale==='ar'?'تم النسخ':'Copied'):(locale==='ar'?'نسخ المعرّف':'Copy ID')}}</button></span></template>
