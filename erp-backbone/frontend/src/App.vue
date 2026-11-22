<script setup lang="ts">
import { workspaceExtensions } from './extensions'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError, allows, type CompanyBrand, type Module, type ModuleView, type Workspace } from './api'
import { locale, message, t } from './i18n'
import WorkspacePage from './WorkspacePage.vue'
import ProfilePage from './ProfilePage.vue'
import SidebarNavigation from './SidebarNavigation.vue'
import ModuleCatalog from './ModuleCatalog.vue'
import TrainingCenter from './TrainingCenter.vue'
import GatewayPage from './GatewayPage.vue'
import SecurityHealth from './SecurityHealth.vue'
import AppFooter from './AppFooter.vue'
import PanelResizer from './PanelResizer.vue'
import CompanyProfilePage from './CompanyProfilePage.vue'
import NavigationSettings from './NavigationSettings.vue'
import { visibleScreen } from './navigation'
import SitesPage from './SitesPage.vue'
import AssetDetailPage from './AssetDetailPage.vue'
import ModuleVisibilityPage from './ModuleVisibilityPage.vue'
import AssetsPage from './AssetsPage.vue'
import OrdersPage from './OrdersPage.vue'
import PurchasePage from './PurchasePage.vue'
import PurchaseOrderPage from './PurchaseOrderPage.vue'
import DirectoryPage from './DirectoryPage.vue'
import OffboardingPage from './OffboardingPage.vue'
import EmployeeNumbering from './EmployeeNumbering.vue'
import PurchaseTemplatesPage from './PurchaseTemplatesPage.vue'
import DelegationsPage from './DelegationsPage.vue'
import ApprovalTemplatesPage from './ApprovalTemplatesPage.vue'
import UsersPage from './UsersPage.vue'
import AccessPage from './AccessPage.vue'
import PageGuidance from './PageGuidance.vue'
import AuditPage from './AuditPage.vue'
import SalesPage from './SalesPage.vue'
import MaintenancePage from './MaintenancePage.vue'
import BudgetingPage from './BudgetingPage.vue'
import ReportsPage from './ReportsPage.vue'
import PayablesPage from './PayablesPage.vue'
import AccountingPage from './AccountingPage.vue'
import InventoryPage from './InventoryPage.vue'
import SearchPage from './SearchPage.vue'
import CommandPalette from './CommandPalette.vue'
import HrPage from './HrPage.vue'
import AttendancePage from './AttendancePage.vue'
import NotificationsPage from './NotificationsPage.vue'
import DataImportPage from './DataImportPage.vue'
import InboxPage from './InboxPage.vue'
import DocumentSettingsPage from './DocumentSettingsPage.vue'

const SupplyPage = defineAsyncComponent(() => import('./SupplyPage.vue'))
const CrmPage = defineAsyncComponent(() => import('./CrmPage.vue'))
const AttendanceCorrections = defineAsyncComponent(() => import('./AttendanceCorrections.vue'))
const router = useRouter()
const route = useRoute()
const workspace = ref<Workspace>()
const workspaceRefresh = ref(0)

function onWorkspaceLink(event: MouseEvent) {
  if (route.name === 'home' && event.button === 0 && !event.metaKey && !event.altKey && !event.ctrlKey && !event.shiftKey) {
    workspaceRefresh.value++
  }
}
const pending = ref(true)
const username = ref('')
const password = ref('')
const code = ref('')
type Challenge = { mode: 'enroll'; setup_key: string } | { mode: 'verify' }
const challenge = ref<Challenge>()
const error = ref('')
const notice = ref('')
const busy = ref(false)
const mobileNav = ref(false)
const helpOpen = ref(false)
const menuButton = ref<HTMLButtonElement>()
const sideClose = ref<HTMLButtonElement>()
const collapseButton = ref<HTMLButtonElement>()
const contentShell = ref<HTMLElement>()
const sidebarCollapsed = ref(false)
const DEFAULT_UI_ZOOM = 1
const UI_ZOOM_LEVELS = [0.85, 0.9, 0.95, 1, 1.05, 1.1, 1.15, 1.2, 1.25]
const uiZoom = ref(DEFAULT_UI_ZOOM)
function uiZoomKey(user = workspace.value?.username) {
  return 'erp:ui-zoom:' + user
}
function clampUiZoom(value: number) {
  const rounded = Math.round(value * 100) / 100
  return Math.min(1.25, Math.max(0.85, Number.isFinite(rounded) ? rounded : DEFAULT_UI_ZOOM))
}
function setUiZoom(next: number) {
  uiZoom.value = clampUiZoom(next)
  if (!workspace.value) return
  try {
    if (uiZoom.value === DEFAULT_UI_ZOOM) localStorage.removeItem(uiZoomKey())
    else localStorage.setItem(uiZoomKey(), String(uiZoom.value))
  } catch { /* Keep the active zoom for this session. */ }
}
const uiZoomLabel = computed(() => {
  const pct = Math.round(uiZoom.value * 100)
  return locale.value === 'ar'
    ? pct.toLocaleString('ar-EG') + '٪'
    : pct + '%'
})
const helpButton = ref<HTMLButtonElement>()
const viewport = ref(window.innerWidth)
const mobile = computed(() => viewport.value <= 820)
const DEFAULT_SIDEBAR_WIDTH = 280
const sidebarWidth = ref(DEFAULT_SIDEBAR_WIDTH)
const helpWidth = ref(300)
const canDockHelp = computed(() => viewport.value >= 1080 &&
  viewport.value - (sidebarCollapsed.value ? 0 : sidebarWidth.value) - helpWidth.value >= 550)
