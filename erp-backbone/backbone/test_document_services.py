from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections, transaction
from django.test import Client, TransactionTestCase

from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, AuditEvent, Authenticator, ModuleState, Role, Site
from backbone.services.inbox import close_task, open_task
from backbone.services.numbering import allocate_number
from purchasing.models import Review


class DocumentServicesTests(TransactionTestCase):
    def setUp(self):
        users = get_user_model().objects
        admin = users.create_superuser(username="admin")
        self.client.force_login(admin)
        session = self.client.session
        session[VERIFIED] = stamp(Authenticator.objects.create(user=admin, encrypted_key="test-only"))
        session.save()
        self.east = Site.objects.create(name="East")
        self.west = Site.objects.create(name="West")
        self.role = Role.objects.create(name="Reviewer", permissions=["purchasing.view", "purchasing.approve"])
        self.reader = self.user("reader", self.east)
        self.outsider = self.user("outsider", self.west)
        ModuleState.objects.create(module_id="purchasing", enabled=True)
        media = self.enterContext(TemporaryDirectory())
        self.enterContext(self.settings(MEDIA_ROOT=media))

    def user(self, name, site):
        user = get_user_model().objects.create_user(username=name)
        Assignment.objects.create(user=user, role=self.role, site=site)
        client = Client()
        client.force_login(user)
        return client

    def create(self, key="create", site=None, price="12.335"):
        return self.client.post("/api/purchase-requests", {
            "title": "<script>Seal</script>", "site_id": (site or self.east).pk, "currency": "AED",
            "lines": [{"description": "Seal", "quantity": "1", "unit_price": price}],
        }, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)

    def patch(self, path, data, client=None):
        return (client or self.client).patch(path, data, content_type="application/json")

    def test_numbering_scope_and_concurrent_allocation(self):
        url = "/api/numbering-rules/pr"
        config = {"prefix": "REQ", "per_site": True, "start": 100, "revision": 1}
        self.assertEqual(self.patch(url, config).status_code, 200)
        first = self.create().json()
        self.assertEqual(first["number"], f"REQ-S{self.east.pk}-000100")
        self.assertEqual(self.create().json(), first)
        self.assertEqual(self.create("west", self.west).json()["number"], f"REQ-S{self.west.pk}-000100")
        self.assertEqual(self.patch(url, {**config, "revision": 2}).json()["error"], "numbering_locked")
        self.assertEqual(self.patch(f"/api/purchase-requests/{first['id']}", {
            **first, "site_id": self.west.pk,
        }).json()["error"], "numbered_site_locked")

        def allocate(_):
            try:
                with transaction.atomic():
                    return allocate_number("pr", self.east.pk)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(set(pool.map(allocate, range(8))),
                             {f"REQ-S{self.east.pk}-{value:06d}" for value in range(101, 109)})
        with transaction.atomic():
            self.assertTrue(allocate_number("pr", self.east.pk).endswith("000109"))
            transaction.set_rollback(True)
        self.assertTrue(allocate_number("pr", self.east.pk).endswith("000109"))

    def test_attachment_archive_and_scope(self):
        row = self.create().json()
        detail = f"/api/purchase-requests/{row['id']}"
        base = f"/api/documents/pr/{row['id']}"
        url = base + "/attachments"

        def upload(data, client=None):
            return (client or self.client).post(url, {"file": SimpleUploadedFile("test.txt", data)},
                                                HTTP_IDEMPOTENCY_KEY="attachment-key")

        response = upload(b"test data")
        self.assertEqual(response.status_code, 201, response.content)
        attachment_id = response.json()["id"]
        self.assertEqual(upload(b"test data").status_code, 200)
        self.assertEqual(upload(b"different").status_code, 409)
        self.assertEqual(upload(b"").status_code, 400)
        self.assertEqual(upload(b"other", self.reader).status_code, 403)
        self.assertEqual(self.outsider.get(url).status_code, 404)
        file_url = f"{url}/{attachment_id}"
        other = self.create("other").json()
        self.assertEqual(self.client.get(f"/api/documents/pr/{other['id']}/attachments/{attachment_id}").status_code, 404)
        response = self.reader.get(file_url)
        self.assertEqual(b"".join(response.streaming_content), b"test data")
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))

        result = self.patch(base + "/archive", {"archived": True, "revision": row["revision"]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.patch(base + "/archive", {"archived": False, "revision": row["revision"]}).status_code, 409)
        self.assertNotIn(row["id"], [item["id"] for item in self.client.get("/api/purchase-requests").json()["items"]])
        self.assertEqual(self.reader.get("/api/purchase-requests?archived=1").json()["items"][0]["id"], row["id"])
        self.assertEqual(self.client.post(detail + "/submit", {"revision": result.json()["revision"]},
                                          content_type="application/json").json()["error"], "document_archived")
        self.assertEqual(self.patch(detail, row).json()["error"], "document_archived")
        self.assertEqual(self.reader.get(file_url).status_code, 200)
        self.assertEqual(self.patch(base + "/archive", {"archived": False, "revision": result.json()["revision"]}).status_code, 200)
        self.assertFalse(self.client.get(detail).json()["archived"])
        self.assertEqual(AuditEvent.objects.filter(action__in=["document.archive", "document.restore"]).count(), 2)
        ModuleState.objects.filter(pk="purchasing").update(enabled=False)
        self.assertEqual(self.reader.get(file_url).status_code, 404)

    def test_print_layout_and_inbox_permissions(self):
        url = "/api/print-templates/pr/ar"
        layout = {"revision": 0, "heading": "طلب تجريبي", "company_name": "الشركة", "footer": "نهاية", "logo": ""}
        self.assertEqual(self.patch(url, layout, self.reader).status_code, 403)
        self.assertEqual(self.patch(url, {**layout, "logo": "data:image/svg+xml;base64,AAAA"}).status_code, 400)
        self.assertEqual(self.patch(url, layout).status_code, 200)
        self.assertEqual(self.patch(url, layout).status_code, 409)
        row = self.create().json()
        printed = f"/print/pr/{row['id']}?language=ar"
        self.assertContains(self.reader.get(printed), 'dir="rtl"')
        self.assertContains(self.reader.get(printed), "طلب تجريبي")
        self.assertContains(self.reader.get(printed), "12.34")
        self.assertContains(self.reader.get(printed), "&lt;script&gt;Seal&lt;/script&gt;")
        self.assertEqual(self.outsider.get(printed).status_code, 404)
        self.assertEqual(self.client.get(f"/print/pr/{row['id']}?language=fr").status_code, 400)

        finance = Role.objects.create(name="Finance", permissions=["purchasing.view", "purchasing.approve"])
        publish = self.client.post("/api/purchase-templates", {
            "currency": "AED", "threshold": "100.00", "manager_role_id": self.role.pk, "finance_role_id": finance.pk,
        }, content_type="application/json")
        self.assertEqual(publish.status_code, 201, publish.content)
        submit = self.client.post(f"/api/purchase-requests/{row['id']}/submit", {"revision": row["revision"]},
                                  content_type="application/json")
        self.assertEqual(submit.status_code, 200, submit.content)
        run = Review.objects.get(requisition_id=row["id"])
        step = f"{run.pk}:0"
        task = open_task("pr", row["id"], step, self.role.pk)
        self.assertEqual(open_task("pr", row["id"], step, self.role.pk).pk, task.pk)
        self.assertEqual(self.reader.get("/api/inbox").json()["items"][0]["number"], row["number"])
        self.assertEqual(self.outsider.get("/api/inbox").json()["items"], [])
        self.assertEqual(self.client.get("/api/inbox").json()["items"], [])
        close_task("pr", row["id"], step)
        self.assertIsNotNone(open_task("pr", row["id"], step, self.role.pk).closed_at)
        self.assertEqual(self.reader.get("/api/inbox").json()["items"][0]["number"], row["number"])
