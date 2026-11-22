from datetime import date
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from accounting.models import Entry, Ledger, Period
from backbone.models import Assignment, AuditEvent, ModuleState, Role, Site
from hr.models import Employee
from .models import Settlement

class ExpenseTests(TestCase):
    def setUp(self):
        ModuleState.objects.create(module_id="expenses",enabled=True)
        self.day=date(2026,11,1)
        self.site=Site.objects.create(name="Expense site")
        self.owner=self.account("owner","view","create")
        self.finance=self.account("finance","view","approve","post","reimburse")
        self.employee=Employee.objects.create(number="E1",name="Staff",joined=self.day,site=self.site,user=self.owner)
        ledger=Ledger.objects.get(pk=1)
        ledger.cutoff=self.day
        ledger.activated_at=timezone.now()
        ledger.save()
        self.period=Period.objects.create(ledger=ledger,start=self.day,end=date(2026,11,30))
        self.root="/api/expenses/claims"
        self.client.force_login(self.owner)

    def account(self,name,*permissions):
        user=get_user_model().objects.create_user(name)
        Assignment.objects.create(user=user,site=self.site,role=Role.objects.create(name=name,permissions=["expenses."+p for p in permissions]))
        return user

    def post(self,url,data,key=None,status=201):
        response=self.client.post(url,data,content_type="application/json",HTTP_IDEMPOTENCY_KEY=key or str(uuid4()))
        self.assertEqual(response.status_code,status,response.content)
        return response.json()

    def draft(self,method="out_of_pocket"):
        row=self.post(self.root,dict(employee_id=self.employee.pk,site_id=self.site.pk,method=method,currency="AED",
                    lines=[dict(category="travel",date=str(self.day),description="Train",net="100.00",tax="5.00",recoverable=True)]))
        self.url=self.root+"/"+str(row["id"])
        return row

    def approved(self,method="out_of_pocket"):
        self.draft(method)
        self.post(self.url+"/submit",{"revision":1})
        self.client.force_login(self.finance)
        self.post(self.url+"/approve",{"revision":2})

    def test_partial_full_and_retry(self):
        self.approved()
        data={"revision":3,"posting_date":str(self.day)}
        row=self.post(self.url+"/post",data,"post")
        self.assertEqual(self.post(self.url+"/post",data,"post",200),row)
        self.assertEqual(dict(Entry.objects.get(pk=row["entry_id"]).lines.values_list("account__code","amount")),
                         {"EMP-EXP":100,"EXP-TAX":5,"EMP-PAY":-105})
        data=dict(revision=4,posting_date=str(self.day),reference="BANK-01",amount="40.00")
        row=self.post(self.url+"/reimburse",data,"pay")
        self.assertEqual(row["balance_due"],"65.00")
        self.post(self.url+"/reimburse",data,"pay",200)
        self.post(self.url+"/reimburse",dict(data,revision=5,amount="66"),status=400)
        row=self.post(self.url+"/reimburse",dict(data,revision=5,amount="65",reference="BANK-02"))
        self.assertEqual((row["status"],row["balance_due"]),("reimbursed","0.00"))
        self.assertEqual(Settlement.objects.count(),2)
        for payment in Settlement.objects.all():
            self.assertEqual(dict(payment.entry.lines.values_list("account__code","amount")),{"1100":-payment.amount,"EMP-PAY":payment.amount})
        self.assertEqual(AuditEvent.objects.filter(action="expenses.reimburse").count(),2)

    def test_company_paid_has_no_employee_payable(self):
        self.approved("company_paid")
        row=self.post(self.url+"/post",dict(revision=3,posting_date=str(self.day)))
        self.assertEqual(row["balance_due"],"0.00")
        self.assertEqual(Entry.objects.get(pk=row["entry_id"]).lines.get(account__code="1100").amount,-105)
        self.post(self.url+"/reimburse",dict(revision=4,amount="1"),status=400)

    def test_receipt_owner_scope(self):
        row=self.draft()
        path=self.url+"/lines/"+str(row["lines"][0]["id"])+"/receipt"
        pdf=b"%PDF-1.4\nbody\n%%EOF"
        res=self.client.post(path,dict(revision="1",file=SimpleUploadedFile("proof.pdf",pdf)),HTTP_IDEMPOTENCY_KEY="proof")
        self.assertEqual(res.status_code,201,res.content)
        self.assertEqual(b"".join(self.client.get(path).streaming_content),pdf)
        self.client.force_login(self.account("peer","view","create"))
        self.assertEqual(self.client.get(path).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(path).status_code,401)

    def test_closed_period_rolls_back(self):
        self.approved()
        self.period.closed_at=timezone.now()
        self.period.save()
        self.post(self.url+"/post",dict(revision=3,posting_date=str(self.day)),status=409)
        self.assertEqual(self.client.get(self.url).json()["revision"],3)

    def test_revision_and_separation(self):
        self.draft()
        self.post(self.url+"/submit",dict(revision=0),status=409)
        self.post(self.url+"/submit",dict(revision=1))
        role=Role.objects.get(name="owner")
        role.permissions.append("expenses.approve")
        role.save()
        self.post(self.url+"/approve",dict(revision=2),status=403)
        self.client.force_login(self.finance)
        self.post(self.url+"/reject",dict(revision=2,reason="Missing evidence"))
        self.client.force_login(self.owner)
        self.assertEqual(self.post(self.url+"/cancel",dict(revision=3))["status"],"cancelled")

    def test_staff_preparation_and_site_limits(self):
        self.client.force_login(self.account("assistant","view","create"))
        data=dict(employee_id=self.employee.id,site_id=self.site.id,currency="AED",method="company_paid",
                  lines=[dict(category="meals",description="Meeting",date=str(self.day),net="10.25")])
        self.post(self.root,data,status=403)
        other=self.account("clerk","view","create","delegate")
        self.client.force_login(other)
        row=self.post(self.root,data)
        self.assertEqual(row["gross"],"10.25")
        self.assertEqual(self.client.get(self.root+"?site="+str(self.site.id)).json()["count"],1)
        self.assertEqual(self.client.get("/api/expenses/settlements").status_code,403)
        self.post(self.root,{**data,"currency":"XXX"},status=400)

    def test_widgets_and_reconciliation_feed(self):
        self.approved()
        self.post(self.url+"/post",dict(revision=3,posting_date=str(self.day)))
        self.post(self.url+"/reimburse",dict(revision=4,posting_date=str(self.day),amount="4",reference="TRX"))
        result=self.client.get("/api/expenses/settlements?q=TRX").json()
        self.assertEqual(result["results"][0]["entry_id"],Settlement.objects.get().entry_id)
        self.assertEqual(self.client.get("/api/workspace/dashboard/blocks").json()["widgets"]["expense-due"]["amount"],"101.00")
