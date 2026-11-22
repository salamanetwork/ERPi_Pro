import csv
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from backbone.models import Assignment, ModuleState, Role, Site
from .models import Account, BudgetMonth, BudgetVersion, Entry, Ledger


class BudgetTests(TestCase):
    def setUp(self):
        ModuleState.objects.create(module_id="accounting", enabled=True)
        self.a = Site.objects.create(name="Plant A")
        self.b = Site.objects.create(name="Plant B")
        self.preparer = self.role("preparer", "budget.centers", "budget.prepare", "budget.approve", "platform.export")
        self.approver = self.role("approver", "budget.approve")
        self.expense = Account.objects.get(ledger_id=1, code="5200")
        self.center = self.send(self.preparer, "/centers", {"site_id": self.a.pk, "code": "MAINT", "name": "Maintenance"})["id"]
        self.filters = {"year": "2026", "as_of": "2026-11-15"}
        self.payload = {"site_id": self.a.pk, "center_id": self.center, "account_id": self.expense.pk,
                        "year": 2026, "months": {"11": "100.00", "12": "200.00"}, "threshold": 75}

    def role(self, name, *permissions):
        user = get_user_model().objects.create_user(username=name)
        role = Role.objects.create(name=name, permissions=["budget.view", *permissions])
        Assignment.objects.create(user=user, role=role, site=self.a)
        client = Client()
        client.force_login(user)
        return client

    def send(self, client, path, payload, expected=201, key="fixture"):
        response = client.post("/api/budgeting" + path, payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, expected, response.content)
        return response.json()

    def advance(self, client, row, action, expected=200):
        return self.send(client, f"/budgets/{row['id']}", {"action": action, "revision": row["revision"]}, expected)

    def journal(self, amount, *, posted=True, site=None, center=None, day=10):
        entry = Entry.objects.create(ledger=Ledger.objects.get(pk=1), date=date(2026, 11, day), kind="manual",
                                     created_by="tester", posted_at=timezone.now() if posted else None)
        entry.lines.create(account=self.expense, site=site or self.a, cost_center_id=center, amount=amount)

    def test_debit_minus_credit_report_csv_and_scope(self):
        first = self.advance(self.preparer, self.send(self.preparer, "/budgets", self.payload), "submit")
        self.advance(self.preparer, first, "approve", 403)
        self.advance(self.approver, first, "approve")
        self.journal(100, center=self.center)
        self.journal(-25, center=self.center)
        self.journal(50, posted=False, center=self.center)
        self.journal(42, site=self.b)
        self.journal(20, center=self.center, day=16)
        self.journal(5)
        result = self.preparer.get("/api/budgeting/report", self.filters).json()
        row = next(item for item in result["items"] if item["center_id"] == self.center)
        self.assertEqual(tuple(Decimal(row[k]) for k in ("debit", "credit", "actual", "remaining")), (100, 25, 75, 25))
        self.assertEqual(row["status"], "warning")
        self.assertEqual(result["ytd"]["actual"], "80.00")
        self.assertEqual(next(item for item in result["items"] if item["center_id"] is None)["status"], "unbudgeted")
        detail = self.preparer.get("/api/budgeting/report/details", {
            **self.filters, "site": self.a.pk, "center": self.center, "account": self.expense.pk, "month": 11}).json()
        self.assertEqual(Decimal(detail["actual"]), 75)
        self.assertEqual(len(detail["items"]), 2)
        export = self.preparer.get("/api/budgeting/report/export", self.filters)
        data = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
        self.assertEqual(sum(Decimal(item["actual"]) for item in data), 80)

    def test_versions_and_month_db_constraint(self):
        initial = self.advance(self.preparer, self.send(self.preparer, "/budgets", self.payload), "submit")
        self.advance(self.approver, initial, "approve")
        newer = self.send(self.preparer, "/budgets", {**self.payload, "reason": "Forecast revised"}, key="second")
        month = BudgetMonth.objects.get(budget_id=initial["id"], month=11)
        duplicate = {key: getattr(month, key) for key in ("site_id", "center_id", "account_id", "year", "version", "month", "amount")}
        with self.assertRaises(IntegrityError), transaction.atomic():
            BudgetMonth.objects.create(budget_id=newer["id"], **duplicate)
        self.advance(self.approver, self.advance(self.preparer, newer, "submit"), "approve")
        self.assertFalse(BudgetVersion.objects.get(pk=initial["id"]).current)
        self.assertTrue(BudgetVersion.objects.get(pk=newer["id"]).current)
        self.assertEqual(BudgetVersion.objects.filter(state="approved").count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BudgetVersion.objects.filter(pk=initial["id"]).update(current=True)
