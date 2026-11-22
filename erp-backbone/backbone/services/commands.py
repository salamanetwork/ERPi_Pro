from dataclasses import dataclass, field
from typing import Callable

from django.views.decorators.cache import never_cache

from backbone.access import has_permission, preload_scopes
from backbone.http import endpoint, string
from backbone.registry import MANIFESTS, enabled_modules
from .experience import document_results, master_results


@dataclass(frozen=True)
class CommandSpec:
    id: str
    category: str  # "action" | "navigation" | "record"
    module: str
    title_en: str
    title_ar: str
    subtitle_en: str
    subtitle_ar: str
    route: str
    permissions: tuple[str, ...] = ()
    global_scope: bool = False
    admin_only: bool = False
    keywords: tuple[str, ...] = field(default_factory=tuple)
    predicate: Callable | None = None


COMMAND_REGISTRY: list[CommandSpec] = []


def register_command(spec: CommandSpec) -> CommandSpec:
    if not any(existing.id == spec.id for existing in COMMAND_REGISTRY):
        COMMAND_REGISTRY.append(spec)
    return spec


def _seed_core_commands() -> None:
    specs = [
        CommandSpec("action.new_pr", "action", "purchasing", "New Purchase Requisition", "طلب شراء جديد",
                    "Procurement · Create purchase request", "المشتريات · إنشاء طلب شراء",
                    "/purchase-requests", ("purchasing.create", "purchasing.view"),
                    keywords=("pr", "requisition", "purchase", "طلب شراء", "مشتريات")),
        CommandSpec("action.new_wo", "action", "workorders", "New Work Order", "أمر عمل جديد",
                    "Maintenance · Create maintenance work order", "الصيانة · إنشاء أمر عمل صيانة",
                    "/work-orders", ("workorders.create", "workorders.view"),
                    keywords=("wo", "work order", "maintenance", "أمر عمل", "صيانة")),
        CommandSpec("action.new_sale", "action", "sales", "New Sales Document", "مستند مبيعات جديد",
                    "Sales · Create quotation or sales order", "المبيعات · إنشاء عرض سعر أو أمر بيع",
                    "/sales", ("sales.create", "sales.view"),
                    keywords=("quote", "sales order", "invoice", "عرض سعر", "مبيعات", "فاتورة")),
        CommandSpec("action.new_customer", "action", "customers", "New Customer", "عميل جديد",
                    "Sales · Register customer master record", "المبيعات · تسجيل بطاقة عميل جديد",
                    "/sales/customers/new", ("sales.manage", "sales.view"),
                    keywords=("customer", "client", "عميل", "العملاء")),
        CommandSpec("action.new_supplier", "action", "suppliers", "New Supplier", "مورد جديد",
                    "Procurement · Register supplier master record", "المشتريات · تسجيل بطاقة مورد جديد",
                    "/suppliers/new", ("suppliers.manage",), global_scope=True,
                    keywords=("supplier", "vendor", "مورد", "الموردين")),
        CommandSpec("action.new_item", "action", "inventory", "New Inventory Item", "صنف مخزني جديد",
                    "Inventory · Register stock item", "المخزون · تسجيل صنف مخزني جديد",
                    "/inventory/items/new", ("inventory.setup", "inventory.view"),
                    keywords=("item", "sku", "product", "صنف", "مخزون")),
        CommandSpec("action.new_employee", "action", "hr", "New Employee", "موظف جديد",
                    "Human Resources · Onboard employee", "الموارد البشرية · إضافة ملف موظف",
                    "/hr/employees/new", ("hr.manage",),
                    keywords=("employee", "staff", "hire", "موظف", "تعيين")),
        CommandSpec("action.new_crm_lead", "action", "crm", "New CRM Lead", "فرصة تسويقية جديدة",
                    "CRM · Capture new sales lead", "إدارة علاقات العملاء · تسجيل عميل محتمل",
                    "/crm/leads/new", ("crm.manage", "crm.view"),
                    keywords=("lead", "crm", "prospect", "عميل محتمل")),
        CommandSpec("nav.workspace", "navigation", "platform", "Workspace Dashboard", "لوحة مساحة العمل",
                    "Overview · KPIs and operational shortcuts", "نظرة عامة · المؤشرات والاختصارات التشغيلية",
                    "/", keywords=("home", "dashboard", "kpi", "الرئيسية", "لوحة التحكم")),
        CommandSpec("nav.inbox", "navigation", "platform", "Approval Inbox", "صندوق المهام والاعتمادات",
                    "Workflow · Pending reviews and actions", "سير العمل · المراجعات والاعتمادات المعلقة",
                    "/inbox", keywords=("inbox", "approvals", "tasks", "اعتمادات", "مهام")),
        CommandSpec("nav.search", "navigation", "platform", "Global Record Search", "البحث الشامل في السجلات",
                    "Search · Find documents, parties, items & codes", "بحث · المستندات والأطراف والأصناف والأكواد",
                    "/search", keywords=("search", "find", "lookup", "record", "بحث", "سجل", "استعلام")),
        CommandSpec("nav.notifications", "navigation", "platform", "Notifications", "الإشعارات والتنبيهات",
                    "Workspace · System alerts and activity notices", "مساحة العمل · التنبيهات وإشعارات النظام",
                    "/notifications", keywords=("notifications", "alerts", "إشعارات", "تنبيهات")),
        CommandSpec("nav.profile", "navigation", "platform", "User Profile", "الملف الشخصي",
                    "Account · Personal details, avatar & security", "الحساب · البيانات الشخصية والصورة والأمان",
                    "/profile", keywords=("profile", "account", "avatar", "الملف الشخصي", "حسابي")),
        CommandSpec("nav.accounting", "navigation", "accounting", "General Accounting & Journals", "المحاسبة العامة والقيود",
                    "Finance · Chart of accounts, journals & postings", "المالية · دليل الحسابات والقيود والترحيل",
                    "/accounting", ("accounting.view",), global_scope=True,
                    keywords=("gl", "journal", "ledger", "accounting", "محاسبة", "قيود", "دفتر الأستاذ")),
        CommandSpec("nav.payables", "navigation", "supplier_invoices", "Supplier Invoices & Payables", "فواتير الموردين والذمم الدائنة",
                    "Procurement · AP invoices and aging", "المشتريات · فواتير الموردين وأعمار الديون",
                    "/payables/invoices", ("ap.view",),
                    keywords=("ap", "payable", "bill", "invoice", "فواتير الموردين", "ذمم دائنة")),
        CommandSpec("nav.budgeting", "navigation", "accounting", "Cost Centers & Budgets", "مراكز التكلفة والموازنات",
                    "Finance · Budget tracking and variance", "المالية · متابعة الموازنات والانحرافات",
                    "/budgeting/report", ("budget.view",),
                    keywords=("budget", "cost center", "موازنة", "مراكز التكلفة")),
        CommandSpec("nav.purchase_orders", "navigation", "purchase_orders", "Purchase Orders", "أوامر الشراء",
                    "Procurement · Issued and pending POs", "المشتريات · أوامر الشراء الصادرة والمعلقة",
                    "/purchase-orders", ("purchasing.view",),
                    keywords=("po", "purchase order", "أمر شراء", "أوامر الشراء")),
        CommandSpec("nav.inventory_balances", "navigation", "inventory", "Stock Balances", "أرصدة المخزون",
                    "Inventory · Real-time warehouse quantities", "المخزون · الكميات المتوفرة بالمستودعات",
                    "/inventory/balances", ("inventory.view",),
                    keywords=("stock", "balance", "warehouse", "أرصدة", "مستودع")),
        CommandSpec("nav.goods_receipts", "navigation", "inventory", "Goods Receipts (GRN)", "إيصالات استلام البضائع",
                    "Inventory · Inbound deliveries", "المخزون · استلام المشتريات الواردة",
                    "/inventory/receipts", ("inventory.view",),
                    keywords=("grn", "receipt", "receive", "استلام بضائع")),
        CommandSpec("nav.assets", "navigation", "assets", "Assets Register", "سجل الأصول",
                    "Assets & Maintenance · Equipment and tags", "الأصول والصيانة · المعدات وبطاقات الأصول",
                    "/assets", ("assets.view",),
                    keywords=("asset", "equipment", "tag", "أصول", "معدات")),
        CommandSpec("nav.maintenance", "navigation", "workorders", "Preventive & Corrective Maintenance", "الصيانة الوقائية والتصحيحية",
                    "Assets & Maintenance · Maintenance schedules", "الأصول والصيانة · خطط وجداول الصيانة",
                    "/maintenance", ("maintenance.view",),
                    keywords=("pm", "preventive", "maintenance", "صيانة وقائية")),
        CommandSpec("nav.hr_employees", "navigation", "hr", "Employees Directory", "دليل الموظفين",
                    "Human Resources · Staff records and contracts", "الموارد البشرية · ملفات الموظفين والعقود",
                    "/hr/employees", ("hr.view", "hr.manage", "hr.self"),
                    keywords=("hr", "employees", "directory", "الموظفين", "الموارد البشرية")),
        CommandSpec("nav.attendance", "navigation", "hr", "Time & Attendance", "الحضور والانصراف",
                    "Human Resources · Shifts, sessions & corrections", "الموارد البشرية · الورديات وسجلات الحضور",
                    "/hr/attendance", ("attendance.record", "attendance.approve", "attendance.setup"),
                    keywords=("attendance", "clock", "shift", "حضور", "انصراف")),
        CommandSpec("nav.payroll", "navigation", "payroll", "Payroll Runs", "مسيرات الرواتب",
                    "Payroll · Monthly salary runs and approvals", "الرواتب · دورات الرواتب الشهرية والاعتمادات",
                    "/payroll/runs", ("payroll.prepare", "payroll.approve", "payroll.post", "payroll.summary"),
                    keywords=("payroll", "salary", "payslip", "رواتب", "مسير")),
        CommandSpec("nav.reports", "navigation", "platform", "Reports & Dashboards", "التقارير ولوحات المتابعة",
                    "Analytics · Financial, stock, sales & maintenance", "التحليلات · تقارير المالية والمخزون والمبيعات والصيانة",
                    "/reports", ("accounting.view", "ap.view", "purchasing.view", "inventory.view", "sales.view", "maintenance.view"),
                    keywords=("reports", "analytics", "charts", "تقارير", "تحليلات")),
        CommandSpec("nav.modules", "navigation", "platform", "Module Manager", "إدارة الموديولات",
                    "Administration · Enable, order & inspect installed modules", "الإدارة · تفعيل وترتيب ومراجعة الوحدات المثبتة",
                    "/modules", admin_only=True,
                    keywords=("modules", "plugins", "extensions", "dependencies", "وحدات", "إدارة الوحدات", "اعتماديات")),
        CommandSpec("nav.integrations", "navigation", "platform", "Integrations & Webhooks", "التكاملات وروابط الويب",
                    "Administration · External API connectors & outbound hooks", "الإدارة · موصلات الأنظمة الخارجية والويب هوك",
                    "/admin/integrations", admin_only=True,
                    keywords=("integrations", "webhooks", "connectors", "api", "تكاملات", "ربط", "ويب هوك")),
        CommandSpec("nav.security_health", "navigation", "platform", "Security Health", "صحة الأمان",
                    "Administration · MFA posture, keys & security diagnostics", "الإدارة · حالة التحقق الثنائي والمفاتيح وفحص الأمان",
                    "/admin/security-health", admin_only=True,
                    keywords=("security", "health", "mfa", "keys", "أمان", "صحة الأمان", "حماية")),
        CommandSpec("nav.users", "navigation", "platform", "Users & Accounts", "المستخدمون والحسابات",
                    "Administration · Manage user accounts & MFA status", "الإدارة · إدارة حسابات المستخدمين وحالة التحقق",
                    "/admin/users", admin_only=True,
                    keywords=("users", "accounts", "staff", "password", "مستخدمين", "المستخدمون", "حسابات")),
        CommandSpec("nav.access_control", "navigation", "platform", "Access Control & Roles", "التحكم بالصلاحيات والأدوار",
                    "Administration · Roles, permissions & site assignments", "الإدارة · الأدوار والصلاحيات وتعيين الفروع",
                    "/admin/access-control", admin_only=True,
                    keywords=("access", "roles", "permissions", "rbac", "صلاحيات", "أدوار", "التحكم بالصلاحيات")),
        CommandSpec("nav.sites", "navigation", "platform", "Sites & Branches", "المواقع والفروع",
                    "Administration · Operational locations & branches", "الإدارة · المواقع التشغيلية والفروع",
                    "/admin/sites", admin_only=True,
                    keywords=("sites", "branches", "locations", "مواقع", "فروع", "المواقع")),
        CommandSpec("nav.module_visibility", "navigation", "platform", "Module Visibility", "إظهار الوحدات للأدوار",
                    "Administration · Control visible modules & screens per role", "الإدارة · التحكم في إظهار الوحدات والشاشات لكل دور",
                    "/module-visibility", admin_only=True,
                    keywords=("visibility", "hide", "screens", "menu", "إظهار", "إخفاء", "القائمة")),
        CommandSpec("nav.audit", "navigation", "platform", "Audit Trail", "سجل التدقيق",
                    "Administration · Immutable security & activity log", "الإدارة · سجل الأمان والعمليات غير القابل للتعديل",
                    "/audit", ("platform.audit",), global_scope=True,
                    keywords=("audit", "log", "history", "تدقيق", "سجل العمليات")),
        CommandSpec("nav.document_settings", "navigation", "platform", "Document Settings & Numbering", "إعدادات المستندات والترقيم",
                    "Administration · Sequence prefixes & numbering rules", "الإدارة · بادئات التسلسل وقواعد ترقيم المستندات",
                    "/document-settings", admin_only=True,
                    keywords=("document settings", "numbering", "sequences", "prefix", "ترقيم", "إعدادات المستندات")),
        CommandSpec("nav.data_import", "navigation", "platform", "Data Import", "استيراد البيانات",
                    "Administration · Bulk CSV/JSON master & opening data import", "الإدارة · استيراد البيانات الأساسية والأرصدة الافتتاحية",
                    "/data-import", admin_only=True,
                    keywords=("data import", "import", "csv", "upload", "migration", "استيراد", "استيراد البيانات")),
        CommandSpec("nav.delegations", "navigation", "platform", "Approval Delegations", "تفويضات الاعتماد",
                    "Administration · Temporary authority & approval delegation", "الإدارة · تفويض الصلاحيات والاعتمادات المؤقتة",
                    "/delegations", ("platform.delegate",), global_scope=True,
                    keywords=("delegations", "delegate", "proxy", "تفويض", "تفويضات")),
        CommandSpec("nav.settings", "navigation", "platform", "Workspace Settings", "إعدادات مساحة العمل",
                    "Preferences · Language, theme and company profile", "التفضيلات · اللغة والمظهر وملف الشركة",
                    "/settings", keywords=("settings", "language", "theme", "dark", "arabic", "إعدادات", "اللغة", "المظهر")),
    ]
    for spec in specs:
        register_command(spec)


