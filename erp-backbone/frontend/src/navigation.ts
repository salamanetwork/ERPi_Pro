import { workspaceExtensions } from './extensions'
import { allows, type Workspace } from './api'

type Screen = {
  id: string
  labelAr?: string
  label: string
  path: string
  aliases?: string[]
  module?: string
  permissions?: string[]
  global?: boolean
  admin?: boolean
}

type Group = { id: string; label: string; screens: Screen[] }

function extensionScreens(group:string): Screen[] {
  return workspaceExtensions.filter(extension=>extension.group===group).map(extension=>({
    id:extension.id,label:extension.label,labelAr:extension.labelAr,path:extension.path,module:extension.id,permissions:[extension.permission || extension.id+'.view'],
  }))
}

export const groups: Group[] = [
  { id: 'general', label: 'General', screens: [
    { id: 'workspace', label: 'Workspace', path: '/' },
    { id: 'reports', label: 'Reports and dashboards', path: '/reports' },
    ...extensionScreens('general'),
  ] },
  { id: 'procurement', label: 'Procurement', screens: [
    { id: 'purchase_requests', label: 'Purchase requisitions', path: '/purchase-requests', aliases: ['/purchase-templates'], module: 'purchasing', permissions: ['purchasing.view'] },
    { id: 'purchase_orders', label: 'Purchase orders', path: '/purchase-orders', module: 'purchase_orders', permissions: ['purchasing.view'] },
    { id: 'suppliers', label: 'Suppliers', path: '/suppliers', module: 'suppliers', permissions: ['suppliers.view', 'suppliers.manage'], global: true },
    { id: 'supplier_invoices', label: 'Supplier invoices', path: '/payables/invoices', aliases: ['/payables'], module: 'supplier_invoices', permissions: ['ap.view'] },
  ] },
  { id: 'inventory', label: 'Inventory', screens: [
    { id: 'inventory_items', label: 'Inventory items', path: '/inventory/items', aliases: ['/inventory'], module: 'inventory', permissions: ['inventory.view'] },
    { id: 'goods_receipts', label: 'Goods receipts', path: '/inventory/receipts', aliases: ['/inventory/receive'], module: 'inventory', permissions: ['inventory.view'] },
    ...extensionScreens('inventory'),
    { id: 'stock_balances', label: 'Stock balances', path: '/inventory/balances', module: 'inventory', permissions: ['inventory.view'] },
    { id: 'supply', label: 'Supply Chain', path: '/supply', module: 'supply', permissions: ['supply.view'] },
    { id: 'stock_movements', label: 'Stock movements', path: '/inventory/movements', module: 'inventory', permissions: ['inventory.view'] },
    { id: 'inventory_setup', label: 'Inventory setup', path: '/inventory/setup', module: 'inventory', permissions: ['inventory.setup'] },
    { id: 'opening_balances', label: 'Opening balance', path: '/inventory/opening', module: 'inventory', permissions: ['inventory.opening'] },
  ] },
  { id: 'maintenance', label: 'Assets & Maintenance', screens: [
    { id: 'assets_list', label: 'Assets', path: '/assets', module: 'assets', permissions: ['assets.view'] },
    { id: 'work_orders', label: 'Work orders', path: '/work-orders', aliases: ['/approval-templates'], module: 'workorders', permissions: ['workorders.view'] },
    { id: 'maintenance', label: 'Maintenance', path: '/maintenance', module: 'workorders', permissions: ['maintenance.view'] },
    { id: 'preventive_maintenance', label: 'Preventive maintenance', path: '/maintenance?tab=plans', module: 'workorders', permissions: ['maintenance.view'] },
  ] },
  { id: 'finance', label: 'Finance', screens: [
    ...extensionScreens('finance'),
    { id: 'general_accounting', label: 'Accounting', path: '/accounting', module: 'accounting', permissions: ['accounting.view'], global: true },
    { id: 'cost_centers_budgets', label: 'Cost centers and budgets', path: '/budgeting/report', aliases: ['/budgeting'], module: 'accounting', permissions: ['budget.view'] },
  ] },
  { id: 'sales', label: 'Sales', screens: [
    ...extensionScreens('sales'),
    { id: 'crm', label: 'CRM', path: '/crm', module: 'crm', permissions: ['crm.view'] },
    { id: 'customers', label: 'Customers', path: '/sales/customers', module: 'customers', permissions: ['sales.view'] },
    { id: 'sales_documents', label: 'Sales documents', path: '/sales', aliases: ['/sales/quotations'], module: 'sales', permissions: ['sales.view'] },
    { id: 'ar_aging', label: 'AR aging', path: '/sales/aging', module: 'sales', permissions: ['sales.view'] },
  ] },
  { id: 'hr', label: 'Human Resources', screens: [
    { id: 'employees', label: 'Employees', path: '/hr/employees', aliases: ['/hr'], module: 'hr', permissions: ['hr.view', 'hr.manage', 'hr.self', 'payroll.prepare', 'hr.identity.view_full', 'hr.offboarding.request', 'hr.offboarding.approve'] },
    { id: 'hr_setup', label: 'HR Setup', path: '/hr/setup/departments', aliases: ['/hr/setup'], module: 'hr', permissions: ['hr.manage'] },
    { id: 'offboarding', label: 'Offboarding', path: '/hr/offboarding', module: 'hr', permissions: ['hr.offboarding.request', 'hr.offboarding.approve'] },
    { id: 'leave_requests', label: 'Leave requests', path: '/hr/leaves', module: 'hr', permissions: ['hr.view', 'hr.self', 'hr.leave.request', 'hr.leave.approve', 'hr.manage'] },
    { id: 'time_attendance', label: 'Time & Attendance', path: '/hr/attendance', module: 'hr', permissions: ['attendance.record', 'attendance.approve', 'attendance.setup', 'attendance.correct', 'attendance.reopen'] },
  ] },
  { id: 'payroll', label: 'Payroll', screens: [
    { id: 'payroll_runs', label: 'Payroll runs', path: '/payroll/runs', aliases: ['/hr/payroll', '/payroll'], module: 'payroll', permissions: ['payroll.prepare', 'payroll.approve', 'payroll.post', 'payroll.pay', 'payroll.summary'] },
    { id: 'payslips', label: 'Payslips', path: '/payroll/payslips', aliases: ['/hr/payslips'], module: 'payroll', permissions: ['payroll.payslip', 'payroll.payslip.self'] },
  ] },
  { id: 'admin', label: 'Admin & Settings', screens: [
    { id: 'module_manager', label: 'Module Manager', path: '/modules', admin: true },
    { id: 'integrations', label: 'Integrations & Webhooks', path: '/admin/integrations', admin: true },
    { id: 'security-health', label: 'Security Health', path: '/admin/security-health', admin: true },
    { id: 'users', label: 'Users', path: '/admin/users', admin: true },
    { id: 'access_control', label: 'Access control', path: '/admin/access-control', aliases: ['/access'], admin: true },
    { id: 'sites', label: 'Sites', path: '/admin/sites', admin: true },
    { id: 'module_visibility', label: 'Module visibility', path: '/module-visibility', admin: true },
    { id: 'audit_trail', label: 'Audit trail', path: '/audit', permissions: ['platform.audit'], global: true },
    { id: 'document_settings', label: 'Document settings', path: '/document-settings', admin: true },
    { id: 'data_import', label: 'Data import', path: '/data-import', admin: true },
    { id: 'delegations', label: 'Delegations', path: '/delegations' },
    { id: 'settings', label: 'Settings', path: '/settings' },
  ] },
]

