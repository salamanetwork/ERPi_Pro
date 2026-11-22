import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from backbone.module_contract import register
from backbone.registry import MANIFESTS,MANIFEST_FILES
class Command(BaseCommand):
    def handle(self,**opts):
        keys=(settings.SECRET_KEY, settings.ERP_MFA_KEY, settings.ERP_CUSTOMER_JWT_KEY, settings.ERP_CONNECTOR_KEY)
        if len(set(keys))!=4 or any(len(k)<32 for k in keys):raise CommandError("Set four independent staff, MFA, customer JWT and connector keys (32+ characters).")
        owners={json.loads((settings.BASE_DIR/p).read_text())['id']:str(Path(p).parent) for p in MANIFEST_FILES}
        shared={
            'assets':'backbone/tests.py',
            'media_service':'modules/public_identity/tests.py',
            'billing_core':'modules/parties/tests.py',
            'scheduling':'modules/parties/tests.py',
        }
        metadata={}
        for key,m in MANIFESTS.items():
            register(m,metadata)
            d=settings.BASE_DIR/owners.get(key,f"modules/{key}")
            if not m.get('sop_guides'):raise CommandError(key+': missing SOP')
            if not(any(d.glob('test*.py')) or any(d.glob('tests/test*.py')) or key in shared and (settings.BASE_DIR/shared[key]).is_file()):raise CommandError(key+': missing tests')
        self.stdout.write(f'{len(MANIFESTS)} contracts passed.')