const dockHelp = computed(() => helpOpen.value && canDockHelp.value)
const modalHelp = computed(() => mobile.value && helpOpen.value)
const modalNav = computed(() => mobile.value && mobileNav.value)
const mobileOverlay = computed(() => modalHelp.value || modalNav.value)
const brandName = computed(() => workspace.value?.company?.name || 'ERPi Pro')
const logoUrl = computed(() => workspace.value?.company?.logo_url)
const badLogo = ref(false)
watch(logoUrl, () => { badLogo.value = false })

type LayoutPanel = 'sidebar' | 'help'
function widthKey(panel: LayoutPanel, user = workspace.value?.username) {
  return 'erp:width:' + panel + ':' + user
}
function readWidth(panel: LayoutPanel, user: string, fallback: number, min: number, max: number) {
  const value = Number(localStorage.getItem(widthKey(panel,user)))
  return Number.isFinite(value) && value >= min && value <= max ? Math.round(value) : fallback
}
watch(() => workspace.value?.username, user => {
  sidebarWidth.value = DEFAULT_SIDEBAR_WIDTH
  helpWidth.value = 300
  helpOpen.value = false
  mobileNav.value = false
  sidebarCollapsed.value = false
  uiZoom.value = DEFAULT_UI_ZOOM
  if (!user) return
  try {
    const savedZoom = Number(localStorage.getItem(uiZoomKey(user)))
    if (Number.isFinite(savedZoom) && savedZoom >= 0.85 && savedZoom <= 1.25) {
      uiZoom.value = clampUiZoom(savedZoom)
    }
    sidebarWidth.value = readWidth('sidebar',user,DEFAULT_SIDEBAR_WIDTH,208,384)
    sidebarCollapsed.value = localStorage.getItem('erp:sidebar-collapsed:' + user) === '1'
    helpWidth.value = readWidth('help',user,300,256,472)
    helpOpen.value = canDockHelp.value && localStorage.getItem('erp:help:' + user) !== 'closed'
  } catch { helpOpen.value = canDockHelp.value }
})
function saveWidth(panel: LayoutPanel) {
  if (!workspace.value) return
  try {
    localStorage.setItem(widthKey(panel), String(panel === 'sidebar' ? sidebarWidth.value : helpWidth.value))
  } catch { /* The current session keeps the selected size. */ }
}
function resetLayout() {
  sidebarWidth.value = DEFAULT_SIDEBAR_WIDTH
  helpWidth.value = 300
  if (!workspace.value) return
  try {
    localStorage.removeItem(widthKey('sidebar'))
    localStorage.removeItem(widthKey('help'))
  } catch { /* The defaults are already applied. */ }
}
function collapseSidebar(collapsed: boolean) {
  sidebarCollapsed.value = collapsed
  if (workspace.value) {
    try { localStorage.setItem('erp:sidebar-collapsed:' + workspace.value.username, collapsed ? '1' : '0') }
    catch { /* Keep the current choice for this session. */ }
  }
  void nextTick(() => (collapsed ? menuButton : collapseButton).value?.focus())
}
function setMenu(open: boolean, restore = true) {
  mobileNav.value = open
  if (open) helpOpen.value = false
  void nextTick(() => {
    if (open) sideClose.value?.focus()
    else if (restore && mobile.value) menuButton.value?.focus()
  })
}
function setHelp(open: boolean) {
  if (open) mobileNav.value = false
  helpOpen.value = open
  if (workspace.value) {
    try { localStorage.setItem('erp:help:' + workspace.value.username, open ? 'open' : 'closed') }
    catch { /* Keep the current choice for this session. */ }
  }
  if (!open) void nextTick(() => helpButton.value?.focus())
  else void nextTick(() => document.querySelector<HTMLButtonElement>('.help-frame header button')?.focus())
}
function closeOverlay() {
  if (modalNav.value) setMenu(false)
  else setHelp(false)
}
function updateCompany(company: CompanyBrand) {
  if (workspace.value) workspace.value = {...workspace.value,company}
}
function measureViewport() { viewport.value = window.innerWidth }
watch(mobile, value => { if (!value) mobileNav.value = false })
watch(() => route.path, () => contentShell.value?.scrollTo(0,0))
onMounted(() => window.addEventListener('resize',measureViewport))
onBeforeUnmount(() => {
  window.removeEventListener('resize',measureViewport)
})
const preferredDark = matchMedia('(prefers-color-scheme: dark)')
const systemDark = ref(preferredDark.matches)
const syncTheme = () => { systemDark.value = preferredDark.matches }
const page = computed(() => route.name ?? 'home')
const extension = computed(() => workspaceExtensions.find(item => item.id === page.value))
const moduleId = computed(() => page.value === 'payroll' ? 'payroll' : ['hr', 'hrSetup', 'offboarding', 'employeeNumbering', 'attendance', 'attendanceCorrections', 'attendanceCorrectionRequest'].includes(String(page.value)) ? 'hr' : ['assets','assetDetail'].includes(String(page.value)) ? 'assets' : ['sales', 'sale'].includes(String(page.value)) ? 'sales' : page.value === 'accounting' ? 'accounting' :
  ['inventory', 'items', 'receipt', 'receive'].includes(String(page.value)) ? 'inventory' :
  ['purchases', 'purchase', 'purchaseTemplates'].includes(String(page.value)) ? 'purchasing' :
  ['purchaseOrders','purchaseOrder'].includes(String(page.value)) ? 'purchase_orders' :
  page.value === 'suppliers' ? 'suppliers' : page.value === 'customers' ? 'customers' : page.value === 'payables' ? 'supplier_invoices' :
  ['orders', 'order', 'approvalTemplates'].includes(String(page.value)) ? 'workorders' : undefined)
