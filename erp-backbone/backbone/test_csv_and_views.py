import csv
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from assets.models import Asset
from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, AuditEvent, Authenticator, ModuleState, Role, Site
from inventory.models import Item, Location, Unit, Warehouse
from workorders.models import WorkOrder


class CsvAndSavedViewsTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        admin = users.create_superuser(username="admin")
        device = Authenticator.objects.create(user=admin, encrypted_key="test-session-only")
        self.client.force_login(admin)
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()
        self.reader = users.create_user(username="reader")
        self.reader_client = Client()
        self.reader_client.force_login(self.reader)
        self.east = Site.objects.create(name="East")
        self.west = Site.objects.create(name="West")
        for module in ("inventory", "assets", "workorders", "sales", "purchasing", "suppliers", "customers"):
            ModuleState.objects.update_or_create(module_id=module, defaults={"enabled": True})

    def upload(self, kind, operation, content, key="test-import", client=None):
        return (client or self.client).post(f"/api/import/{kind}/{operation}",
            {"file": SimpleUploadedFile("data.csv", content.encode("utf-8"))}, headers={"Idempotency-Key": key})

    def test_create_only_import_preview_atomicity_and_replay(self):
        Unit.objects.create(code="PCS", name="Piece")
        invalid = "code,name,base_unit_code\nA,صمام,PCS\nB,مضخة,UNKNOWN\n"
        preview = self.upload("item", "preview", invalid)
        self.assertEqual(preview.json(), {"row_count": 2, "errors": [
            {"row": 3, "column": "base_unit_code", "reason": "unknown_reference", "detail": "UNKNOWN"}]})
        self.assertEqual(self.upload("item", "commit", invalid).status_code, 400)
        self.assertFalse(Item.objects.exists())
        fixed = invalid.replace("UNKNOWN", "PCS")
        self.assertEqual(self.upload("item", "commit", fixed).status_code, 201)
        self.assertEqual(self.upload("item", "commit", fixed).json()["imported"], 2)
        self.assertEqual(Item.objects.get(code="B").name, "مضخة")
        self.assertEqual(AuditEvent.objects.filter(action="csv.import").count(), 1)
        self.assertEqual(self.upload("item", "commit", fixed + "\n").status_code, 409)
        errors = self.upload("item", "preview", fixed, key="new").json()["errors"]
        self.assertEqual([(e["row"], e["column"], e["reason"]) for e in errors],
                         [(2, "code", "code_exists"), (3, "code", "code_exists")])
        self.assertEqual(self.upload("item", "preview", fixed, client=self.reader_client).status_code, 403)

    def test_master_kinds_and_preview_row_numbers(self):
        for kind, content in {
            "unit": "code,name\nZZ,Test unit",
            "warehouse": "code,name,site_name\nWH,Main,East\nWH,Main,West",
            "location": "code,name,site_name,warehouse_code\nA,Shelf,West,WH",
            "asset": "tag,name,site_name\n001,Machine,West\n002,Mobile,",
            "customer": "code,name,terms,active\nC,Customer,60,false",
            "supplier": "code,name,currency_default\nS,Supplier,AED",
        }.items():
            self.assertEqual(self.client.get(f"/api/import/{kind}/template").status_code, 200)
            response = self.upload(kind, "commit", content)
            self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Warehouse.objects.filter(code="WH").count(), 2)
        self.assertEqual(Location.objects.get(code="A").warehouse.site, self.west)
        self.assertIsNone(Asset.objects.get(tag="002").site)
        duplicate = self.upload("asset", "preview", 'tag,name,site_name\n"006","line\ncontinued",West\n006,Again,East')
        self.assertEqual(duplicate.json()["errors"], [
            {"row": 4, "column": "tag", "reason": "duplicate_in_file", "detail": "006"}])
        broken = self.upload("customer", "preview", "code,name,terms,active\nD,,366,yes")
        self.assertEqual({e["column"] for e in broken.json()["errors"]}, {"name", "terms", "active"})
        with patch("backbone.services.csv_import.LIMIT", 1):
            response = self.upload("unit", "preview", "code,name\nX,First\nY,Second")
            self.assertEqual(response.json()["errors"][0]["reason"], "row_limit")

    def test_export_scope_formula_guard_and_limit(self):
        role = Role.objects.create(name="Readers", permissions=["workorders.view"])
        assignment = Assignment.objects.create(user=self.reader, role=role)
        export_role = Role.objects.create(name="East exporter", permissions=["platform.export"])
        export_assignment = Assignment.objects.create(user=self.reader, role=export_role, site=self.east)
        for title in ("=1+1", "+1", "-1", "@SUM(1)", " \t=1", 'عربي, "قيمة"'):
            WorkOrder.objects.create(title=title, site=self.east)
        WorkOrder.objects.create(title="Secret", site=self.west)
        path = "/api/export/work-orders"
        response = self.reader_client.get(path)
        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual({row["title"] for row in rows}, {"'=1+1", "'+1", "'-1", "'@SUM(1)", "' \t=1", 'عربي, "قيمة"'})
        self.assertEqual({row["site"] for row in rows}, {"East"})
        with patch("backbone.services.csv_export.LIMIT", 2):
            self.assertEqual(self.reader_client.get(path).json()["error"], "csv_export_limit")
        assignment.delete()
        self.assertEqual(self.reader_client.get(path).status_code, 403)
        export_assignment.delete()
        self.assertEqual(self.reader_client.get(path).status_code, 403)

    def test_views_are_personal_and_one_default_per_screen(self):
        url = "/api/saved-views/inbox"
        payload = {"name": "شرق", "filters": {"site": str(self.east.pk), "kind": "wo"}, "is_default": True}
        first = self.reader_client.post(url, payload, content_type="application/json").json()
        second = self.reader_client.post(url, {**payload, "name": "West"}, content_type="application/json").json()
        self.assertEqual([row["id"] for row in self.reader_client.get(url).json()["items"] if row["is_default"]], [second["id"]])
        self.assertEqual(self.client.get(url).json()["items"], [])
        self.assertEqual(self.client.delete(f"{url}/{first['id']}").status_code, 404)
        self.assertEqual(self.reader_client.patch(f"{url}/{first['id']}", {**payload, "name": "New"},
                                                 content_type="application/json").json()["name"], "New")
        self.assertEqual(self.reader_client.post(url, {**payload, "filters": {"site": "bad"}},
                                                content_type="application/json").status_code, 400)
        self.assertEqual(self.reader_client.delete(f"{url}/{first['id']}").status_code, 200)
