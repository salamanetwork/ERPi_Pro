from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from PIL import Image

from .mfa import VERIFIED, stamp
from .models import Authenticator, CompanyProfile

PATH = "/api/company-profile"


class BrandingTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.admin = users.create_superuser("branding_admin")
        self.user = users.create_user("branding_reader")
        device = Authenticator.objects.create(user=self.admin, encrypted_key="test-session-only")
        self.client.force_login(self.admin)
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()
        self.reader = Client()
        self.reader.force_login(self.user)

    def save(self, revision=0, **fields):
        return self.client.post(PATH, {"revision": revision, "name": "شركة اختبار", **fields})

    def image(self, format, filename, content_type):
        buffer = BytesIO()
        Image.new("RGB", (600, 300), "teal").save(buffer, format)
        return SimpleUploadedFile(filename, buffer.getvalue(), content_type=content_type)

    def test_branding_is_shared_and_only_admin_can_change_it(self):
        self.assertEqual(self.reader.get(PATH).status_code, 403)
        self.assertEqual(self.reader.post(PATH, {"name": "No access", "revision": 0}).status_code, 403)
        saved = self.save(logo=self.image("PNG", "logo.png", "image/png"))
        self.assertEqual(saved.status_code, 200, saved.content)
        record = saved.json()
        self.assertEqual(self.reader.get("/api/workspace").json()["company"], record)
        self.assertEqual(record["name"], "شركة اختبار")
        image = self.reader.get(record["logo_url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image["Content-Type"], "image/png")
        self.assertEqual(Image.open(BytesIO(image.content)).size, (512, 256))
        self.assertEqual(Client().get(record["logo_url"]).status_code, 401)
        self.assertEqual(self.save(0, name="Stale").status_code, 409)
        replaced = self.save(1, logo=self.image("JPEG", "logo.jpg", "image/jpeg")).json()
        self.assertEqual(self.reader.get(replaced["logo_url"])["Content-Type"], "image/jpeg")
        self.assertEqual(self.reader.get(record["logo_url"]).status_code, 404)
        cleared = self.save(2, name="", remove_logo="1").json()
        self.assertEqual(cleared, {
            "revision": 3,
            "name": "",
            "tax_registration_number": "",
            "commercial_registration": "",
            "default_currency": "AED",
            "address": "",
            "logo_url": None,
            "deletion_policy": "soft",
        })

    def test_svg_logo_is_parsed_and_untrusted_content_is_rejected(self):
        good = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 16">
          <defs><linearGradient id="a"><stop stop-color="#006e68"/></linearGradient></defs>
          <path d="M0 0h32v16H0z" style="fill:url(#a);stroke:none"/></svg>'''
        saved = self.save(logo=SimpleUploadedFile("logo.svg", good, content_type="image/svg+xml"))
        self.assertEqual(saved.status_code, 200, saved.content)
        response = self.reader.get(saved.json()["logo_url"])
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertIn(b'fill="url(#a)"', response.content)
        for body in (
            b'<svg onload="alert(1)"/>',
            b'<svg><script>alert(1)</script></svg>',
            b'<svg><foreignObject/></svg>',
            b'<svg><image href="https://example.invalid/logo"/></svg>',
            b'<svg><path fill="url(https://example.invalid/fill)"/></svg>',
            b'<!DOCTYPE svg [<!ENTITY injected SYSTEM "file:///etc/passwd">]><svg>&injected;</svg>',
            b'not image content',
            b'\x89PNG\r\n\x1a\ntruncated',
        ):
            with self.subTest(body=body):
                invalid = self.save(1, name="Must not change",
                                    logo=SimpleUploadedFile("tries.png", body, content_type="image/png"))
                self.assertEqual(invalid.status_code, 400, invalid.content)
                self.assertEqual(invalid.json()["error"], "invalid_company_logo")
        too_big = SimpleUploadedFile("big.png", b"x" * (2 * 1024 * 1024 + 1), content_type="image/png")
        self.assertEqual(self.save(1, logo=too_big).status_code, 413)
        self.assertEqual(CompanyProfile.objects.get(pk=1).name, "شركة اختبار")
        self.assertEqual(CompanyProfile.objects.get(pk=1).revision, 1)


    def test_company_identity_and_currency_fields(self):
        bad = self.save(0, default_currency="12")
        self.assertEqual(bad.status_code, 400)
        saved = self.save(
            0,
            tax_registration_number="100123456700003",
            commercial_registration="CN-987654",
            default_currency="usd",
            address="Dubai, UAE",
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        payload = saved.json()
        self.assertEqual(payload["tax_registration_number"], "100123456700003")
        self.assertEqual(payload["commercial_registration"], "CN-987654")
        self.assertEqual(payload["default_currency"], "USD")
        self.assertEqual(payload["address"], "Dubai, UAE")

    def test_company_identity_renders_in_print_outputs(self):
        from django.template.loader import render_to_string
        from .company_profile import branding

        saved = self.save(
            0,
            name="ERPi Technologies LLC",
            tax_registration_number="100456789000003",
            commercial_registration="CN-445566",
            default_currency="AED",
            address="Sheikh Zayed Road, Dubai, UAE",
        )
        self.assertEqual(saved.status_code, 200, saved.content)

        # 1. Generic print preview endpoint (en & ar)
        for lang, expected_labels in (
            ("en", ("ERPi Technologies LLC", "TRN: <bdi>100456789000003</bdi>", "CR: <bdi>CN-445566</bdi>", "Sheikh Zayed Road, Dubai, UAE")),
            ("ar", ("ERPi Technologies LLC", "الرقم الضريبي: <bdi>100456789000003</bdi>", "السجل التجاري: <bdi>CN-445566</bdi>", "Sheikh Zayed Road, Dubai, UAE")),
        ):
            preview = self.client.post(
                f"/api/print-templates/pr/{lang}/preview",
                {"heading": "", "company_name": "", "footer": "", "logo": ""},
                content_type="application/json",
            )
            self.assertEqual(preview.status_code, 200, preview.content)
            html = preview.content.decode("utf-8")
            for snippet in expected_labels:
                self.assertIn(snippet, html)

        # 2. Sales, Billing Core, and HR Payslip templates with shared branding()
        company = branding()
        templates = [
            ("sales/print.html", {"row": {"number": "INV-1", "status": "issued", "amount": "100.00", "kind": "invoice", "lines": []}, "customer": {"name": "Cust"}, "title": "Sales invoice", "company": company}),
            ("billing_core/document.html", {"doc": {"number": "BC-1", "currency": "AED", "subtotal": "100.00", "tax": "5.00", "total": "105.00", "site": {"name": "HQ"}, "party": {"name_en": "Client", "name_ar": "عميل"}}, "receipt": None, "ar": False, "company": company}),
            ("hr/payslip.html", {"slip": {"number": "PAY-1", "name": "Staff", "month": "2026-11", "department": "IT", "center": "HQ", "state": "posted", "basic": "5000", "allowance": "1000", "extra": "0", "gross": "6000", "deduction": "0", "net": "6000", "inputs": []}, "arabic": True, "company": company}),
        ]
        for tpl_name, ctx in templates:
            rendered = render_to_string(tpl_name, ctx)
            self.assertIn("ERPi Technologies LLC", rendered)
            self.assertIn("100456789000003", rendered)
            self.assertIn("CN-445566", rendered)
            self.assertIn("Sheikh Zayed Road, Dubai, UAE", rendered)


class DeletionPolicyAndInventorySetupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin_del")
        device = Authenticator.objects.create(user=self.user, encrypted_key="test-session-only")
        self.client.force_login(self.user)
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()

    def test_company_profile_deletion_policy_persistence(self):
        res = self.client.get("/api/company-profile")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("deletion_policy"), "soft")

        upd = self.client.post(
            "/api/company-profile",
            {"name": "ERPi Pro", "deletion_policy": "permanent", "revision": str(data["revision"])},
        )
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.json().get("deletion_policy"), "permanent")

    def test_inventory_setup_soft_and_permanent_delete(self):
        from backbone.models import CompanyProfile, ModuleState, Site
        ModuleState.objects.update_or_create(module_id="inventory", defaults={"enabled": True})
        from inventory.models import Location, Unit, Warehouse

        site = Site.objects.create(name="Test Site")
        wh1 = Warehouse.objects.create(site=site, code="WH-SOFT", name="Soft WH")
        wh2 = Warehouse.objects.create(site=site, code="WH-PERM", name="Perm WH")
        loc = Location.objects.create(warehouse=wh2, code="LOC-1", name="Loc 1")
        unit = Unit.objects.create(code="U-DEL", name="Box")

        # Soft delete mode (default)
        CompanyProfile.objects.update_or_create(pk=1, defaults={"name": "ERPi", "deletion_policy": "soft"})
        r1 = self.client.delete(f"/api/inventory/setup/warehouse/{wh1.id}")
        self.assertEqual(r1.status_code, 200)
        wh1.refresh_from_db()
        self.assertTrue(wh1.is_deleted)
        self.assertFalse(wh1.active)
        self.assertIsNotNone(wh1.deleted_at)

        # Protected delete when warehouse has active location
        CompanyProfile.objects.filter(pk=1).update(deletion_policy="permanent")
        r_blocked = self.client.delete(f"/api/inventory/setup/warehouse/{wh2.id}")
        self.assertEqual(r_blocked.status_code, 409)

        # Permanent delete location, then warehouse, then unit
        self.assertEqual(self.client.delete(f"/api/inventory/setup/location/{loc.id}").status_code, 200)
        self.assertFalse(Location.objects.filter(pk=loc.id).exists())

        self.assertEqual(self.client.delete(f"/api/inventory/setup/warehouse/{wh2.id}").status_code, 200)
        self.assertFalse(Warehouse.objects.filter(pk=wh2.id).exists())

        self.assertEqual(self.client.delete(f"/api/inventory/setup/unit/{unit.id}").status_code, 200)
        self.assertFalse(Unit.objects.filter(pk=unit.id).exists())