const active = computed(() => workspace.value?.modules.filter(module => module.visible) ?? [])
const navigation = computed(() => active.value.filter(module => module.show_in_navigation))
const navigationAllows = (id:string) => navigation.value.some(module => module.id === id)
const reportNavigation = computed(() => {
  const context = workspace.value
  if (!context) return undefined
  const show = (module:string, permission:string) => context.modules.some(row=>row.id===module && row.enabled && row.navigation_allowed) && allows(context,permission)
  if (show('accounting','accounting.view') || show('accounting','ap.view')) return '/reports/finance'
  if (show('purchasing','purchasing.view') || show('inventory','inventory.view')) return '/reports/stock'
  if (show('sales','sales.view')) return '/reports/sales'
  if (show('workorders','maintenance.view') && navigationAllows('assets')) return '/reports/maintenance'
})
const moduleAllowed = computed(() => active.value.some(module => module.id === moduleId.value))
const auditAllowed = computed(() => workspace.value && allows(workspace.value, 'platform.audit', null))
const searchTerm = ref('')
const commandPaletteOpen = ref(false)
const unread = ref(0)
const openTasks = ref(0)
const viewModes: ModuleView[] = ["list", "grid", "card"]
const viewLabels = {list:"List view",grid:"Grid view",card:"Card view"}
let noticeRequest = 0
const inboxBack = computed(() => {
  if (typeof route.query.fromInbox !== 'string' || (!moduleId.value && page.value !== 'payables')) return undefined
  const source = new URLSearchParams(route.query.fromInbox)
  const query: Record<string, string> = {}
  for (const key of ['kind', 'site', 'action']) {
    const value = source.get(key)
    if (value) query[key] = value
  }
  return {path: '/inbox', query: {...query, fromTask: '1'}}
})
function globalSearch() {
  if (searchTerm.value.trim().length >= 2) void router.push({path: '/search', query: {q: searchTerm.value.trim()}})
}
function onGlobalKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    if (!workspace.value) return
    event.preventDefault()
    commandPaletteOpen.value = !commandPaletteOpen.value
    return
  }
  if (event.altKey && !event.ctrlKey && !event.metaKey && workspace.value) {
    if (event.key === '=' || event.key === '+') { event.preventDefault(); setUiZoom(uiZoom.value + 0.05) }
    else if (event.key === '-') { event.preventDefault(); setUiZoom(uiZoom.value - 0.05) }
    else if (event.key === '0') { event.preventDefault(); setUiZoom(DEFAULT_UI_ZOOM) }
  }
}
let counterPending = false
let counterAgain = false
async function refreshCounters() {
  const user = workspace.value?.username
  if (!user) { ++noticeRequest; unread.value = 0; openTasks.value = 0; return }
  if (document.hidden) return
  if (counterPending) { counterAgain = true; return }
  counterPending = true
  const request = ++noticeRequest
  try {
    const result = await api<{inbox:number; unread:number}>('/workspace/counters')
    if (request === noticeRequest && workspace.value?.username === user) {
      unread.value = result.unread
      openTasks.value = result.inbox
    }
  } catch {
    // Keep the last known counts on temporary network failures.
  } finally {
    counterPending = false
    if (counterAgain) { counterAgain = false; void refreshCounters() }
  }
}
function updateUnread(count: number) {
  ++noticeRequest
  unread.value = count
}
watch(() => workspace.value?.username, refreshCounters)
watch(() => route.fullPath, () => {
  if (route.name === 'search' && typeof route.query.q === 'string') searchTerm.value = route.query.q
})
const noticeTimer = window.setInterval(refreshCounters, 60000)
document.addEventListener('visibilitychange', refreshCounters)
window.addEventListener('erp:work-changed', refreshCounters)
onBeforeUnmount(() => {
  window.clearInterval(noticeTimer)
  document.removeEventListener('visibilitychange', refreshCounters)
  window.removeEventListener('erp:work-changed', refreshCounters)
})


