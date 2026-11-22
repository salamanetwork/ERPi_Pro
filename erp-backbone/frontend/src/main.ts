import { createApp, defineComponent } from 'vue'
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'
import '@fontsource/noto-sans-arabic/arabic-400.css'
import '@fontsource/noto-sans-arabic/arabic-600.css'
import './style.css'
import App from './App.vue'
import { workspaceExtensions } from './extensions'
import FieldHint from './FieldHint.vue'
import EntityAutocomplete from './EntityAutocomplete.vue'
import { validatedForm } from './formValidation'

const screen = defineComponent({ render: () => null })
const router = createRouter({
  history: createWebHistory(),
  routes: [
    ['/', 'home'], ['/profile', 'profile'], ['/modules', 'modules'], ['/assets', 'assets'], ['/assets/:id(\\d+)', 'assetDetail'], ['/admin/sites', 'sites'], ['/module-visibility', 'moduleVisibility'], ['/work-orders', 'orders'],
    ['/purchase-requests', 'purchases'], ['/purchase-requests/:id(\\d+)', 'purchase'],
    ['/purchase-orders', 'purchaseOrders'], ['/purchase-orders/:id(\\d+)', 'purchaseOrder'], ['/suppliers/:entry(new|\\d+)?/:edit(edit)?', 'suppliers'],
    ['/sales/customers/:entry(new|\\d+)?/:edit(edit)?', 'customers'],
    ['/inventory/items/:entry(new|\\d+)?/:edit(edit)?', 'items'],
    ['/hr/setup/:kind(departments|job-titles|grades)/:entry(new|\\d+)?/:edit(edit)?', 'hrSetup'],
    ['/hr/setup/employee-numbering', 'employeeNumbering'], ['/hr/offboarding', 'offboarding'],
    ['/supply/:section(transfers|reservations|shipments|transit)?/:entry(new|\\d+)?', 'supply'],
    ['/crm/:section(leads|opportunities|activities|customer-requests|settings)?/:entry(new|\\d+)?', 'crm'],
    ['/sales/:section(quotations|aging)?', 'sales'], ['/sales/:id(\\d+)', 'sale'],
    ['/maintenance', 'maintenance'], ['/maintenance/assets/:id(\\d+)', 'maintenanceAsset'],
    ['/hr/attendance/corrections/:id(\\d+)?', 'attendanceCorrections'],
    ['/hr/attendance/sessions/:sessionId(\\d+)/correction', 'attendanceCorrectionRequest'],
    ['/hr/attendance/:id(\\d+)?', 'attendance'],
    ['/hr/:section(employees|leaves)?/:id(new|\\d+)?', 'hr'],
    ['/payroll/:section(runs|payslips)?/:id(\\d+)?', 'payroll'],
    ['/budgeting/:section(centers|budgets|report|details)?', 'budgeting'],
    ['/reports/:board(finance|stock|sales|maintenance)?', 'reportsDashboard'], ['/reports/detail/:metric', 'reportsDetail'],
    ['/payables/:section(invoices|aging)?/:id(\\d+)?', 'payables'],
    ['/accounting/:section(journals|postings|reports|setup|periods)?', 'accounting'],
    ['/inventory/:section(receipts|balances|movements|opening|setup)?', 'inventory'],
    ['/inventory/receipts/:id(\\d+)', 'receipt'], ['/inventory/receive/:id(\\d+)', 'receive'],
    ['/purchase-templates', 'purchaseTemplates'], ['/document-settings', 'documentSettings'], ['/data-import', 'dataImport'], ['/inbox', 'inbox'], ['/search', 'search'], ['/notifications', 'notifications'],
    ['/work-orders/:id(\\d+)', 'order'], ['/admin/integrations','gateway'], ['/admin/security-health','securityHealth'], ['/admin/access-control', 'access'], ['/admin/users/:id(\\d+)?', 'adminUsers'], ['/approval-templates', 'approvalTemplates'], ['/audit', 'audit'], ['/settings', 'settings'], ['/settings/company', 'companyProfile'], ['/delegations', 'delegations'],
    ...workspaceExtensions.map(extension => [extension.route, extension.id]),
    ['/:pathMatch(.*)*', 'notFound'],
  ].map<RouteRecordRaw>(([path, name]) => ({ path, name, component: screen })).concat([
    { path:'/admin/audit-trail',redirect:'/audit'},
    { path: '/admin/modules', redirect: '/modules' },
    { path: '/sites', redirect: '/admin/sites' },
    { path: '/access', redirect: '/admin/access-control' },
    {
      path: '/hr/payroll/:id(\\d+)?',
      redirect: (to: import('vue-router').RouteLocation) => ({
        path: '/payroll/runs' + (to.params.id ? '/' + to.params.id : ''), query: to.query, hash: to.hash,
      }),
    },
    {
      path: '/hr/payslips/:id(\\d+)?',
      redirect: (to: import('vue-router').RouteLocation) => ({
        path: '/payroll/payslips' + (to.params.id ? '/' + to.params.id : ''), query: to.query, hash: to.hash,
      }),
    },
  ]),
})
createApp(App).directive('validated-form', validatedForm).component('FieldHint', FieldHint).component('EntityAutocomplete', EntityAutocomplete).use(router).mount('#app')