_seed_core_commands()


def _extension_commands(enabled: set[str]) -> list[CommandSpec]:
    dynamic: list[CommandSpec] = []
    for mod_id, manifest in sorted(MANIFESTS.items()):
        if mod_id not in enabled:
            continue
        for idx, nav in enumerate(manifest.get("navigation") or []):
            route = nav.get("route")
            label = nav.get("label") or {}
            if not route or not label.get("en"):
                continue
            mod_name = manifest.get("name") or {}
            perm = nav.get("required_permission")
            dynamic.append(
                CommandSpec(
                    id=f"ext.{mod_id}.{idx}",
                    category="navigation",
                    module=mod_id,
                    title_en=label["en"],
                    title_ar=label.get("ar") or label["en"],
                    subtitle_en=f"{mod_name.get('en', mod_id)} · Module workspace",
                    subtitle_ar=f"{mod_name.get('ar', mod_id)} · مساحة الوحدة",
                    route=route,
                    permissions=(perm,) if perm else (),
                    keywords=(mod_id, mod_name.get("en", ""), mod_name.get("ar", "")),
                )
            )
    return dynamic


def _permitted(user, spec: CommandSpec, enabled: set[str]) -> bool:
    if spec.admin_only and not user.is_superuser:
        return False
    if spec.module and spec.module != "platform" and spec.module not in enabled:
        return False
    if spec.predicate is not None and not spec.predicate(user):
        return False
    if not spec.permissions:
        return True
    scope = None if spec.global_scope else ...
    return any(has_permission(user, perm, scope) for perm in spec.permissions)