watch([locale, systemDark, () => workspace.value?.theme], ([language, dark, theme]) => {
  document.documentElement.lang = language
  document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr'
  document.documentElement.dataset.theme = theme === 'dark' || (!theme || theme === 'system') && dark ? 'dark' : 'light'
}, { immediate: true })

watch(() => route.fullPath, () => {
  error.value = ''
  notice.value = ''
  if (mobileNav.value) setMenu(false)
  if (modalHelp.value) setHelp(false)
  if (workspace.value) void refreshWorkspace()
})

async function refreshWorkspace() {
  const current = workspace.value
  try {
    const updated = await api<Workspace>('/workspace')
    if (workspace.value === current) workspace.value = updated
  }
  catch (reason) { error.value = message(reason) }
}

async function load() {
  error.value = ''
  try {
    const session = await api<{ user: object | null; mfa?: Challenge }>('/session')
    challenge.value = session.mfa
    if (session.user) {
      workspace.value = await api<Workspace>('/workspace')
      locale.value = workspace.value.language
    }
  } catch (reason) { error.value = message(reason) }
  finally { pending.value = false }
}

function expired() {
  workspace.value = undefined
  challenge.value = undefined
  error.value = 'Your session ended. Sign in again.'
}

onMounted(() => {
  preferredDark.addEventListener('change', syncTheme)
  window.addEventListener('erp:session-expired', expired)
  window.addEventListener('keydown', onGlobalKeydown)
  void load()
})
onBeforeUnmount(() => {
  preferredDark.removeEventListener('change', syncTheme)
  window.removeEventListener('erp:session-expired', expired)
  window.removeEventListener('keydown', onGlobalKeydown)
})

async function run(operation: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try { await operation() }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}

function signIn() {
  void run(async () => {
    const result = await api<{ ok: boolean; mfa?: Challenge }>('/login', 'POST', {
      username: username.value, password: password.value,
    })
    password.value = ''
    challenge.value = result.mfa
    if (result.ok) await load()
  })
}

function verifyCode() {
  void run(async () => {
    try {
      await api('/login/mfa', 'POST', { code: code.value })
      await load()
    } catch (reason) {
      if (reason instanceof ApiError && ['mfa_expired', 'mfa_locked'].includes(reason.code)) {
        challenge.value = undefined
      }
      throw reason
    } finally {
      code.value = ''
    }
  })
}

function cancelVerification() {
  void run(async () => {
    await api('/logout', 'POST', {})
    challenge.value = undefined
    code.value = ''
  })
}

function signOut() {
  void run(async () => {
    await api('/logout', 'POST', {})
    workspace.value = undefined
    username.value = ''
    await router.replace('/')
  })
}

function activationChainFor(target: Module, allModules: Module[]): Module[] {
  const byId = new Map(allModules.map(m => [m.id, m]))
  const visited = new Set<string>()
  const ordered: Module[] = []
  function visit(mod: Module) {
    if (visited.has(mod.id)) return
    visited.add(mod.id)
    for (const depId of mod.required_dependencies ?? []) {
      const dep = byId.get(depId)
      if (dep) visit(dep)
    }
    ordered.push(mod)
  }
  visit(target)
  return ordered
}

function toggle(module: Module) {
  const modules = workspace.value?.modules ?? []
  const restricted = module.enabled ? modules.filter(row => row.enabled && row.required_dependencies.includes(module.id))
    : modules.filter(row => !row.enabled && module.required_dependencies.includes(row.id))
  if (restricted.length) {
    error.value = t(module.enabled ? 'Required by enabled modules' : 'Enable these dependencies first')
      + ' · ' + restricted.map(row => row.name[locale.value]).join(', ')
    return
  }
  void run(async () => {
    const updated = await api<{ modules: Module[] }>('/modules/' + module.id, 'PATCH', { enabled: !module.enabled })
    if (workspace.value) workspace.value.modules = updated.modules
    notice.value = 'Changes saved.'
  })
}

