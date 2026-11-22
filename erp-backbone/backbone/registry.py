import json
from django.conf import settings
from .models import ModuleState
from .compatibility import PLATFORM_VERSION, RELEASE_VERSION, compatibility_issue, compatible_modules, effective_modules

MANIFEST_FILES = ["backbone/core_manifest.json"] + [
    f"modules/{package}/manifest.json"
    for package in ("assets", "workorders", "purchasing", "inventory", "accounting", "sales", "hr", "crm", "supply")
] + [
    "modules/purchasing/suppliers_manifest.json",
    "modules/purchasing/purchase_orders_manifest.json",
    "modules/accounting/supplier_invoices_manifest.json",
    "modules/sales/customers_manifest.json",
    "modules/hr/payroll_manifest.json",
]
MANIFESTS = {}
for path in MANIFEST_FILES:
    manifest = json.loads((settings.BASE_DIR / path).read_text())
    MANIFESTS[manifest["id"]] = manifest

MANIFESTS.update(settings.ERP_EXTENSION_MANIFESTS)

MODULE_ACCESS = {
    "hr": (MANIFESTS["hr"]["permissions"], ...),
    "payroll": (MANIFESTS["payroll"]["permissions"], ...),
    "accounting": (["accounting.view"], None),
    "suppliers": (["suppliers.view", "suppliers.manage"], None),
    "purchase_orders": (["purchasing.view"], ...),
    "supplier_invoices": (["ap.view"], ...),
    "customers": (["sales.view"], ...),
}

MAINTENANCE_PERMISSIONS = {"maintenance." + action for action in ("view", "plan", "execute", "supervise", "parts")}
BUDGET_PERMISSIONS = {"budget.view", "budget.centers", "budget.prepare", "budget.approve"}
PERMISSIONS = BUDGET_PERMISSIONS | MAINTENANCE_PERMISSIONS | {"platform.audit", "platform.delegate", "platform.export"} | {
    permission for manifest in MANIFESTS.values() for permission in manifest["permissions"]
}


from .module_contract import register
PERMISSION_METADATA = {}
for manifest in MANIFESTS.values():
    register(manifest, PERMISSION_METADATA)



def enabled_modules():
    return effective_modules(MANIFESTS, ModuleState.objects.filter(enabled=True).values_list("module_id", flat=True))


def modules_for(user):
    from .access import has_permission

    enabled = enabled_modules()
    from .models import Assignment
    roles = []
    if not user.is_superuser:
        roles = getattr(user, "_erp_navigation_roles", None)
        if roles is None:
            roles = list(Assignment.objects.filter(user=user).values_list("role__hidden_modules", flat=True))
    modules = []
    compatible = compatible_modules(MANIFESTS)
    for module_id, manifest in MANIFESTS.items():
        permissions, scope = MODULE_ACCESS.get(module_id, ([f"{module_id}.view"], ...))
        if module_id == "hr" and "payroll" in enabled:
            permissions = [*permissions, "payroll.prepare"]
        allowed = module_id in enabled and any(has_permission(user, key, scope) for key in permissions)
        navigation_allowed = not roles or any(module_id not in hidden for hidden in roles)
        issue = compatibility_issue(manifest) or ("dependency_incompatible" if module_id not in compatible else "")
        modules.append({**manifest, "compatible": not issue, "compatibility_issue": issue,
                        "core_version": manifest.get("core_version_range"), "runtime_core_version": RELEASE_VERSION, "platform_contract": PLATFORM_VERSION,
                        "missing_dependencies": sorted(set(manifest.get("required_dependencies", [])) - enabled),
                        "enabled": module_id in enabled, "visible": allowed,
                        "show_in_navigation": allowed and navigation_allowed, "navigation_allowed": navigation_allowed})
    return modules
