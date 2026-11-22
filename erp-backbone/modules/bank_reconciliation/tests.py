import json
from datetime import date
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from accounting.models import Account,Entry,Ledger,Period
from accounting.services import posted_entry
from backbone.models import Assignment, AuditEvent, ModuleState, Role, Site
from expenses.models import ExpenseClaim, Settlement
from hr.models import Employee
from .models import BankStatement, ClearedLine

class BankTests(TestCase):
    def setUp(self):
        ModuleState.objects.create(module_id="bank_reconciliation",enabled=True)
        self.site=Site.objects.create(name="HQ")
        self.user=get_user_model().objects.create_user("banker")
        self.role=Role.objects.create(name="Treasury",permissions=["bank_reconciliation."+x for x in ("view","import","reconcile","close","post_adjustment")])
        Assignment.objects.create(user=self.user,site=self.site,role=self.role)
        self.client.force_login(self.user)
        self.base="/api/bank_reconciliation/"
        self.ledger=Ledger.objects.get(pk=1)
        self.day=date(2026,11,1)
        self.ledger.activated_at=timezone.now()
        self.ledger.cutoff=self.day
        self.ledger.save()
        self.period=Period.objects.create(ledger=self.ledger,start=self.day,end=date(2026,11,30))
        self.bank,self.fee,self.interest=[Account.objects.get(code=k) for k in ("1100","5200","4100")]
        p=self.post(self.base+"profiles",dict(site_id=self.site.pk,name="Bank",last_four="1299",account_id=self.bank.pk,
                     charges_id=self.fee.pk,interest_id=self.interest.pk,tolerance="0.05"))
        self.profile=p["id"]
    def post(self,url,data,status=201,key=None):
        r=self.client.post(url,data,content_type="application/json",HTTP_IDEMPOTENCY_KEY=key or uuid4().hex)
        self.assertEqual(r.status_code,status,r.content)
        return r.json()
    def statement(self,end="100.00"):
        self.state=self.post(self.base+"statements",dict(profile_id=self.profile,start=str(self.day),end="2026-11-30",opening="0.00",closing=end))
        self.url=self.base+"statements/"+str(self.state["id"])
    def act(self,a,status=201,**data):
        row=self.post(self.url+"/"+a,dict(revision=self.state["revision"],**data),status)
        if status==201:self.state=row
        return row
    def bankline(self,amount):
        self.act("line",date=str(self.day),description="Bank transfer",amount=str(amount))
        return BankStatement.objects.get(pk=self.state["id"]).lines.latest("pk").pk
    def gl(self,amount):
        entry=posted_entry(self.ledger,self.period,self.day,"banker","Transfer","manual",
                [(self.bank,Decimal(str(amount)),self.site.pk,None),(self.fee,-Decimal(str(amount)),self.site.pk,None)])
        return entry.lines.get(account=self.bank)
    def test_batch_and_close(self):
        self.statement()
        b=self.bankline(100)
        a,c=self.gl(60),self.gl(40)
        self.act("start")
        self.act("match",bank_ids=[b],gl_ids=[a.pk,c.pk])
        self.assertEqual(self.state["difference"],"0.00")
        self.act("close")
        self.act("line",409,date=str(self.day),description="x",amount="1")
        self.assertTrue(AuditEvent.objects.filter(action="bank_reconciliation.close").exists())
        self.assertEqual(self.client.get(self.url+"/candidates").json()["count"],0)
        self.post(self.base+"statements",dict(profile_id=self.profile,start="2026-12-01",end="2026-12-31",opening="0.00",closing="100"),409)
    def test_many_to_one_unmatch_duplicate_and_revision(self):
        self.statement()
        a,b=self.bankline(45),self.bankline(55)
        gl=self.gl(100)
        self.act("start")
        self.post(self.url+"/match",dict(revision=1,bank_ids=[a,b],gl_ids=[gl.pk]),409)
        self.act("match",bank_ids=[a,b],gl_ids=[gl.pk])
        self.act("match",409,bank_ids=[a,b],gl_ids=[gl.pk])
        group=ClearedLine.objects.get(line=gl).match_id
        self.act("unmatch",match_id=group)
        self.assertEqual(self.state["difference"],"100.00")
        self.assertEqual(self.client.get(self.url+"/candidates").json()["count"],1)
    def test_fee_posted_once_and_period_lock(self):
        self.statement("-12")
        b=self.bankline(-12)
        self.act("start")
        data=dict(revision=self.state["revision"],kind="fee",line_id=b)
        self.period.closed_at=timezone.now();self.period.save()
        self.post(self.url+"/adjust",data,409)
        self.period.closed_at=None;self.period.save()
        self.post(self.url+"/adjust",data,key="fee")
        self.post(self.url+"/adjust",data,200,key="fee")
        e=Entry.objects.get(kind="bank_adj")
        self.assertEqual(dict(e.lines.values_list("account__code","amount")),{"1100":-12,"5200":12})
    def test_rounding_not_implicit(self):
        self.statement("9.98")
        b=self.bankline("9.98");g=self.gl(10)
        self.act("start")
        self.act("match",409,bank_ids=[b],gl_ids=[g.pk])
        self.act("adjust",line_id=b,gl_ids=[g.pk],kind="rounding")
        self.assertEqual(Entry.objects.get(kind="bank_adj").lines.get(account=self.bank).amount,Decimal("-0.02"))
        self.act("close")
    def test_employee_reimbursement_and_site_scope(self):
        self.statement("-40")
        b=self.bankline(-40);g=self.gl(-40)
        emp=Employee.objects.create(number="Bank1",name="Walid",joined=self.day,site=self.site,user=self.user)
        claim=ExpenseClaim.objects.create(employee=emp,site=self.site,creator=self.user,currency="AED",method="out_of_pocket",gross=40)
        Settlement.objects.create(claim=claim,entry=g.entry,date=self.day,reference="BANK-001",actor=self.user,amount=40)
        self.assertEqual(self.client.get(self.url+"/candidates",{"q":"BANK-001","line":b}).json()["results"][0]["party"],"Walid")
        self.act("start");self.act("match",bank_ids=[b],gl_ids=[g.pk])
        other=Site.objects.create(name="Elsewhere")
        Assignment.objects.filter(user=self.user).update(site=other)
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.post(self.url+"/close",dict(revision=self.state["revision"]),404)
    def test_zero_offset_unmatched_and_integrity(self):
        self.statement("1")
        self.bankline(10);self.bankline(-10);self.act("start")
        self.assertEqual(self.state["integrity_difference"],"1.00")
        self.act("close",409)
        self.act("edit",closing="0")
        self.act("close",409)
    def test_csv_mapping_preview_atomicity_and_private_attachment(self):
        self.statement("25")
        content="Day,Narration,Dr,Cr,Running\n2026-11-01,Deposit,,40,40\n2026-11-02,Payment,15,,25\n"
        def upload(action,txt,status):
            r=self.client.post(self.url+"/"+action,dict(file=SimpleUploadedFile("statement.csv",txt.encode()),revision=self.state["revision"],
                 mapping=json.dumps(dict(date="Day",description="Narration",credit="Cr",debit="Dr",running_balance="Running")),attach="true"),HTTP_IDEMPOTENCY_KEY=uuid4().hex)
            self.assertEqual(r.status_code,status,r.content)
            return r.json()
        self.assertEqual(upload("preview",content,200)["count"],2)
        upload("import_csv",content.replace("15,,25","15,,24"),400)
        self.assertFalse(ClearedLine.objects.exists())
        self.assertEqual(self.client.get(self.url).json()["lines"]["count"],0)
        self.state=upload("import_csv",content,201)
        upload("import_csv",content,409)
        f=self.client.get(self.url).json()["attachments"][0]
        self.assertEqual(b"".join(self.client.get(self.url+"/attachments/"+str(f["id"])).streaming_content),content.encode())
        self.client.logout()
        self.assertEqual(self.client.get(self.url+"/attachments/"+str(f["id"])).status_code,401)
    def test_closed_statement_gl_not_reusable_next_month(self):
        self.statement()
        b=self.bankline(100);g=self.gl(100)
        self.act("start");self.act("match",bank_ids=[b],gl_ids=[g.pk]);self.act("close")
        self.state=self.post(self.base+"statements",dict(profile_id=self.profile,start="2026-12-01",end="2026-12-31",opening="100",closing="200"))
        self.url=self.base+"statements/"+str(self.state["id"])
        self.act("line",date="2026-12-01",amount="100",description="Other transfer")
        other=BankStatement.objects.get(pk=self.state["id"]).lines.get()
        self.act("start")
        self.act("match",409,bank_ids=[other.pk],gl_ids=[g.pk])
        self.assertEqual(ClearedLine.objects.filter(line=g).count(),1)

    def test_fee_requires_post_permission(self):
        self.statement("-3");b=self.bankline(-3);self.act("start")
        self.role.permissions.remove("bank_reconciliation.post_adjustment");self.role.save()
        self.act("adjust",403,kind="fee",line_id=b)
        self.assertFalse(Entry.objects.filter(kind="bank_adj").exists())

    def upload_csv(self, raw, mapping=None, *, action="import_csv", filename="lines.csv"):
        fields = {"date": "Day", "description": "Text", "amount": "Amount"}
        return self.client.post(self.url + "/" + action, {
            "file": SimpleUploadedFile(filename, raw),
            "revision": self.state["revision"],
            "mapping": json.dumps(fields if mapping is None else mapping),
        }, HTTP_IDEMPOTENCY_KEY=uuid4().hex)

    def test_csv_row_limit(self):
        self.statement()
        row = b"2026-11-01,Deposit,1\n"
        raw = b"Day,Text,Amount\n" + row * 5000
        self.assertEqual(self.upload_csv(raw, action="preview").json()["count"], 5000)
        response = self.upload_csv(raw + row)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_csv_row")

    def test_csv_byte_limit(self):
        self.statement()
        raw = b"Day,Text,Amount\n2026-11-01,Deposit,1\n"
        # Bytes following an unmapped column count towards the upload limit too.
        raw = raw.replace(b"Amount\n", b"Amount,Unused\n").rstrip() + b","
        chunk = b"x" * 64000
        row = b"\n2026-11-01,Deposit,1,"
        while len(raw) + len(chunk) + len(row) < 2 * 1024**2:
            raw += chunk + row
        raw = raw.ljust(2 * 1024**2, b"x")
        self.assertEqual(self.upload_csv(raw, action="preview").status_code, 200)
        response = self.upload_csv(raw + b"x")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_csv_file")

    def test_csv_utf8_bom(self):
        self.statement()
        raw = "Day,Text,Amount\n2026-11-01,إيداع,1\n".encode("utf-8-sig")
        self.assertEqual(self.upload_csv(raw).status_code, 201)
        self.assertEqual(BankStatement.objects.get(pk=self.state["id"]).lines.get().description, "إيداع")

    def test_csv_duplicate_bytes_under_new_filename(self):
        self.statement()
        raw = b"Day,Text,Amount\n2026-11-01,Deposit,1\n"
        first = self.upload_csv(raw)
        self.assertEqual(first.status_code, 201)
        self.state = first.json()
        second = self.upload_csv(raw, filename="renamed.csv")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"], "duplicate_import")
        self.assertEqual(BankStatement.objects.get(pk=self.state["id"]).lines.count(), 1)

    def test_csv_running_balance_mismatch(self):
        self.statement()
        raw = b"Day,Text,Amount,Bal\n2026-11-01,First,5,5\n2026-11-02,Second,-2,4\n"
        response = self.upload_csv(raw, dict(date="Day", description="Text", amount="Amount", running_balance="Bal"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "running_balance_mismatch")
        self.assertFalse(BankStatement.objects.get(pk=self.state["id"]).lines.exists())

    def test_csv_invalid_mapping_preserves_revision_and_existing_lines(self):
        self.statement()
        line = self.bankline(5)
        response = self.upload_csv(b"Day,Text,Amount\n2026-11-02,Second,1\n",
                                   dict(date="Day", description="Text", amount="Missing"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_column_mapping")
        statement = BankStatement.objects.get(pk=self.state["id"])
        self.assertEqual(statement.revision, self.state["revision"])
        self.assertEqual(statement.lines.get().pk, line)