function toggleChain(target: Module) {
  const modules = workspace.value?.modules ?? []
  const steps = activationChainFor(target, modules).filter(m => !m.enabled)
  if (!steps.length) return
  void run(async () => {
    for (const step of steps) {
      const updated = await api<{ modules: Module[] }>('/modules/' + step.id, 'PATCH', { enabled: true })
      if (workspace.value) workspace.value.modules = updated.modules
    }
    notice.value = 'Changes saved.'
  })
}

function saveModuleView(module_view: ModuleView) {
  void run(async () => {
    const selected = await api<{module_view: ModuleView}>('/preferences/module-view', 'PATCH', {module_view})
    if (workspace.value) workspace.value.module_view = selected.module_view
  })
}
function updateProfile(name: string, picture: string | null) {
  if (workspace.value) workspace.value = {...workspace.value, display_name: name, picture_url: picture}
}
function updateNavigation(hidden: string[]) {
  if (workspace.value) workspace.value = {...workspace.value, hidden_screens: hidden}
}
function savePreferences() {
  if (!workspace.value) return
  const { language, theme } = workspace.value
  void run(async () => {
    await api('/preferences', 'PATCH', { language, theme })
    notice.value = 'Changes saved.'
  })
}
</script>

<template>
  <div v-if="pending" class="center-screen">{{ t('Loading') }}…</div>
  <main v-else-if="!workspace" class="center-screen">
    <form v-validated-form v-if="challenge" class="login-card panel" @submit.prevent="verifyCode">
      <span class="brand-mark" aria-hidden="true">E</span>
      <h1>{{ t('Two-step verification') }}</h1>
      <template v-if="challenge.mode === 'enroll'">
        <p class="muted">{{ t('Add this key to your authenticator app as a time-based account.') }}</p>
        <p class="authenticator-key" dir="ltr"><code>{{ challenge.setup_key }}</code></p>
      </template>
      <p class="muted">{{ t('Enter the six-digit code from your authenticator app.') }}</p>
      <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
      <div class="field-wrap"><label>{{ t('Verification code') }}
        <input data-field="code" :placeholder="t('e.g., CODE-001')" v-model="code" dir="ltr" inputmode="numeric" autocomplete="one-time-code"
          pattern="[0-9]{6}" maxlength="6" required>
      </label><FieldHint :label="t('Verification code')" required /></div>
      <button class="primary" :disabled="busy">{{ t('Verify') }}</button>
      <button class="secondary" type="button" :disabled="busy" @click="cancelVerification">{{ t('Back to sign in') }}</button>
    </form>
    <form v-validated-form v-else class="login-card panel" @submit.prevent="signIn">
      <span class="brand-mark" aria-hidden="true">E</span>
      <h1>ERPi Pro</h1>
      <p class="muted">{{ t('Sign in to manage your company workspace.') }}</p>
      <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
      <div class="field-wrap"><label>{{ t('Username') }}<input data-field="username" :placeholder="t('e.g., john.doe')" v-model="username" autocomplete="username" required maxlength="150"></label><FieldHint :label="t('Username')" required /></div>
      <div class="field-wrap"><label>{{ t('Password') }}<input data-field="password" v-model="password" type="password" autocomplete="current-password" required maxlength="256"></label><FieldHint :label="t('Password')" required /></div>
      <button class="primary" :disabled="busy">{{ t('Sign in') }}</button>
    </form>
  </main>
  <div v-else class="app-shell" :style="{ zoom: uiZoom }">
    <div class="layout-body" :class="{'dock-help':dockHelp,'nav-collapsed':sidebarCollapsed && !mobile}"
      :style="{'--sidebar-width':sidebarWidth+'px','--help-width':helpWidth+'px'}">
    <div v-if="mobileOverlay" class="layout-backdrop" aria-hidden="true" @click="closeOverlay" />
    <aside v-show="mobile || !sidebarCollapsed" id="workspace-sidebar" class="sidebar" :class="{open:modalNav}" :role="modalNav ? 'dialog' : undefined"
      :aria-modal="modalNav || undefined" :aria-label="t('Menu')" :inert="modalHelp || undefined"
      @keydown.esc="modalNav && setMenu(false)">
      <div class="sidebar-brand-row">
        <RouterLink class="brand" to="/">
          <img v-if="logoUrl && !badLogo" class="company-logo" :src="logoUrl" alt="" @error="badLogo = true">
          <span v-else class="brand-mark" aria-hidden="true">E</span><bdi>{{ brandName }}</bdi>
        </RouterLink>
        <button v-if="modalNav" ref="sideClose" class="sidebar-close secondary" type="button"
          :aria-label="t('Close menu')" @click="setMenu(false)">×</button>
        <button v-else-if="!mobile" ref="collapseButton" class="sidebar-collapse" type="button"
          :aria-label="t('Collapse sidebar')" :title="t('Collapse sidebar')" @click="collapseSidebar(true)">
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
            <rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16m7-9-3 2 3 2"/>
          </svg>
        </button>
      </div>
      <SidebarNavigation :workspace="workspace" :reports="reportNavigation" @reset-layout="resetLayout" />
      <PanelResizer v-if="!mobile && !sidebarCollapsed" :width="sidebarWidth" :min="208" :max="384" edge="start" label="Resize sidebar"
        controls="workspace-sidebar" @resize="sidebarWidth = $event" @commit="saveWidth('sidebar')" />
    </aside>
    <div id="workspace-content" ref="contentShell" class="main-shell" :inert="mobileOverlay || undefined">
      <header class="topbar">
        <button v-if="mobile || sidebarCollapsed" ref="menuButton" class="menu-button secondary" type="button"
          @click="mobile ? setMenu(!mobileNav) : collapseSidebar(false)"
          :aria-label="t(mobile ? 'Menu' : 'Expand sidebar')" :title="t(mobile ? 'Menu' : 'Expand sidebar')"
          :aria-expanded="mobile ? modalNav : false" aria-controls="workspace-sidebar">☰</button>
        <RouterLink class="top-title" :to="route.name === 'home' ? route.fullPath : '/'" @click="onWorkspaceLink">
          {{ t('Workspace') }}
        </RouterLink>
        <form v-validated-form class="workspace-search field-wrap table-field" role="search" @submit.prevent="globalSearch">
          <input data-field="searchTerm" v-model="searchTerm" type="search" :aria-label="t('Global search')" :placeholder="t('Global search')" maxlength="120">
          <FieldHint :label="t('Global search')" />
        </form>
        <button type="button" class="secondary command-trigger" @click="commandPaletteOpen = true" :aria-label="t('Command palette')" :title="t('Command palette') + ' (Ctrl+K)'"><kbd dir="ltr">Ctrl+K</kbd></button>
        <div class="user-bar">
          <div class="ui-zoom-group" role="group" :aria-label="t('Interface zoom')">
            <button type="button" class="secondary ui-zoom-btn" :disabled="uiZoom <= 0.85"
              :aria-label="t('Zoom out')" :title="t('Zoom out') + ' (Alt+-)'" @click="setUiZoom(uiZoom - 0.05)">−</button>
            <button type="button" class="secondary ui-zoom-readout" :disabled="uiZoom === 1"
              :aria-label="t('Reset zoom')" :title="t('Reset zoom') + ' (Alt+0)'" @click="setUiZoom(DEFAULT_UI_ZOOM)">{{ uiZoomLabel }}</button>
            <button type="button" class="secondary ui-zoom-btn" :disabled="uiZoom >= 1.25"
              :aria-label="t('Zoom in')" :title="t('Zoom in') + ' (Alt+=)'" @click="setUiZoom(uiZoom + 0.05)">+</button>
          </div>
          <button ref="helpButton" type="button" class="secondary" aria-controls="context-help" :aria-expanded="helpOpen"
            @click="setHelp(!helpOpen)">{{ t('Help') }}</button>
          <RouterLink v-if="visibleScreen(workspace, 'inbox')" :to="{path:'/inbox',query:{fromTask:'1'}}" class="button secondary top-action" :aria-label="t('Inbox') + ' · ' + openTasks">
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 4h16l2 10v6H2v-6L4 4ZM2 14h6l2 3h4l2-3h6"/></svg>
            <span>{{ t('Inbox') }}</span><bdi class="top-count">{{ openTasks }}</bdi>
          </RouterLink>
          <RouterLink v-if="visibleScreen(workspace, 'notifications')" to="/notifications" class="button secondary top-action" :aria-label="t('Notifications') + ' · ' + unread">
            <svg aria-hidden="true" class="notification-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg><span>{{ t('Notifications') }}</span><bdi class="top-count">{{ unread }}</bdi>
          </RouterLink>
          <RouterLink v-if="visibleScreen(workspace, 'profile')" to="/profile" class="profile-link" :title="workspace.display_name">
            <img v-if="workspace.picture_url" :src="workspace.picture_url" alt="" class="profile-avatar">
            <bdi>{{ workspace.display_name }}</bdi>
          </RouterLink><button class="secondary" type="button" :disabled="busy" @click="signOut">{{ t('Sign out') }}</button></div>
      </header>
      <main class="main-content"><PageGuidance/>
        <RouterLink v-if="inboxBack && visibleScreen(workspace, 'inbox')" class="button secondary inbox-back" :to="inboxBack">‹ {{ t('Back to Inbox') }}</RouterLink>
        <p v-if="error" class="alert" role="alert">{{ t(error) }}</p>
        <p v-if="notice" class="success" role="status">{{ t(notice) }}</p>
        <WorkspacePage v-if="page === 'home'" :workspace="workspace" :refresh-key="workspaceRefresh" />
        <component v-else-if="extension && visibleScreen(workspace, extension.id)" :is="extension.component" :workspace="workspace" />
        <SupplyPage v-else-if="page === 'supply' && visibleScreen(workspace, 'supply')" :workspace="workspace" />
        <CrmPage v-else-if="page === 'crm' && visibleScreen(workspace, 'crm')" :workspace="workspace" />
        <ProfilePage v-else-if="page === 'profile'" @saved="updateProfile" />
        <template v-else-if="page === 'modules' && workspace.admin">
          <div class="page-heading">
            <h1>{{ t('Module Manager') }}</h1>
            <div class="actions" role="group" :aria-label="t('Module view')">
              <button v-for="mode in viewModes" :key="mode" type="button"
                :class="workspace.module_view === mode ? 'primary' : 'secondary'"
                :disabled="busy" :aria-pressed="workspace.module_view === mode"
                @click="saveModuleView(mode)">{{ t(viewLabels[mode]) }}</button>
            </div>
          </div>
          <ModuleCatalog :modules="workspace.modules" :view="workspace.module_view" :busy="busy" @toggle="toggle" @toggle-chain="toggleChain" />
          <p class="muted">{{ t('Disabling a module keeps its records.') }}</p>
          <div class="actions module-admin-links">
            <RouterLink class="button secondary" to="/admin/access-control">{{ t('Access control') }}</RouterLink>
            <RouterLink class="button secondary" to="/module-visibility">{{ t('Module visibility') }}</RouterLink>
          </div>
          <NavigationSettings :hidden="workspace.hidden_screens" @saved="updateNavigation" />
        </template>
        <template v-else-if="moduleId && !moduleAllowed">
          <h1>{{ t('Module unavailable') }}</h1>
          <p class="muted">{{ t('This module is disabled or outside your permissions.') }}</p>
          <RouterLink class="button secondary" to="/">{{ t('Back to workspace') }}</RouterLink>
        </template>
        <SitesPage v-else-if="page === 'sites' && workspace.admin" @changed="refreshWorkspace" />
        <ModuleVisibilityPage v-else-if="page === 'moduleVisibility' && workspace.admin" :workspace="workspace" />
        <AssetDetailPage v-else-if="page === 'assetDetail'" :workspace="workspace" />
        <AssetsPage v-else-if="page === 'assets'" :workspace="workspace" />
        <ApprovalTemplatesPage v-else-if="page === 'approvalTemplates' && workspace.admin" />
        <PurchasePage v-else-if="page === 'purchases' || page === 'purchase'" :workspace="workspace" />
        <PurchaseOrderPage v-else-if="page === 'purchaseOrders' || page === 'purchaseOrder'" :workspace="workspace" />
        <DirectoryPage v-else-if="page === 'suppliers'" :workspace="workspace" kind="supplier" />
        <DirectoryPage v-else-if="page === 'customers'" :workspace="workspace" kind="customer" />
        <DirectoryPage v-else-if="page === 'items'" :workspace="workspace" kind="item" />
        <DirectoryPage v-else-if="page === 'hrSetup' && allows(workspace, 'hr.manage')" :workspace="workspace" :kind="String(route.params.kind)" />
        <OffboardingPage v-else-if="page === 'offboarding' && (allows(workspace,'hr.offboarding.request') || allows(workspace,'hr.offboarding.approve'))" :workspace="workspace" />
        <EmployeeNumbering v-else-if="page === 'employeeNumbering' && workspace.admin" />
        <PurchaseTemplatesPage v-else-if="page === 'purchaseTemplates' && workspace.admin" />
        <OrdersPage v-else-if="page === 'orders' || page === 'order'" :workspace="workspace" />
        <GatewayPage v-else-if="page==='gateway' && workspace.admin" />
        <SecurityHealth v-else-if="page==='securityHealth' && workspace.admin" />
        <UsersPage v-else-if="page === 'adminUsers' && workspace.admin" :workspace="workspace" @changed="refreshWorkspace" />
        <AccessPage v-else-if="page === 'access' && workspace.admin" :workspace="workspace" @changed="refreshWorkspace" />
        <DelegationsPage v-else-if="page === 'delegations'" :workspace="workspace" />
        <AuditPage v-else-if="page === 'audit' && auditAllowed" :workspace="workspace" />
        <SalesPage v-else-if="['sales', 'sale'].includes(String(page))" :workspace="workspace" />
        <MaintenancePage v-else-if="['maintenance', 'maintenanceAsset'].includes(String(page)) && allows(workspace, 'maintenance.view')" :workspace="workspace" />
        <BudgetingPage v-else-if="page === 'budgeting' && allows(workspace,'budget.view')" :workspace="workspace" />
        <ReportsPage v-else-if="page === 'reportsDashboard' || page === 'reportsDetail'" :workspace="workspace" />
        <PayablesPage v-else-if="page === 'payables' && allows(workspace, 'ap.view')" :workspace="workspace" />
        <AccountingPage v-else-if="page === 'accounting'" :workspace="workspace" />
        <InventoryPage v-else-if="['inventory', 'receipt', 'receive'].includes(String(page))" :workspace="workspace" />
        <DataImportPage v-else-if="page === 'dataImport' && workspace.admin" :workspace="workspace" />
        <InboxPage v-else-if="page === 'inbox'" :workspace="workspace" />
        <SearchPage v-else-if="page === 'search'" />
        <AttendanceCorrections v-else-if="['attendanceCorrections', 'attendanceCorrectionRequest'].includes(String(page)) && ['attendance.record','attendance.correct'].some(permission => allows(workspace!, permission))" />
        <AttendancePage v-else-if="page === 'attendance' && ['attendance.record','attendance.approve','attendance.setup','attendance.reopen','attendance.correct'].some(permission => allows(workspace!, permission))" :workspace="workspace" />
        <HrPage v-else-if="page === 'hr'" key="hr" :workspace="workspace" />
        <HrPage v-else-if="page === 'payroll'" key="payroll" :workspace="workspace" area="payroll" />
        <NotificationsPage v-else-if="page === 'notifications'" @unread="updateUnread" />
        <DocumentSettingsPage v-else-if="page === 'documentSettings' && workspace.admin" :workspace="workspace" />
        <CompanyProfilePage v-else-if="page === 'companyProfile' && workspace.admin" @saved="updateCompany" />
        <template v-else-if="page === 'settings'">
          <div class="page-heading">
            <h1>{{ t('Settings') }}</h1>
            <RouterLink v-if="workspace.admin" to="/settings/company" class="button secondary">{{ t('Company Profile') }}</RouterLink>
          </div>
          <form v-validated-form class="panel settings-form" @submit.prevent="savePreferences">
            <div class="field-wrap"><label>{{ t('Language') }}<select data-field="language" v-model="workspace.language" @change="locale = workspace.language"><option value="en">English</option><option value="ar">العربية</option></select></label><FieldHint :label="t('Language')" /></div>
            <div class="field-wrap"><label>{{ t('Theme') }}<select data-field="theme" v-model="workspace.theme"><option value="system">{{ t('System') }}</option><option value="light">{{ t('Light') }}</option><option value="dark">{{ t('Dark') }}</option></select></label><FieldHint :label="t('Theme')" /></div>
            <div class="field-wrap"><label>{{ t('Interface zoom') }}<select data-field="uiZoom" :value="uiZoom" @change="setUiZoom(Number(($event.target as HTMLSelectElement).value))"><option v-for="lvl in UI_ZOOM_LEVELS" :key="lvl" :value="lvl">{{ locale === 'ar' ? Math.round(lvl * 100).toLocaleString('ar-EG') + '٪' : Math.round(lvl * 100) + '%' }}</option></select></label><FieldHint :label="t('Interface zoom')" /></div>
            <button class="primary" :disabled="busy">{{ t('Save') }}</button>
          </form>
          <p class="muted">{{ t('Version') }} <bdi>{{ workspace.platform_version }}</bdi></p>
        </template>
        <template v-else-if="page === 'notFound'">
          <h1>{{ t('Page not found') }}</h1>
          <RouterLink class="button secondary" to="/">{{ t('Workspace') }}</RouterLink>
        </template>
        <h1 v-else>{{ t('Admin access required') }}</h1>
      </main>
    </div>
    <Transition name="help-drawer">
      <aside v-if="helpOpen" id="context-help" class="help-frame" :role="modalHelp ? 'dialog' : undefined"
        :aria-modal="modalHelp || undefined" :aria-label="t('Page help')" :inert="modalNav || undefined">
        <TrainingCenter :key="workspace.username" :username="workspace.username" @close="setHelp(false)" />
        <PanelResizer v-if="!mobile" :width="helpWidth" :min="256" :max="472" edge="end" label="Resize help"
          controls="context-help" @resize="helpWidth = $event" @commit="saveWidth('help')" />
      </aside>
    </Transition>
    </div>
    <AppFooter :version="workspace.platform_version" :mode="workspace.mode" :inert="mobileOverlay || undefined" />
    <CommandPalette :open="commandPaletteOpen" @close="commandPaletteOpen = false" />
  </div>
</template>
