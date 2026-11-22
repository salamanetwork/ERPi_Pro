from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from backbone.compatibility import compatibility_issue, effective_modules
from backbone.models import ModuleState
from backbone.registry import MANIFESTS


class Command(BaseCommand):
    help = "Check enabled modules against the target release before migration."

    def handle(self, *args, **options):
        if ModuleState._meta.db_table not in connection.introspection.table_names():
            self.stdout.write("Module compatibility preflight passed (new database).")
            return
        requested = set(ModuleState.objects.filter(enabled=True).values_list("module_id", flat=True))
        active = effective_modules(MANIFESTS, requested)
        blocked = sorted((requested - active) | {key for key in requested & active
            if set(MANIFESTS[key].get("required_dependencies", [])) - active})
        if blocked:
            reasons = []
            for key in blocked:
                manifest = MANIFESTS.get(key)
                reason = (compatibility_issue(manifest) or "dependency unavailable") if manifest else "manifest missing"
                reasons.append(f"{key}: {reason}")
            raise CommandError("Upgrade blocked by enabled modules: " + ", ".join(reasons))
        self.stdout.write("Module compatibility preflight passed.")
