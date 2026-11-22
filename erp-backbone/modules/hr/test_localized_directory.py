from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase

from backbone.mfa import VERIFIED, stamp
from backbone.models import Assignment, Authenticator, ModuleState, Profile, Role, Site
from .models import Employee, HRReference

GRADES = "/api/hr/setup/grades"


def login_admin(client):
    user = get_user_model().objects.create_superuser(username="hr_admin")
    device = Authenticator.objects.create(user=user, encrypted_key="test-session-only")
    Profile.objects.create(user=user, language="ar")
    authorize_admin(client, user, device)
    return user, device


def authorize_admin(client, user, device):
    client.force_login(user)
    session = client.session
    session[VERIFIED] = stamp(device)
    session.save()


class LocalizedDirectoryTests(TestCase):
    def setUp(self):
        login_admin(self.client)
        ModuleState.objects.update_or_create(pk="hr", defaults={"enabled": True})
        self.site = Site.objects.create(name="الموقع الأول")
        self.other = Site.objects.create(name="موقع آخر")
        self.department = HRReference.objects.create(kind="departments", code="ENG", name="الهندسة")
        self.title = HRReference.objects.create(kind="job-titles", code="TECH", name="فني")

    def test_arabic_list_filters_and_create_with_site_scope(self):
        fields = {"department": "الهندسة", "title": "فني", "joined": date(2025, 1, 1),
                  "department_ref": self.department, "title_ref": self.title}
        Employee.objects.create(number="10011", name="أحمد ١", site=self.site, **fields)
        Employee.objects.create(number="10012", name="أحمد ٢", site=self.site, **fields)
        Employee.objects.create(number="10013", name="أحمد في موقع آخر", site=self.other, **fields)
        user = get_user_model().objects.create_user(username="scoped_hr")
        Profile.objects.create(user=user, language="ar")
        role = Role.objects.create(name="Scoped HR", permissions=["hr.view", "hr.manage"])
        Assignment.objects.create(user=user, site=self.site, role=role)
        client = Client()
        client.force_login(user)
        arabic = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
        eastern = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
        request = client.get("/api/hr/employees", {"page":"١","page_size":"۱","q":"أحمد","status":"active",
            "site":str(self.site.pk).translate(arabic),"department":str(self.department.pk).translate(eastern)},
            HTTP_ACCEPT_LANGUAGE="ar")
        self.assertEqual(request.status_code, 200, request.content)
        result = request.json()
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["results"][0]["number"], "10011")
        second = client.get(result["next"]).json()
        self.assertEqual(second["results"][0]["name"], "أحمد ٢")
        match = client.get("/api/hr/employees", {"page":1,"q":"١٠٠١٢"}).json()
        self.assertEqual([row["number"] for row in match["results"]], ["10012"])
        unknown_kind = HRReference.objects.create(kind="legacy", code="LEGACY", name="قديم")
        options = client.get("/api/hr/employee-options", HTTP_ACCEPT_LANGUAGE="ar").json()
        self.assertNotIn(unknown_kind.pk, [row["id"] for rows in options["references"].values() for row in rows])
        self.assertEqual(options["references"]["job-titles"][0]["name"], "فني")
        payload = {"name":"موظف تجريبي","site_id":self.site.pk, "department_ref_id":self.department.pk,
                   "title_ref_id":self.title.pk,"joined":"2026-01-01"}
        response = client.post("/api/hr/employees", payload, content_type="application/json",
                               HTTP_IDEMPOTENCY_KEY="employee-ar", HTTP_ACCEPT_LANGUAGE="ar")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(client.get(f'/api/hr/employees/{response.json()["id"]}').json()["name"], "موظف تجريبي")

    def test_grade_preview_manual_override_active_and_linked_history(self):
        preview = GRADES + "/code-preview"
        for _ in range(2):
            self.assertEqual(self.client.get(preview, {"name": "Manager"}).json(), {"code":"MGR001"})
        self.assertFalse(HRReference.objects.filter(kind="grades").exists())
        automatic = {"name":"Manager","auto_code":True}
        result = self.client.post(GRADES, automatic, content_type="application/json", HTTP_IDEMPOTENCY_KEY="grade-auto")
        self.assertEqual(result.status_code, 201, result.content)
        grade = result.json()
        replay = self.client.post(GRADES, automatic, content_type="application/json", HTTP_IDEMPOTENCY_KEY="grade-auto")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), grade)
        self.assertEqual(HRReference.objects.get(pk=grade["id"]).kind, "grades")
        self.assertEqual(grade["code"], "MGR001")
        self.assertTrue(grade["active"])
        manual = {"name":"درجة يدوية", "code":"MGR900", "active":False}
        result = self.client.post(GRADES, manual, content_type="application/json")
        self.assertEqual(result.status_code, 201, result.content)
        self.assertFalse(result.json()["active"])
        self.assertEqual(self.client.get(preview, {"name":"مدير"}).json(), {"code":"GRD001"})
        self.assertEqual(self.client.post(GRADES, manual, content_type="application/json").json()["error"],
                         "hr_reference_code_exists")
        employee = Employee.objects.create(number="E1", name="Employee", site=self.site, department="الهندسة",
            title="فني", joined=date(2025,1,1), grade_ref_id=grade["id"])
        result = self.client.patch(f'{GRADES}/{grade["id"]}', {**grade, "active":False}, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(self.client.get("/api/hr/employee-options").json()["references"]["grades"])
        self.assertEqual(self.client.get(f"/api/hr/employees/{employee.pk}").json()["grade"], "Manager")

    def test_job_titles_share_grade_automation_and_activity(self):
        job_titles = "/api/hr/setup/job-titles"
        preview = job_titles + "/code-preview"
        self.assertEqual(self.client.get(preview, {"name":"Manager"}).json(), {"code":"MGR001"})
        self.assertEqual(self.client.get(preview, {"name":"مدير"}).json(), {"code":"JOB001"})
        grade = self.client.post(GRADES, {"name":"Manager","auto_code":True}, content_type="application/json")
        title = self.client.post(job_titles, {"name":"Manager","auto_code":True,"active":False}, content_type="application/json")
        self.assertEqual(title.status_code, 201, title.content)
        self.assertEqual(title.json()["code"], grade.json()["code"])
        self.assertFalse(title.json()["active"])
        self.assertEqual(self.client.get(preview, {"name":"Manager"}).json(), {"code":"MGR002"})
        self.assertNotIn(title.json()["id"], [item["id"] for item in
            self.client.get("/api/hr/employee-options").json()["references"]["job-titles"]])
        override = self.client.post(job_titles, {"name":"مدير موقع","code":"SITE-MGR","active":True},
                                    content_type="application/json")
        self.assertEqual(override.status_code, 201, override.content)
        self.assertEqual(override.json()["code"], "SITE-MGR")


class GradeConcurrencyTests(TransactionTestCase):
    def test_parallel_creates_after_the_same_preview(self):
        ModuleState.objects.update_or_create(pk="hr", defaults={"enabled": True})
        user, device = login_admin(self.client)
        clients = [Client(), Client()]
        for client in clients:
            authorize_admin(client, user, device)
        barrier = Barrier(2)

        def create(client):
            try:
                preview = client.get(GRADES + "/code-preview", {"name":"Manager"}).json()["code"]
                barrier.wait(timeout=10)
                response = client.post(GRADES, {"name":"Manager","auto_code":True}, content_type="application/json")
                return preview, response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(create, clients))
        self.assertEqual([(preview, status) for preview,status,_ in results], [("MGR001",201)] * 2, results)
        self.assertEqual({data["code"] for _,_,data in results}, {"MGR001","MGR002"})
