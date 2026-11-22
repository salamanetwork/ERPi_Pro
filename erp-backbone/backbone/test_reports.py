import csv
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounting.models import Account, Accrual, Entry, Ledger, Posting, Rule, SettlementAllocation, SupplierInvoice, SupplierSettlement
from assets.models import Asset
from inventory.models import Item, Location, Movement, Unit, Warehouse
from purchasing.models import PurchaseOrder, Requisition, Supplier
from sales.models import Customer, Document, PostingEvent
from workorders.models import MaintenanceJob, MaintenancePlan, WorkOrder
from .models import Assignment, ModuleState, Role, Site


class ReportsTests(TestCase):
    def setUp(self):
        for module in ("accounting", "purchasing", "inventory", "sales", "assets", "workorders"):
            ModuleState.objects.create(module_id=module, enabled=True)
        self.site = Site.objects.create(name="Plant A")
        self.other = Site.objects.create(name="Plant B")
        user = get_user_model().objects.create_user(username="operator")
        role = Role.objects.create(name="Report reader", permissions=[
            "ap.view", "sales.view", "purchasing.view", "inventory.view", "maintenance.view", "workorders.view", "platform.export"])
        Assignment.objects.create(site=self.site, user=user, role=role)
        finance = Role.objects.create(name="Accounting", permissions=["accounting.view"])
        Assignment.objects.create(user=user, role=finance)
        self.client.force_login(user)
        self.user = user
        self.supplier = Supplier.objects.create(code="S1", name="=Vendor", currency_default="AED")
        self.customer = Customer.objects.create(code="C1", name="Buyer")
        self.ledger = Ledger.objects.get(pk=1)

    def result(self, url, query):
        response = self.client.get(url, query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def card(self, board, metric, query):
        return next(row for row in self.result("/api/dashboards/" + board, query)["cards"] if row["metric"] == metric)

    def order(self, site=None, delivery_due=None):
        site = site or self.site
        line = {"id": 1, "quantity": "3.000", "unit_price": "100.0000", "description": "Seal"}
        if delivery_due is not None:
            line["delivery_due"] = delivery_due
        pr = Requisition.objects.create(title="Seal", site=site, status="approved", currency="AED", lines=[], total=300)
        return PurchaseOrder.objects.create(source_pr=pr, site=site, supplier=self.supplier, status="issued",
            currency="AED", title="Seal", number=f"PO-{pr.pk}", lines=[line], total=300,
            snapshot={"supplier": {"name": self.supplier.name}, "lines": [line]})

    def sale(self, kind, amount, *, site=None, customer=None, posting=None, source=None, lines=None):
        doc = Document.objects.create(kind=kind, customer=customer or self.customer, site=site or self.site,
            date=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed", actor="operator",
            amount=amount, source=source, lines=lines or [])
        if posting:
            rule = Rule.objects.filter(ledger=self.ledger, kind="sales_invoice").first()
            entry = self.entry(posting, rule)
            Posting.objects.create(ledger=self.ledger, sale=PostingEvent.objects.create(document=doc), entry=entry, state="posted")
        return doc

    def entry(self, day, rule=None):
        return Entry.objects.create(ledger=self.ledger, kind="test", date=day,
                                    created_by="test", posted_at=timezone.now(), rule=rule)

    def test_drilldown_csv_and_permission_scope(self):
        self.order(delivery_due="2026-01-01")
        self.order(self.other, delivery_due="2026-01-01")
        self.order()
        filters = {"site": str(self.site.pk), "supplier": str(self.supplier.pk)}
        card = self.card("stock", "overdue-purchases", filters)
        path = "/api/reports/advanced/overdue-purchases"
        report = self.result(path, card["filters"])
        self.assertEqual(card, {key: report[key] for key in card})
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["items"][0]["quantity"], "3.000")
        response = self.client.get(path + "/export", card["filters"])
        records = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(records), report["count"])
        self.assertEqual(records[0]["party"], "'=Vendor")
        self.assertEqual(self.result(path, {**filters, "site": str(self.other.pk)})["count"], 0)
        self.assertEqual(self.card("stock", "undated-purchases", filters)["count"], 1)
        self.assertEqual(self.client.get(path, {**filters, "site": "bad"}).status_code, 400)

    def test_period_and_customer_filters_round_trip_and_save(self):
        self.sale("delivery", 100)
        other = Customer.objects.create(code="C2", name="Other")
        self.sale("delivery", 250, customer=other)
        self.sale("delivery", 500, site=self.other)
        self.sale("delivery", 700)
        Document.objects.filter(amount=700).update(date="2026-02-01")
        filters = {"site": str(self.site.pk), "customer": str(self.customer.pk),
                   "from": "2026-01-01", "to": "2026-01-31"}
        card = self.card("sales", "deliveries", filters)
        self.assertEqual(card["filters"], filters)
        path = "/api/reports/advanced/deliveries"
        report = self.result(path, card["filters"])
        self.assertEqual(card, {key: report[key] for key in card})
        self.assertEqual(report["amount"], "100.00")
        response = self.client.post("/api/saved-views/reports-sales",
            {"name": "January collections", "filters": filters, "is_default": True}, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["filters"], filters)

    def test_historical_ar_and_ap_use_posting_dates(self):
        inv = self.sale("invoice", 100, posting="2026-01-02")
        self.sale("receipt", 25, posting="2026-01-15", lines=[{"invoice_id": inv.pk, "amount": "25.00"}])
        self.sale("credit", 10, posting="2026-01-20", source=inv)
        self.sale("invoice", 500, site=self.other, posting="2026-01-02")
        ap_entry = self.entry("2026-01-02")
        ap = Account.objects.get(code="AP-CONTROL")
        ap_entry.lines.create(account=ap, site=self.site, amount=-100)
        row = SupplierInvoice.objects.create(order=self.order(), supplier=self.supplier, site=self.site,
            supplier_number="AP-1", supplier_number_key="ap-1", invoice_date="2026-01-01", due_date="2026-01-10",
            created_by=self.user, status="posted", declared_total=100, total=100, entry=ap_entry, draft_lines=[])
        pay = SupplierSettlement.objects.create(supplier=self.supplier, site=self.site, kind="payment",
            reference="PAY1", total=25, entry=self.entry("2026-01-15"))
        SettlementAllocation.objects.create(settlement=pay, invoice=row, amount=25)
        for day, ar, ap_total in (("2026-01-10", "100.00", "100.00"), ("2026-01-20", "65.00", "75.00")):
            common = {"site": str(self.site.pk), "as_of": day}
            self.assertEqual(self.card("finance", "ar-aging", common)["amount"], ar)
            self.assertEqual(self.card("finance", "ap-aging", common)["amount"], ap_total)
        self.assertEqual(self.card("finance", "ap-aging", {"as_of": "2026-01-20", "account": str(ap.pk)})["count"], 1)
        self.assertEqual(self.card("finance", "ap-aging", {"as_of": "2026-01-20", "account": "999"})["count"], 0)
        ModuleState.objects.update_or_create(pk="supplier_invoices", defaults={"enabled": True})
        path = "/api/workspace/dashboard/charts"
        filters = {"site": str(self.site.pk), "as_of": "2026-01-20", "from": "2026-01-01", "to": "2026-01-31"}
        widgets = self.result(path, filters)["widgets"]
        for metric, expected in (("ap-aging", "75.00"), ("ar-aging", "65.00")):
            aging = widgets[metric]["series"][0]
            self.assertEqual((aging["currency"], aging["total"]), ("AED", expected))
            point = next(point for point in aging["points"] if point["bucket"] == "1-30")
            self.assertEqual(point["value"], expected)
            report_path = "/api/reports/advanced/" + metric
            detail = self.result(report_path, point["filters"])
            self.assertEqual(detail["amount"], expected)
            self.assertEqual(detail["count"], 1)
            params = {**point["filters"], "bucket": "current"}
            csv_response = self.client.get(report_path + "/export", params)
            self.assertEqual(csv_response.status_code, 200)
            self.assertEqual(len(list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))), 0)
        self.assertNotIn("ar-aging", self.result(path, {**filters, "site": self.other.pk})["widgets"])


    def test_grni_history_and_ungenerated_pm_occurrences(self):
        unit, _ = Unit.objects.get_or_create(code="PC", defaults={"name": "Piece"})
        item = Item.objects.create(code="REPORT", name="Seal", base_unit=unit)
        loc = Location.objects.create(code="A1", name="Shelf", warehouse=Warehouse.objects.create(
            site=self.site, code="WH", name="Main"))
        move = Movement.objects.create(item=item, site=self.site, location=loc, quantity=1, value=100,
            kind="receipt", actor="test", actor_id=self.user.pk, occurred_at=timezone.now(), business_date="2026-01-01", facts={})
        Accrual.objects.create(ledger=self.ledger, receipt_movement=move, remaining=70,
            account=Account.objects.get(code="2100"), inventory_account=Account.objects.get(code="1200"), entry=self.entry("2026-01-02"))
        returned = Movement.objects.create(item=item, site=self.site, location=loc, quantity=-1, value=-30,
            kind="return", original=move, actor="test", actor_id=self.user.pk, occurred_at=timezone.now(),
            business_date="2026-01-11", facts={"original_receipt_value": "30.00"})
        Posting.objects.create(ledger=self.ledger, movement=returned, state="posted", entry=self.entry("2026-01-12"))
        self.assertEqual(self.card("finance", "grni", {"as_of": "2026-01-11"})["amount"], "100.00")
        self.assertEqual(self.card("finance", "grni", {"as_of": "2026-01-12"})["amount"], "70.00")
        asset = Asset.objects.create(tag="P-01", name="Pump", site=self.site)
        plan = MaintenancePlan.objects.create(asset=asset, title="Inspect", first_due="2026-01-01",
                                               frequency="months", interval=1, checklist=["Inspect"])
        order = WorkOrder.objects.create(title=plan.title, site=self.site)
        MaintenanceJob.objects.create(order=order, asset=asset, plan=plan, due_date="2026-01-01", stage="closed",
                                      completed_at="2026-01-01T12:00:00Z")
        result = self.result("/api/reports/advanced/pm-on-time", {"from": "2026-01-01", "to": "2026-02-28"})
        self.assertEqual((result["on_time"], result["count"]), (1, 2))
        self.assertEqual(result["items"][1]["status"], "not_generated")
