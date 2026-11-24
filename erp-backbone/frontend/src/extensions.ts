import { defineAsyncComponent, type Component } from 'vue'

type Extension = {id:string; group:string; label:string; labelAr?:string; permission?:string; path:string; route:string; component:Component}
type Contract = {id:string; navigation:{route:string;label:{en:string;ar:string};required_permission:string}[]}
const declarations = import.meta.glob<Contract>('../../modules/*/manifest.json', {eager:true,import:'default'})
const views = import.meta.glob<{default:Component}>('../../modules/*/ui/ModulePage.vue')
const generated:Extension[] = Object.entries(declarations).filter(([path])=>views[path.replace('manifest.json','ui/ModulePage.vue')]).map(([path, manifest])=>{
  const nav=manifest.navigation[0]
  const load=views[path.replace('manifest.json','ui/ModulePage.vue')]
  return {id:manifest.id,group:['subscriptions','pos'].includes(manifest.id)?'sales':['bank_reconciliation','expenses'].includes(manifest.id)?'finance':'general',label:nav.label.en,labelAr:nav.label.ar,permission:nav.required_permission,path:nav.route,route:nav.route,component:defineAsyncComponent(load)}
})
export const workspaceExtensions:Extension[] = [{
  id: 'publication', group: 'general', label: 'Public Sites', path: '/publication', route: '/publication/:entry(new)?',
  component: defineAsyncComponent(() => import('./FoundationPage.vue')),
}, {
  id: 'manufacturing',
  group: 'inventory',
  label: 'Manufacturing',
  path: '/manufacturing',
  route: '/manufacturing/:section(boms|orders|plans|policies)?/:entry(new|\\d+)?',
  component: defineAsyncComponent(() => import('./ManufacturingPage.vue')),
}, {
  id: 'projects', group: 'general', label: 'Projects', path: '/projects',
  route: '/projects/:entry(new|\\d+)?',
  component: defineAsyncComponent(() => import('./ProjectsPage.vue')),
}, {
  id: 'helpdesk', group: 'general', label: 'Helpdesk', path: '/helpdesk',
  route: '/helpdesk/:entry(new|settings|\\d+)?',
  component: defineAsyncComponent(() => import('./HelpdeskPage.vue')),
}, {
  id: 'marketing', group: 'general', label: 'Marketing', path: '/marketing',
  route: '/marketing/:section(campaigns)?/:id(\\d+)?',
  component: defineAsyncComponent(() => import('./MarketingPage.vue')),
}, ...generated]
