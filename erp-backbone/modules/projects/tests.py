from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounting.models import Account
from accounting.services import ledger_lock, period_for, posted_entry
from backbone.models import Assignment, AuditEvent, ModuleState, Role
from hr.models import AttendanceSession, Employee, WeeklyTimesheet, WorkOrderTimer
from supply.tests import SupplyFixture
from workorders.models import WorkOrder

from .models import TimeAllocation


class ProjectTests(SupplyFixture, TestCase):
    def setUp(self):
        super().setUp()
        for module in ("hr","projects","workorders","assets"):
            ModuleState.objects.update_or_create(module_id=module,defaults={"enabled":True})
        self.project=self.run_api("projects",{"site_id":self.source.pk,"code":"CONST-A","name":"Construction A",
                                              "start":self.day,"end":self.day},"project")
        self.path=f"projects/{self.project['id']}"
        self.task=self.run_api(self.path+"/tasks",{"title":"Install","due":self.day,"planned_hours":"4"},"task")
        self.revise("state",{"action":"activate"})

    def run_api(self,path,data,key="new",status=201,client=None):
        response=self.post(path,data,key,client)
        self.assertEqual(response.status_code,status,response.content)
        return response.json()

    def revise(self,action,data,key=None,status=201):
        response=self.run_api(self.path+"/"+action,{"revision":self.project["revision"],**data},
                              key or f"{action}:{self.project['revision']}",status)
        if status<300:
            self.project=response
        return response

    def employee_client(self, name, permissions):
        user=get_user_model().objects.create_user(username=name)
        role=Role.objects.create(name=name,permissions=permissions)
        Assignment.objects.create(user=user,site=self.source,role=role)
        client=Client()
        client.force_login(user)
        return user,client

    def include(self,user,role):
        self.revise("member",{"user_id":user.pk,"role":role,"active":True})

    def test_membership_is_mandatory_even_for_admin_and_direct_urls(self):
        outsider,client=self.employee_client("site-accountant",["projects.view","projects.manage","projects.cost"])
        budget=f"/api/{self.path}/budget"
        self.assertEqual(client.get(budget).status_code,404)
        self.assertEqual(client.get("/api/"+self.path).status_code,404)
        self.assertEqual(client.get("/api/projects").json()["count"],0)
        other_admin=get_user_model().objects.create_superuser(username="another-admin")
        admin_client=Client()
        self.login_admin(admin_client,other_admin)
        self.assertEqual(admin_client.get(budget).status_code,404)
        self.include(outsider,"member")
        self.assertEqual(client.get("/api/"+self.path).status_code,200)
        self.assertEqual(client.get(budget).status_code,403)
        self.include(outsider,"finance")
        self.assertEqual(client.get(budget).status_code,200)
        self.assertEqual(AuditEvent.objects.filter(action="projects.membership",target__contains=f"user #{outsider.pk}").count(),2)

    def test_timesheet_options_explain_empty_sections_without_exposing_projects(self):
        worker, client = self.employee_client("options-worker", ["attendance.record", "projects.view"])
        _, reviewer = self.employee_client("options-reviewer", ["attendance.approve", "projects.view", "projects.time"])
        employee=Employee.objects.create(number="OPT-1",name="Options employee",site=self.source,user=worker,joined=date(2024,1,1))
        start=datetime(2025,1,6,tzinfo=UTC)
        sheet=WeeklyTimesheet.objects.create(site=self.source,employee=employee,week_start=start.date(),
                  starts_at=start,ends_at=start+timedelta(days=7),timezone="UTC",standard_minutes=480)
        path=f"/api/projects/timesheets/{sheet.pk}"
        self.assertEqual(reviewer.get(path).json(), {"status":"not_owner","tasks":[],"timers":[]})
        self.assertEqual(client.get(path).json(), {"status":"permission","tasks":[],"timers":[]})
        role = Role.objects.get(name="options-worker")
        role.permissions.append("projects.time")
        role.save(update_fields=["permissions"])
        self.assertEqual(client.get(path).json(), {"status":"no_projects","tasks":[],"timers":[]})
        self.include(worker,"member")
        self.assertEqual(client.get(path).json()["status"], "ready")
        self.assertEqual([row["id"] for row in client.get(path).json()["tasks"]], [self.task["id"]])
        from .models import Task
        Task.objects.filter(pk=self.task["id"]).update(milestone=True)
        self.assertEqual(client.get(path).json(), {"status":"no_tasks","tasks":[],"timers":[]})
        sheet.state="approved"
        sheet.save(update_fields=["state"])
        self.assertEqual(client.get(path).json(), {"status":"locked","tasks":[],"timers":[]})

    def test_existing_timer_approval_and_reopen_keep_one_time_source(self):
        worker,client=self.employee_client("project-worker",["projects.view","projects.time","attendance.record"])
        reviewer,review=self.employee_client("project-reviewer",["projects.view","projects.review","attendance.approve","attendance.reopen"])
        _,outsider=self.employee_client("nonmember-reviewer",["projects.view","projects.review","attendance.approve"])
        self.include(worker,"member")
        self.include(reviewer,"finance")
        self.revise("rate",{"role":"member","rate":"100"})
        self.revise("baseline",{"hours":"4","reason":"Initial plan","categories":{"materials":"100","expense":"0","labour":"400"}})
        employee=Employee.objects.create(number="P-1",name="Technician",site=self.source,user=worker,joined=date(2024,1,1))
        start=datetime(2025,1,6,tzinfo=UTC)
        sheet=WeeklyTimesheet.objects.create(site=self.source,employee=employee,week_start=start.date(),
                  starts_at=start,ends_at=start+timedelta(days=7),timezone="UTC",standard_minutes=480)
        shift=AttendanceSession.objects.create(employee=employee,site=self.source,timezone="UTC",
                                               started_at=start+timedelta(hours=8),ended_at=start+timedelta(hours=16))
        order=WorkOrder.objects.create(title="Run",site=self.source)
        timer=WorkOrderTimer.objects.create(employee=employee,session=shift,order=order,
                                          started_at=start+timedelta(hours=9),ended_at=start+timedelta(hours=11))
        change=f"projects/timesheets/{sheet.pk}"
        options = client.get("/api/"+change).json()
        self.assertEqual(options["timers"], [{"id":timer.pk,"order_id":order.pk,"seconds":7200}])
        payload={"revision":1,"task_id":self.task["id"],"timer_id":timer.pk}
        response=self.run_api(change,payload,"allocation",client=client)
        self.assertEqual(self.run_api(change,payload,"allocation",status=200,client=client),response)
        allocated=client.get(f"/api/attendance/timesheets/{sheet.pk}").json()
        self.assertEqual(allocated["details"]["totals"]["wo_seconds"],7200)
        self.assertEqual(allocated["details"]["totals"]["net_seconds"],28800)
        self.assertEqual(allocated["project_time"][0]["timer_id"],timer.pk)
        self.assertEqual(self.run_api(change,{**payload,"revision":2},"duplicate",status=409,client=client)["error"],
                         "project_timer_assigned")
        path=f"attendance/timesheets/{sheet.pk}"
        self.run_api(path+"/submit",{"revision":2},status=200,client=client)
        self.assertFalse(any(i["id"]==f"hr:timesheet:{sheet.pk}" for i in outsider.get("/api/inbox").json()["items"]))
        self.assertTrue(any(i["id"]==f"hr:timesheet:{sheet.pk}" for i in review.get("/api/inbox").json()["items"]))
        self.run_api(path+"/approve",{"revision":3},status=404,client=outsider)
        self.run_api(path+"/approve",{"revision":3},status=200,client=review)
        self.run_api(path+"/approve",{"revision":3},status=409,client=review)
        result=self.client.get(f"/api/{self.path}/budget").json()
        self.assertEqual((result["actual_hours"],result["labour"],result["gl_actual"]),("2.00","200.00","0.00"))
        self.run_api(path+"/reopen",{"revision":4,"reason":"Review again"},status=200,client=review)
        self.assertEqual(self.client.get(f"/api/{self.path}/budget").json()["labour"],"0.00")
        self.run_api(path+"/submit",{"revision":5},status=200,client=client)
        self.run_api(path+"/approve",{"revision":6},status=200,client=review)
        self.assertEqual(self.client.get(f"/api/{self.path}/budget").json()["labour"],"200.00")
        self.assertEqual(TimeAllocation.objects.filter(timer=timer).count(),1)
        self.assertEqual(WorkOrderTimer.objects.filter(pk=timer.pk).count(),1)
        self.assertEqual(AuditEvent.objects.filter(action="projects.time.approve").count(),2)

    def test_plan_versions_posted_expense_variance_and_close_audit(self):
        self.activate()
        self.revise("baseline",{"hours":"10","reason":"Original","categories":{"materials":"500","expense":"300","labour":"400"}})
        self.revise("baseline",{"hours":"11","reason":"Updated","categories":{"materials":"500","expense":"350","labour":"400"}})
        self.revise("state",{"action":"close","reason":"Complete"},status=409)
        ledger=ledger_lock()
        expense=Account.objects.get(code="5200")
        bank=Account.objects.get(code="1100")
        entry=posted_entry(ledger,period_for(ledger,self.today),self.today,"admin","Expense","manual",
                           [(expense,Decimal(400),self.source.pk,None),(bank,Decimal(-400),self.source.pk,None)])
        line=entry.lines.get(account=expense)
        self.run_api(self.path+"/link",{"type":"expense","source_id":line.pk,"task_id":self.task["id"]})
        report=self.client.get(f"/api/{self.path}/budget").json()
        self.assertEqual((report["planned"],report["actual"],report["variance"]),("1250.00","400.00","850.00"))
        self.run_api(self.path+"/tasks",{"id":self.task["id"],"revision":self.task["revision"],"action":"move","state":"done"},"complete")
        self.revise("state",{"action":"close","reason":"Completed"})
        posted_entry(ledger,period_for(ledger,self.today),self.today,"admin","Expense correction","manual",
                           [(expense,Decimal(-400),self.source.pk,None),(bank,Decimal(400),self.source.pk,None)],reversal_of=entry)
        self.assertEqual(self.client.get(f"/api/{self.path}/budget").json()["gl_actual"],"0.00")
        self.assertTrue(AuditEvent.objects.filter(action="projects.state",target__contains="close / Completed").exists())
        self.assertEqual(AuditEvent.objects.filter(action="projects.baseline").count(),2)

    def test_work_order_parts_and_return_reconcile_with_posted_journals(self):
        from assets.models import Asset
        from workorders.models import MaintenanceJob
        from accounting.models import Line

        self.activate()
        self.seed("30")
        asset=Asset.objects.create(name="Pump",tag="P-1",site=self.source)
        order=WorkOrder.objects.create(title="Install pump",site=self.source)
        job=MaintenanceJob.objects.create(order=order,asset=asset,stage="waiting",center_required=False)
        self.run_api(self.path+"/link",{"type":"work","source_id":order.pk,"task_id":self.task["id"]},"link-work")
        order.status="approved"
        order.save(update_fields=["status"])
        job.stage="in_progress"
        job.save(update_fields=["stage"])
        path=f"maintenance/orders/{order.pk}/parts"
        issue=self.run_api(path,{"item_id":self.item.pk,"location_id":self.home.pk,"quantity":"25"},"part-issue")
        self.run_api(path,{"original_id":issue["id"],"quantity":"7.5"},"part-return")
        report=self.client.get(f"/api/{self.path}/budget").json()
        self.assertEqual((report["gl_actual"],report["labour"],report["actual"]),("175.00","0.00","175.00"))
        journals={row["entry_id"] for row in report["entries"]}
        self.assertEqual(len(journals),2)
        self.assertEqual(sum(Line.objects.filter(entry_id__in=journals,account__kind="expense").values_list("amount",flat=True)),175)

    def test_posted_supplier_invoice_releases_only_its_matched_po_commitment(self):
        from purchasing.models import PurchaseOrder,Supplier,Requisition

        self.activate()
        ModuleState.objects.update_or_create(module_id="supplier_invoices",defaults={"enabled":True})
        _,poster=self.employee_client("ap-project-poster",["ap.view","ap.post"])
        supplier=Supplier.objects.create(code="PROJECT-V",name="Vendor",currency_default="AED")
        pr=Requisition.objects.create(site=self.source,title="Material",currency="AED",total=1000,lines=[])
        po=PurchaseOrder.objects.create(site=self.source,source_pr=pr,supplier=supplier,number="PO-PROJECT",
             title="Stocks",currency="AED",total=1000,lines=[{"id":1,"description":"Stock","quantity":"10.000",
                                                           "unit_price":"100.0000","amount":"1000.00"}])
        self.run_api(self.path+"/link",{"type":"purchase","source_id":po.pk,"line_id":1,"task_id":self.task["id"]})
        self.run_api(f"purchase-orders/{po.pk}/issue",{"revision":1},"issue-po",status=200)
        self.revise("baseline",{"hours":"0","reason":"Stock","categories":{"materials":"1200","expense":"0","labour":"0"}})
        path=f"/api/{self.path}/budget"
        self.assertEqual(self.client.get(path).json()["committed"],"1000.00")
        receipt=self.run_api("inventory/receipts",{"order_id":po.pk,"warehouse_id":self.home.warehouse_id,
                    "lines":[{"order_line_id":1,"quantity":"4","item_id":self.item.pk,"unit_id":self.item.base_unit_id,
                              "location_id":self.home.pk}]},"receipt-po")
        done=self.run_api(f"inventory/receipts/{receipt['id']}/confirm",{"revision":receipt["revision"]},"confirm")
        invoice=self.run_api("ap/invoices",{"order_id":po.pk,"supplier_number":"INV-PRJ","invoice_date":self.day,"due_date":self.day,
             "declared_total":"400.00","lines":[{"movement_id":done["lines"][0]["movement_id"],"quantity":"4","unit_price":"100"}]},"inv")
        self.run_api(f"ap/invoices/{invoice['id']}/post",{"revision":invoice["revision"],"posting_date":self.day},"post",client=poster)
        balanced=self.client.get(path).json()
        self.assertEqual((balanced["committed"],balanced["gl_actual"],balanced["variance"]),("600.00","0.00","1200.00"))
        self.assertEqual(balanced["purchases"][0]["billed_stock"],"400.00")
        self.run_api(f"ap/invoices/{invoice['id']}/reverse",{"revision":invoice["revision"]+1,
                     "posting_date":self.day,"reason":"Supplier requested correction"},"reverse",client=poster)
        self.assertEqual(self.client.get(path).json()["committed"],"1000.00")
