from datetime import date, timedelta
from importlib import import_module
from django.apps import apps
from decimal import Decimal
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

from django.db import connections, transaction
from django.test import Client, TransactionTestCase
from django.utils import timezone

from backbone.models import AuditEvent, ModuleState
from inventory.models import Item, Location, Unit, Warehouse, Movement
from purchasing.test_orders import PurchaseOrderFixture
from .models import Account, Accrual, Entry, Posting
from .services import ledger_lock, observe


class AccountingTests(PurchaseOrderFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        for module in ("inventory", "accounting"):
            ModuleState.objects.create(module_id=module, enabled=True)
        with connections["default"].schema_editor() as editor:
            import_module("accounting.migrations.0002_default_ledger").seed(apps, editor)
        self.unit, _ = Unit.objects.get_or_create(code="PC", defaults={"name": "Piece"})
        self.item = Item.objects.create(code="SEAL", name="Seal", base_unit=self.unit)
        self.location = Location.objects.create(code="A1", name="Shelf", warehouse=Warehouse.objects.create(
            site=self.east, code="WH", name="Main"))
        self.cut = date(2026, 11, 1)
        self.period = self.post("/api/accounting/periods", {"start": self.cut.isoformat()}).json()["id"]
        self.next_period = self.post("/api/accounting/periods", {"start": "2026-12-01"}).json()["id"]
        self.accounts = dict(Account.objects.values_list("code", "id"))

    def movement(self, kind, value, day=None, original=None, historical=None):
        with transaction.atomic():
            ledger = ledger_lock()
            row = Movement.objects.create(site=self.east, item=self.item, location=self.location,
                actor="admin", actor_id=self.admin.pk, occurred_at=timezone.now(), business_date=day or self.cut,
                quantity=1 if kind in ("opening", "receipt") else -1, value=Decimal(value), kind=kind,
                original=original, facts={"currency": "AED", "original_receipt_value": historical})
            observe(ledger, row)
            return row

    def approve(self):
        path = "/api/accounting/cutover"
        preview = self.post(path, {"cutoff": self.cut.isoformat()}).json()
        response = self.post(path, {"cutoff": self.cut.isoformat(), "token": preview["token"]})
        self.assertEqual(response.status_code, 201, response.content)
        return preview

    def journal_amounts(self, movement):
        row = Posting.objects.get(movement=movement)
        return dict(row.entry.lines.values_list("account__code", "amount"))

    def test_cutover_grni_return_variance_correction_and_trial(self):
        old = self.cut - timedelta(days=1)
        self.movement("opening", "50", old)
        first = self.movement("receipt", "100", old)
        preview = self.approve()
        self.assertEqual(preview["inventory"][0]["value"], "150.00")
        self.assertEqual(preview["grni"][0]["movement_id"], first.pk)
        self.assertEqual(Posting.objects.get(movement=first).state, "opening")
        new = self.movement("receipt", "200")
        returned = self.movement("return", "-110", original=first, historical="100.00")
        credit = self.movement("return", "-90", original=new, historical="100.00")
        corrected = self.movement("correction", "-50", original=new, historical="50.00")
        self.assertEqual(self.journal_amounts(returned), {"1200": -110, "2100": 100, "5100": 10})
        self.assertEqual(self.journal_amounts(credit), {"1200": -90, "2100": 100, "5100": -10})
        self.assertEqual(self.journal_amounts(corrected), {"1200": -50, "2100": 50})
        self.assertEqual(Accrual.objects.get(receipt_movement=new).remaining, Decimal("50"))
        rows = self.client.get("/api/accounting/reports/trial?start=2026-11-01&end=2026-11-30").json()["items"]
        self.assertEqual(sum(Decimal(row["closing"]) for row in rows), 0)
        self.assertEqual(next(row["closing"] for row in rows if row["code"] == "1200"), "100.00")
        self.assertEqual(next(row["closing"] for row in rows if row["code"] == "2100"), "-50.00")
        self.assertEqual(self.client.get("/api/accounting/reports/grni").json()["items"][0]["value"], "50.00")

    def test_manual_reason_audit_controls_permissions_and_reversal(self):
        self.approve()
        path = "/api/accounting/journals"
        payload = {"date": "2026-11-01", "description": "  ",
                   "lines": [{"account_id": self.accounts["1100"], "debit": "15.00", "credit": "0"},
                             {"account_id": self.accounts["4100"], "debit": "0", "credit": "15.00"}]}
        self.assertEqual(self.post(path, payload, HTTP_IDEMPOTENCY_KEY="manual").json()["error"], "invalid_description")
        payload["description"] = "Approved test journal " + "x" * 180
        created = self.post(path, payload, HTTP_IDEMPOTENCY_KEY="manual")
        self.assertEqual(created.status_code, 201, created.content)
        row = created.json()
        self.assertTrue(AuditEvent.objects.filter(action="accounting.journal.create", target__endswith=payload["description"]).exists())
        self.assertEqual(self.post(path, payload, HTTP_IDEMPOTENCY_KEY="manual").json(), row)
        self.assertEqual(self.post(f"{path}/{row['id']}/post", {"revision": 1}, self.buyer).status_code, 403)
        posted = self.post(f"{path}/{row['id']}/post", {"revision": 1})
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual(self.patch(f"{path}/{row['id']}", {**payload, "revision": 2}).status_code, 404)
        reversal = self.post(f"{path}/{row['id']}/reverse", {"date": "2026-11-02", "description": "Reverse fixture"},
                             HTTP_IDEMPOTENCY_KEY="reverse").json()
        self.assertEqual(reversal["lines"][0]["credit"], "15.00")
        self.assertEqual(self.post(f"{path}/{reversal['id']}/post", {"revision": 1}).status_code, 200)
        payload["lines"][0]["account_id"] = self.accounts["1200"]
        self.assertEqual(self.post(path, payload, HTTP_IDEMPOTENCY_KEY="control").status_code, 404)

    def test_pending_period_and_concurrent_retry(self):
        self.approve()
        self.assertEqual(self.post(f"/api/accounting/periods/{self.period}/close", {}).status_code, 200)
        move = self.movement("receipt", "10")
        row = Posting.objects.get(movement=move)
        self.assertEqual(row.error, "finance_period_closed")
        self.assertEqual(self.post(f"/api/accounting/periods/{self.next_period}/close", {}).json()["error"], "finance_pending_postings")
        url = f"/api/accounting/postings/{row.pk}/retry"
        self.assertEqual(self.post(url, {"date": "2026-12-01"}).json()["error"], "invalid_reason")
        barrier = Barrier(2)

        def retry(_):
            try:
                client = Client()
                client.cookies = self.client.cookies.copy()
                barrier.wait(timeout=8)
                return self.post(url, {"date": "2026-12-01", "reason": "Late stock"}, client).json()["entry_id"]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(retry, (1, 2))
        self.assertEqual(first, second)
        self.assertEqual(Entry.objects.filter(kind="receipt").count(), 1)
        self.assertEqual(Entry.objects.get(pk=first).source_date, self.cut)

    def test_rule_versions_and_stale_cutover(self):
        path = "/api/accounting/cutover"
        preview = self.post(path, {"cutoff": self.cut.isoformat()}).json()
        self.movement("opening", "10", self.cut - timedelta(days=1))
        self.assertEqual(self.post(path, {"cutoff": self.cut.isoformat(), "token": preview["token"]}).json()["error"],
                         "finance_preview_changed")
        self.approve()
        move = self.movement("receipt", "10")
        old_rule = Posting.objects.get(movement=move).entry.rule_id
        self.assertEqual(self.post("/api/accounting/rules", {
            "kind": "receipt", "starts": "2026-12-01", "inventory_id": self.accounts["1200"],
            "offset_id": self.accounts["2100"]}).status_code, 201)
        later = self.movement("receipt", "11", date(2026, 12, 1))
        self.assertNotEqual(Posting.objects.get(movement=later).entry.rule_id, old_rule)
        self.assertEqual(Posting.objects.get(movement=move).entry.rule_id, old_rule)

    def test_open_financial_month_validates_first_day_and_reuses_period(self):
        path = "/api/accounting/periods"
        for value in ("2028-02", "02/2028", "2028-02-30", "2028-02-02"):
            result = self.post(path, {"start": value})
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.json()["error"], "invalid_period_start")
        created = self.post(path, {"start": "2028-02-01"})
        self.assertEqual(created.status_code, 201)
        repeated = self.post(path, {"start": "2028-02-01"})
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json(), created.json())
        periods = self.client.get("/api/accounting/setup").json()["periods"]
        self.assertEqual(next(p["end"] for p in periods if p["id"] == created.json()["id"]), "2028-02-29")

    def test_audit_period_overlap_and_report_site_filter(self):
        from accounting.models import Period, Ledger
        from backbone.models import Site
        ledger = Ledger.objects.get(pk=1)
        Period.objects.create(ledger=ledger, start=date(2029, 3, 1), end=date(2029, 3, 15))
        res = self.post("/api/accounting/periods", {"start": "2029-03-01"})
        self.assertEqual(res.status_code, 200)
        # Overlapping custom period
        Period.objects.create(ledger=ledger, start=date(2029, 4, 10), end=date(2029, 5, 10))
        res_overlap = self.post("/api/accounting/periods", {"start": "2029-05-01"})
        self.assertEqual(res_overlap.status_code, 409)
        self.assertEqual(res_overlap.json()["error"], "finance_period_overlap")

        # Trial balance site filter
        self.approve()
        other_site = Site.objects.create(name="Secondary Branch")
        trial = self.client.get(f"/api/accounting/reports/trial?start={self.cut.isoformat()}&end={self.cut.isoformat()}&site_id={other_site.id}")
        self.assertEqual(trial.status_code, 200)
        self.assertEqual(trial.json()["items"], [])

    def test_unbalanced_journal_entry_rejected(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from accounting.models import Account, Entry, Ledger, Period
        from accounting.services import write_lines
        from backbone.http import Problem

        User = get_user_model()
        actor = getattr(self, "user", None) or User.objects.filter(is_active=True).first()
        self.assertIsNotNone(actor)

        ledger = Ledger.objects.get(pk=1)
        period = Period.objects.filter(ledger=ledger).order_by("-start").first()
        accts = list(Account.objects.filter(ledger=ledger, active=True, detail=True)[:2])
        self.assertEqual(len(accts), 2)

        entry = Entry.objects.create(
            ledger=ledger,
            period=period,
            date=period.start if period else self.cut,
            description="Unbalanced check",
            kind="manual",
            created_by=actor,
            posted_by=actor,
        )
        with self.assertRaises(Problem) as ctx:
            write_lines(entry, [
                (accts[0], Decimal("100.00"), None, None),
                (accts[1], Decimal("-60.00"), None, None),
            ])
        self.assertEqual(ctx.exception.code, "finance_unbalanced")