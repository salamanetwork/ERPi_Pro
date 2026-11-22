from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from . import mfa
from .models import Assignment, AuditEvent, Authenticator, Profile, Role, Site


class AdminIamTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("iam-owner")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin)
        device = Authenticator.objects.create(user=self.admin, encrypted_key="test-session-stamp")
        session = self.client.session
        session[mfa.VERIFIED] = mfa.stamp(device)
        session.save()
        self.client.get("/admin/users")
        self.token = self.client.cookies["csrftoken"].value
        self.site = Site.objects.create(name="IAM east")
        self.role = Role.objects.create(name="IAM reader", permissions=["assets.view"])

    def send(self, method, url, payload):
        return getattr(self.client, method)(url, payload, content_type="application/json", HTTP_X_CSRFTOKEN=self.token)

    def create(self, **extra):
        return self.send("post", "/api/admin/users", {"username":"iam-worker","display_name":"وليد أحمد",
            "password":"Temporary#78314109!","role_id":self.role.pk,"site_id":self.site.pk, **extra})

    def test_create_search_edit_reset(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.content)
        url = "/api/admin/users/"+str(response.json()["id"])
        row = self.client.get(url).json()
        self.assertEqual(row["assignments"][0]["site_id"], self.site.pk)
        self.assertEqual(self.client.get("/api/admin/users",{"q":"وليد"}).json()["count"],1)
        updated = self.send("patch",url,{"display_name":"Walid","active":False,"revision":row["revision"]})
        self.assertEqual(updated.json()["revision"],2)
        self.assertEqual(self.send("patch",url,{"display_name":"old","revision":1}).status_code,409)
        self.assertEqual(self.client.get("/api/admin/users",{"status":"inactive"}).json()["count"],1)
        self.assertEqual(self.send("post",url+"/reset-password",{"password":"Changed#1029384756!", "revision":2}).status_code,200)
        self.assertTrue(get_user_model().objects.get(pk=row["id"]).check_password("Changed#1029384756!"))
        self.assertTrue(AuditEvent.objects.filter(action="user.password_reset",actor_user_id=self.admin.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(target__contains="Changed#").exists())

    def test_legacy_user_without_profile(self):
        user = get_user_model().objects.create_user("legacy-iam")
        profiles = Profile.objects.filter(user=user)
        self.assertFalse(profiles.exists())
        url = f"/api/admin/users/{user.pk}"
        listing = self.client.get("/api/admin/users").json()["results"]
        row = next(item for item in listing if item["id"] == user.pk)
        self.assertIs(type(row["revision"]), int)
        self.assertEqual(row["revision"], 1)
        self.assertEqual(row["display_name"], "legacy-iam")
        self.assertEqual(self.client.get(url).json()["revision"], 1)
        self.assertFalse(profiles.exists())
        self.assertEqual(self.send("patch", url, {"display_name": "Legacy", "revision": 7}).status_code, 409)
        self.assertFalse(profiles.exists())
        saved = self.send("patch", url, {"display_name": "Legacy", "revision": 1})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["revision"], 2)
        self.assertEqual(profiles.get().iam_revision, 2)
        self.assertEqual(self.send("patch", url, {"display_name": "Older", "revision": 1}).status_code, 409)

    def test_atomic_rollback_and_self_protection(self):
        self.assertEqual(self.create(site_id=999999).status_code,400)
        self.assertFalse(get_user_model().objects.filter(username="iam-worker").exists())
        self.assertFalse(AuditEvent.objects.filter(action="user.create").exists())
        self.assertEqual(self.send("patch",f"/api/admin/users/{self.admin.pk}",{"active":False,"revision":1}).status_code,409)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_roles_counts_update_and_legacy_grants(self):
        Assignment.objects.create(user=self.admin,role=self.role,site=self.site)
        Assignment.objects.create(user=self.admin,role=self.role)
        row=next(r for r in self.client.get("/api/admin/roles").json()["results"] if r["id"]==self.role.pk)
        self.assertEqual(row["user_count"],1)
        change={"revision":1,"name":"IAM team","permissions":["assets.view","assets.create"]}
        url=f"/api/admin/roles/{self.role.pk}"
        self.assertEqual(self.send("patch",url,change).status_code,200)
        self.assertEqual(self.send("patch",url,change).status_code,409)
        self.assertEqual(self.send("post","/api/admin/roles",{**change,"name":"Team copy"}).status_code,201)
        self.assertEqual(self.role.assignment_set.count(),2)
        self.assertTrue(AuditEvent.objects.filter(action="role.update",target=str(self.role.pk)).exists())

    def test_auth_csrf_and_deep_links(self):
        self.assertEqual(Client().get("/api/admin/users").status_code,401)
        self.assertEqual(self.client.get("/api/admin/users",HTTP_AUTHORIZATION="Bearer customer").status_code,403)
        self.assertEqual(self.client.post("/api/admin/users",{},content_type="application/json").status_code,403)
        for url in ["/admin/users","/admin/users/1","/access","/admin/access-control"]:
            self.assertEqual(self.client.get(url).status_code,200)
        employee=get_user_model().objects.create_user("ordinary")
        self.client.force_login(employee)
        self.assertEqual(self.client.get("/api/admin/users").status_code,403)
        self.assertEqual(self.client.get("/api/admin/roles").status_code,403)

    def test_catalog_describes_every_permission(self):
        payload = self.client.get("/api/access").json()
        self.assertEqual(set(payload["permission_catalog"]), set(payload["permission_metadata"]))
        for value in payload["permission_metadata"].values():
            for lang in ("ar", "en"):
                self.assertTrue(value["label"][lang] and value["description"][lang])

    def test_site_edits_preserve_legacy_clients(self):
        self.assertEqual(self.client.get("/admin/sites").status_code, 200)
        url = f"/api/sites/{self.site.pk}"
        self.assertEqual(self.client.get(url).json()["description"], "")
        update = {"name": self.site.name, "active": False, "revision": 1, "description": "فرع"}
        self.assertEqual(self.send("patch", url, update).json()["description"], "فرع")
        self.assertEqual(self.send("patch", url, update).status_code, 409)
        self.assertFalse(self.client.get(url).json()["active"])
        del update["description"]
        update.update(revision=2, active=True)
        self.assertEqual(self.send("patch", url, update).json()["description"], "فرع")
