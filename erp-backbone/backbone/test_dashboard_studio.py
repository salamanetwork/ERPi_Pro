from django.contrib.auth import get_user_model
from django.test import TestCase


class DashboardStudioTests(TestCase):
    path = "/api/preferences/dashboards"

    def test_layout_is_personal_and_revision_checked(self):
        owner = get_user_model().objects.create_user("studio-owner")
        self.client.force_login(owner)
        data = {"revision":0,"layout":{"default":"custom-mine","boards":{"custom-mine":{"en":"Mine","ar":"لوحتي","widgets":[{"id":"inbox","size":"full"}]}}}}
        self.assertEqual(self.client.patch(self.path,data,content_type="application/json").status_code,200)
        self.assertEqual(self.client.patch(self.path,data,content_type="application/json").status_code,409)
        self.client.force_login(get_user_model().objects.create_user("other-owner"))
        self.assertEqual(self.client.get(self.path).json(),{"revision":0,"layout":{}})
        self.assertEqual(self.client.get("/api/workspace/dashboard/blocks").json(),{"widgets":{}})

    def test_blocks_use_report_dates_and_staff_site_scope(self):
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone
        from backbone.models import Assignment, Role, Site, ModuleState
        from sales.models import Customer, Document
        user = get_user_model().objects.create_user("sales-studio")
        east, west = (Site.objects.create(name=name) for name in ("East", "West"))
        Assignment.objects.create(user=user, site=east, role=Role.objects.create(name="sales",permissions=["sales.view"]))
        ModuleState.objects.create(module_id="sales",enabled=True)
        customer=Customer.objects.create(code="A",name="Alpha")
        today=timezone.localdate()
        for site,day,amount in ((east,today,20),(east,today-timedelta(days=90),30),(west,today,100)):
            Document.objects.create(kind="receipt",customer=customer,site=site,date=day,amount=Decimal(amount),actor="staff",status="recorded")
        self.client.force_login(user)
        result=self.client.get("/api/workspace/dashboard/blocks",{"from":today,"to":today}).json()["widgets"]
        self.assertEqual(len(result["customers-activity"]["items"]),1)
        self.assertEqual(Decimal(result["customers-activity"]["items"][0]["amount"]),20)
        self.assertNotIn("billing",result)
        self.assertEqual(self.client.get("/api/workspace/dashboard/blocks",{"site":west.id}).json()["widgets"],{})
