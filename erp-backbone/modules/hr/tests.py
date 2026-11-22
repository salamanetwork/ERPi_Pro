from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounting.models import Account, CostCenterScope, Entry, Ledger, Line, Period, Reference
from backbone.models import Assignment, ModuleState, Role, Site
from .models import Compensation, Payment, PayrollRun, Payslip


class PayrollAcceptanceTests(TestCase):
    def setUp(self):
        for module in ("accounting", "hr", "payroll"):
            ModuleState.objects.update_or_create(module_id=module, defaults={"enabled": True})
        self.site = Site.objects.create(name="Test Site A")
        self.other_site = Site.objects.create(name="Test Site B")
        self.creator = self.member("creator", ["hr.manage", "hr.identity.link", "payroll.prepare", "payroll.approve"])
        self.review = self.member("review", ["hr.leave.approve", "payroll.approve", "payroll.post", "payroll.pay"])
        self.manager = self.member("manager", ["payroll.summary", "budget.view"])
        self.outsider = self.member("outsider", ["payroll.summary", "payroll.payslip"], self.other_site)
        self.worker = self.member("worker", ["hr.self"])
        self.worker2 = self.member("worker2", ["hr.self", "payroll.payslip.self"])
        self.day = "2026-11-01"
        self.ledger = Ledger.objects.get(pk=1)
        self.ledger.cutoff = date.fromisoformat(self.day)
        self.ledger.activated_at = timezone.now()
        self.ledger.save()
        self.period = Period.objects.create(ledger=self.ledger, start=self.day, end="2026-11-30")
        self.center = Reference.objects.create(ledger=self.ledger, kind="cost_center", code="MAINT-A", name="Maintenance")
        CostCenterScope.objects.create(site=self.site, reference=self.center)
        self.seq = 0
        self.e1 = self.employee("E1", self.worker)
        self.e2 = self.employee("E2", self.worker2)

    def member(self, username, permissions, site=None):
        user = get_user_model().objects.create_user(username)
        Assignment.objects.create(user=user, role=Role.objects.create(name=username, permissions=permissions),
                                  site=site or self.site)
        return user

    def post(self, user, path, payload, status=201, key=None):
        self.client.force_login(user)
        self.seq += 1
        response = self.client.post("/api/hr" + path, payload, content_type="application/json",
                                    headers={"Idempotency-Key": key or f"hr-{self.seq}"})
        self.assertEqual(response.status_code, status, response.content)
        self.assertIn("no-store", response.headers["Cache-Control"])
        return response.json()

    def employee(self, code, user):
        employee = self.post(self.creator, "/employees", {
            "site_id": self.site.pk, "number": code, "name": user.username, "department": "Maintenance",
            "title": "Technician", "joined": self.day, "user_id": user.pk,
        })
        self.post(self.creator, f"/employees/{employee['id']}/compensation", {
            "effective": self.day, "center_id": self.center.pk, "basic": "0.10", "allowance": "0.20",
        })
        return employee["id"]

    def action(self, run, user, action, status=201, key=None, **fields):
        return self.post(user, f"/payroll/{run['id']}/action",
                         {"revision": run["revision"], "action": action, **fields}, status, key)

    def run_draft(self):
        return self.post(self.creator, "/payroll", {"site_id": self.site.pk, "month": self.day})

    def get(self, user, path, status=200):
        self.client.force_login(user)
        response = self.client.get(path)
        self.assertEqual(response.status_code, status, response.content)
        return response

    def test_decimal_review_post_pay_and_privacy(self):
        run = self.run_draft()
        inputs = f"/payroll/{run['id']}/inputs"
        payload = {"action": "add", "kind": "deduction", "amount": "0.01", "employee_id": self.e1,
                   "reason": "Pilot deduction", "reference": "D1"}
        self.post(self.creator, inputs, {**payload, "amount": 0.01}, 400)
        item = self.post(self.creator, inputs, payload)
        invalid = self.action(run, self.creator, "calculate")
        self.assertEqual(invalid["issues"][0]["code"], "payroll_input_pending")
        self.post(self.creator, inputs, {"action": "approve", "id": item["id"]}, 403)
        self.post(self.review, inputs, {"action": "approve", "id": item["id"]})
        calc = self.action(invalid, self.creator, "calculate")
        self.assertEqual(calc["preview"], {"gross": "0.60", "deduction": "0.01", "net": "0.59"})
        run = self.action(calc, self.creator, "submit")
        self.action(run, self.creator, "approve", 403)
        inbox = self.get(self.review, "/api/inbox").json()["items"]
        self.assertEqual([r["action"] for r in inbox], ["approve_payroll"])
        self.assertEqual(inbox[0]["kind"], "payroll")
        self.assertEqual(inbox[0]["route"], f"/payroll/runs/{run['id']}")
        self.assertNotIn("0.59", str(inbox))
        run = self.action(run, self.review, "approve")
        self.assertEqual(self.get(self.review, "/api/inbox").json()["items"], [])
        self.assertEqual(self.get(self.worker, "/api/hr/employees").json()["items"][0]["id"], self.e1)
        own = Payslip.objects.get(employee_id=self.e2)
        other = Payslip.objects.get(employee_id=self.e1)
        for user, forbidden in [(self.worker, other), (self.worker2, other), (self.manager, own), (self.outsider, own)]:
            self.get(user, f"/api/hr/payslips/{forbidden.pk}", 404)
            self.get(user, f"/hr/print/payslip/{forbidden.pk}", 404)
        self.assertEqual([r["id"] for r in self.get(self.worker2, "/api/hr/payslips").json()["items"]], [own.pk])
        printed = self.get(self.worker2, f"/hr/print/payslip/{own.pk}?language=ar")
        self.assertContains(printed, 'dir="rtl"')
        self.assertIn("no-store", printed.headers["Cache-Control"])
        manager = self.get(self.manager, "/api/hr/payroll").json()["items"][0]
        self.assertEqual(manager["net"], "0.59")
        self.assertNotIn("employee", str(manager))
        self.get(self.manager, f"/api/hr/payroll/{run['id']}/inputs", 404)
        self.assertEqual(self.get(self.outsider, "/api/hr/payroll").json()["items"], [])
        posted = self.action(run, self.review, "post", key="post-stable", posting_date=self.day)
        repeat = self.action(run, self.review, "post", 200, "post-stable", posting_date=self.day)
        self.assertEqual(posted["entry_id"], repeat["entry_id"])
        lines = {line.account.code: (line.amount, line.cost_center_id)
                 for line in Line.objects.filter(entry_id=posted["entry_id"]).select_related("account")}
        self.assertEqual(lines, {"PAY-BASIC": (Decimal("0.20"), self.center.pk),
                                 "PAY-ALLOW": (Decimal("0.40"), self.center.pk),
                                 "PAY-NET": (Decimal("-0.59"), None), "PAY-DEDUCT": (Decimal("-0.01"), None)})
        budget = self.get(self.manager, f"/api/budgeting/report?year=2026&as_of={self.day}&center={self.center.pk}")
        self.assertEqual(sum(Decimal(row["actual"]) for row in budget.json()["items"]), Decimal("0.60"))
        paid = self.action(posted, self.review, "pay", posting_date=self.day, amount="0.20", reference="BANK-001")
        self.assertEqual(paid["outstanding"], "0.39")
        self.assertEqual(self.get(self.review, "/api/hr/payroll").json()["items"][0]["payments"][0]["reference"], "BANK-001")
        self.assertEqual(self.get(self.manager, "/api/hr/payroll").json()["items"][0]["payments"], [])
        self.action(paid, self.review, "pay", 409, posting_date=self.day, amount="0.40", reference="BANK-002")
        self.action(paid, self.review, "reverse", 409, posting_date=self.day, reason="Pilot correction")

    def test_stale_sources_leave_and_unpaid_reversal(self):
        self.post(self.creator, f"/employees/{self.e1}/leave-balance", {"day": self.day, "days": 4, "reason": "Opening"})
        leave = self.post(self.worker, "/leaves", {"employee_id": self.e1, "start": self.day, "end": self.day,
                                                   "paid": True, "reason": "Pilot"})
        self.assertEqual(self.get(self.review, "/api/inbox").json()["items"][0]["action"], "approve_leave")
        self.post(self.review, f"/leaves/{leave['id']}", {"action": "approve"}, key="leave-approve")
        self.post(self.review, f"/leaves/{leave['id']}", {"action": "approve"}, 200, "leave-approve")
        self.assertEqual(self.get(self.worker, f"/api/hr/employees/{self.e1}/leave-balance").json()["available"], 3)
        draft = self.run_draft()
        calc = self.action(draft, self.creator, "calculate")
        self.post(self.creator, f"/employees/{self.e1}/compensation", {
            "effective": "2026-11-04", "center_id": self.center.pk, "basic": "0.50", "allowance": "0.20",
        })
        self.assertEqual(self.action(calc, self.creator, "submit", 409)["error"], "payroll_recalculate")
        inputs = f"/payroll/{draft['id']}/inputs"
        item = self.post(self.creator, inputs, {"action": "add", "kind": "review", "amount": "0.00",
                                               "employee_id": self.e1, "reason": "Mid-month review", "reference": "R1"})
        self.post(self.review, inputs, {"action": "approve", "id": item["id"]})
        calc = self.action(calc, self.creator, "calculate")
        submitted = self.action(calc, self.creator, "submit")
        approved = self.action(submitted, self.review, "approve")
        posted = self.action(approved, self.review, "post", posting_date=self.day)
        duplicate = PayrollRun.objects.create(site=self.other_site, prepared_by=self.creator, month=self.day)
        slip = Payslip.objects.get(employee_id=self.e1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payslip.objects.create(run=duplicate, employee_id=self.e1, month=self.day, center=self.center,
                                   snapshot={}, gross=slip.gross, deduction=slip.deduction, net=slip.net)
        reversed_run = self.action(posted, self.review, "reverse", posting_date=self.day, reason="Pilot correction")
        self.assertEqual(reversed_run["state"], "reversed")
        self.assertEqual(reversed_run["outstanding"], "0.00")
        self.assertFalse(Payslip.objects.filter(run_id=posted["id"], reserved=True).exists())
        actual = Line.objects.filter(account=Account.objects.get(code="PAY-BASIC"), cost_center=self.center)
        self.assertEqual(sum(row.amount for row in actual), Decimal("0.00"))
        self.assertNotEqual(self.run_draft()["id"], posted["id"])

    def test_approved_balance_and_payment_after_independent_review(self):
        Compensation.objects.update(basic=Decimal("5000.00"), allowance=Decimal("225.00"))
        finance_role = Role.objects.create(name="Payroll Finance", permissions=["payroll.post", "payroll.pay"])
        Assignment.objects.create(user=self.creator, role=finance_role)
        outside_finance = self.member("outside-finance", ["payroll.post", "payroll.pay"], self.other_site)
        draft = self.run_draft()
        calculated = self.action(draft, self.creator, "calculate")
        submitted = self.action(calculated, self.creator, "submit")
        self.action(submitted, self.creator, "approve", 403)
        approved = self.action(submitted, self.review, "approve")
        details = self.get(self.creator, "/api/hr/payroll").json()["items"][0]
        self.assertTrue(details["can_post"])
        self.assertFalse(details["can_reverse"])
        self.assertFalse(self.get(self.manager, "/api/hr/payroll").json()["items"][0]["can_post"])
        self.assertEqual(details["outstanding"], "10450.00")
        self.assertEqual(details["paid"], "0.00")
        self.action(approved, self.creator, "pay", 409, posting_date=self.day, amount="1.00", reference="EARLY")
        self.action(approved, outside_finance, "post", 404, posting_date=self.day)
        self.action(approved, self.manager, "post", 404, posting_date=self.day)

        posted = self.action(approved, self.creator, "post", posting_date=self.day)
        self.assertEqual(posted["outstanding"], "10450.00")
        self.action(posted, self.creator, "reverse", 403, posting_date=self.day, reason="Correction")
        paid = self.action(posted, self.creator, "pay", key="balance-payment", posting_date=self.day,
                           amount="10450.00", reference="BANK-FULL")
        self.action(posted, self.creator, "pay", 200, "balance-payment", posting_date=self.day,
                    amount="10450.00", reference="BANK-FULL")
        self.assertEqual(paid["outstanding"], "0.00")
        self.assertEqual(paid["paid"], "10450.00")
        self.assertEqual(Payment.objects.filter(run_id=posted["id"]).count(), 1)
        payment_entry = Entry.objects.get(kind="hr_pay")
        self.assertEqual(dict(payment_entry.lines.values_list("account__code", "amount")), {
            "PAY-NET": Decimal("10450.00"), "1100": Decimal("-10450.00"),
        })
        self.action(paid, self.creator, "pay", 409, posting_date=self.day, amount="0.01", reference="OVER")

    def test_disabling_payroll_keeps_hr_and_hides_payroll_data_and_approvals(self):
        run = self.run_draft()
        PayrollRun.objects.filter(pk=run["id"]).update(state="submitted")
        self.assertEqual(len(self.get(self.review, "/api/inbox?kind=payroll").json()["items"]), 1)

        ModuleState.objects.filter(pk="payroll").update(enabled=False)
        for user, url in ((self.review, "/api/hr/payroll"), (self.worker2, "/api/hr/payslips"),
                          (self.creator, f"/api/hr/employees/{self.e1}/compensation")):
            self.assertEqual(self.get(user, url, 404).json()["error"], "module_disabled")
        self.assertEqual(self.get(self.review, "/api/inbox?kind=payroll").json()["items"], [])
        self.assertEqual(len(self.get(self.creator, "/api/hr/employees").json()["items"]), 2)
        self.assertTrue(PayrollRun.objects.filter(pk=run["id"], state="submitted").exists())