def _matches(spec: CommandSpec, term: str) -> tuple[bool, int]:
    if not term:
        return True, 50
    haystacks = (
        spec.title_en.casefold(),
        spec.title_ar.casefold(),
        spec.subtitle_en.casefold(),
        spec.subtitle_ar.casefold(),
        spec.route.casefold(),
        *(kw.casefold() for kw in spec.keywords if kw),
    )
    if any(h.startswith(term) for h in haystacks[:2]):
        return True, 0
    if any(term in h for h in haystacks[:2]):
        return True, 1
    if any(term in h for h in haystacks[2:]):
        return True, 2
    return False, 99


@never_cache
@endpoint("GET")
def commands(request):
    user = request.user
    preload_scopes(user)
    enabled = enabled_modules()
    query = string(request.GET, "q", 120, required=False).strip()
    term = query.casefold()

    seen_routes: set[tuple[str, str]] = set()
    candidates: list[tuple[int, int, dict]] = []

    all_specs = [*COMMAND_REGISTRY, *_extension_commands(enabled)]
    for order, spec in enumerate(all_specs):
        if not _permitted(user, spec, enabled):
            continue
        dedupe_key = (spec.category, spec.route)
        if dedupe_key in seen_routes:
            continue
        matched, rank = _matches(spec, term)
        if not matched:
            continue
        seen_routes.add(dedupe_key)
        candidates.append((
            rank,
            order,
            {
                "id": spec.id,
                "category": spec.category,
                "module": spec.module,
                "title": {"en": spec.title_en, "ar": spec.title_ar},
                "subtitle": {"en": spec.subtitle_en, "ar": spec.subtitle_ar},
                "route": spec.route,
            },
        ))

    candidates.sort(key=lambda item: (item[0], item[1]))
    items = [entry for _, _, entry in candidates[:40]]

    if len(query) >= 2:
        records = [*document_results(user, enabled, query), *master_results(user, enabled, query)]
        records.sort(key=lambda row: (row["number"].casefold() != term, row["kind"], row["number"]))
        for rec in records[:15]:
            items.append({
                "id": f"rec.{rec['kind']}.{rec['number']}.{rec['route']}",
                "category": "record",
                "module": rec["kind"],
                "title": {"en": f"{rec['number']} · {rec['title']}", "ar": f"{rec['number']} · {rec['title']}"},
                "subtitle": {"en": rec["kind"].replace("_", " ").upper(), "ar": rec["kind"].replace("_", " ").upper()},
                "route": rec["route"],
            })

    return {"items": items}