export const platformScreens: Screen[] = [
  { id: 'inbox', label: 'Inbox', path: '/inbox' },
  { id: 'notifications', label: 'Notifications', path: '/notifications' },
  { id: 'profile', label: 'User profile', path: '/profile' },
]

export const screens = [...platformScreens, ...groups.flatMap(group => group.screens)]

export function screenVisible(workspace: Workspace, screen: Screen) {
  if (workspace.hidden_screens.includes(screen.id)) return false
  if (screen.admin && !workspace.admin) return false
  if (screen.module) {
    if (!workspace.modules.some(module =>
      module.id === screen.module && module.enabled && module.navigation_allowed)) return false
  }
  if (['maintenance', 'preventive_maintenance'].includes(screen.id)
      && (!moduleNavigation(workspace, 'assets') || !allows(workspace, 'assets.view') || !allows(workspace, 'workorders.view'))) return false
  return !screen.permissions || screen.permissions.some(permission =>
    (permission !== 'payroll.prepare' || screen.module !== 'hr' ||
      workspace.modules.some(module => module.id === 'payroll' && module.enabled)) &&
    allows(workspace, permission, screen.global ? null : undefined))
}

function moduleNavigation(workspace: Workspace, id: string) {
  return workspace.modules.some(module => module.id === id && module.enabled && module.navigation_allowed)
}

export function visibleScreen(workspace: Workspace, id: string) {
  const screen = screens.find(item => item.id === id)
  return !!screen && screenVisible(workspace, screen)
}

export function activeScreen(path: string, plans: boolean, hrSection?: string) {
  if (plans && path === '/maintenance') return 'preventive_maintenance'
  const current = path.replace(/\/$/, '') || '/'
  if (current === '/hr' && hrSection) return activeScreen('/hr/' + hrSection, false)
  return screens.flatMap(screen => [screen.path, ...(screen.aliases ?? [])]
    .filter(url => !url.includes('?') && (current === url || url !== '/' && current.startsWith(url + '/')))
    .map(url => ({ id: screen.id, length: url.length })))
    .sort((a, b) => b.length - a.length)[0]?.id
}
