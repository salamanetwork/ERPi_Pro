import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command,CommandError
from django.test import TestCase,Client
from backbone import mfa
from backbone.registry import MANIFESTS,PERMISSION_METADATA,enabled_modules
from backbone.models import Authenticator


class ModuleContractTests(TestCase):
    def test_declarations_and_shared_metadata(self):
        for key,manifest in MANIFESTS.items():
            self.assertEqual(manifest["contract_version"],"1.0" if key in {"expenses","bank_reconciliation","subscriptions"} else "0.1",key)
            self.assertEqual(manifest["dependencies"],manifest["required_dependencies"])
            self.assertTrue(set(manifest["dependencies"])<=MANIFESTS.keys())
            for code,item in manifest["permission_definitions"].items():
                self.assertEqual(item["description_ar"],PERMISSION_METADATA[code]["description"]["ar"])
        self.assertIn("platform",enabled_modules())

    def test_core_cannot_disable(self):
        user=get_user_model().objects.create_superuser("contract-admin")
        self.client.force_login(user)
        device=Authenticator.objects.create(user=user,encrypted_key="test-session-stamp")
        session=self.client.session
        session[mfa.VERIFIED]=mfa.stamp(device)
        session.save()
        self.assertEqual(self.client.get("/admin/modules").status_code,200)
        self.assertEqual(self.client.patch("/api/modules/platform",{"enabled":False},content_type="application/json").status_code,409)
        self.assertIn("platform",enabled_modules())
        self.assertEqual(self.client.patch("/api/modules/supply",{"enabled":True},content_type="application/json").status_code,409)

    def test_command_rejects_existing_name(self):
        with self.assertRaises(CommandError):
            call_command("create_erpi_module","sales",name_en="Example",name_ar="مثال",stdout=StringIO())
