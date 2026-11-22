from .extensions import spa_prefixes
from .models import Assignment, NavigationSettings

# IDs are stable across languages and kept in sync with the frontend menu config.
SCREEN_IDS = (
    "workspace", "reports", "purchase_requests", "purchase_orders", "suppliers", "supplier_invoices",
    "inventory_items", "goods_receipts", "stock_balances", "stock_movements", "inventory_setup", "opening_balances",
    "assets_list", "work_orders", "maintenance", "preventive_maintenance",
    "general_accounting", "cost_centers_budgets", "sales_documents", "customers", "ar_aging",
    "employees", "hr_setup", "offboarding", "leave_requests", "payroll_runs", "payslips", "time_attendance",
    "module_manager", "users", "access_control", "sites", "module_visibility", "audit_trail",
    "document_settings", "data_import", "delegations", "settings",
    "inbox", "notifications", "profile", "integrations", "security-health", "subscriptions", "bank_reconciliation", "crm", "supply",
)
SCREEN_ID_SET = frozenset((*SCREEN_IDS, *spa_prefixes()))


def visibility(user):
    global_hidden = NavigationSettings.objects.filter(pk=1).values_list("hidden_screens", flat=True).first() or []
    hidden = set(global_hidden)
    if not user.is_superuser:
        assignments = getattr(user, "_erp_navigation_roles", None)
        if assignments is None:
            assignments = list(Assignment.objects.filter(user=user).values_list("role__hidden_modules", flat=True))
        # A screen stays visible when any assigned role leaves it visible.
        if assignments:
            hidden.update(set.intersection(*map(set, assignments)))
    return sorted(hidden & SCREEN_ID_SET)

MENU_GROUPS = frozenset("general procurement inventory maintenance finance sales hr payroll admin".split())

def validate_layout(value):
    from .validation import Problem
    def invalid():
        raise Problem("invalid_menu", field="layout")
    def ids(values, allowed):
        if not isinstance(values,list) or len(values)>len(allowed) or any(not isinstance(x,str) or x not in allowed for x in values) or len(set(values))!=len(values):
            invalid()
        return values
    if not isinstance(value,dict) or set(value)-{"order","children","hidden_groups","hidden_screens"}:invalid()
    for field in ("order","hidden_groups"):
        ids(value.get(field,[]),MENU_GROUPS)
    ids(value.get("hidden_screens",[]),SCREEN_ID_SET)
    children=value.get("children",{})
    if not isinstance(children,dict) or set(children)-MENU_GROUPS:invalid()
    seen=set()
    for screens in children.values():
        ids(screens,SCREEN_ID_SET)
        if seen.intersection(screens):invalid()
        seen.update(screens)
    return value
