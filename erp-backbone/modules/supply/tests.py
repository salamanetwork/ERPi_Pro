from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from accounting.models import Posting
from backbone.mfa import VERIFIED, stamp
from backbone.models import Authenticator, Assignment, ModuleState, Role, Site
from inventory.models import Item, Location, Movement, Position, Unit, Warehouse
from sales.models import Customer, Document
from supply.models import Reservation


class SupplyFixture:
    def setUp(self):
        store = get_user_model().objects
        self.admin = store.create_superuser(username="admin")
        self.source = Site.objects.create(name="Dispatch")
        self.dest = Site.objects.create(name="Receive")
        self.outside = Site.objects.create(name="Outside")
        self.login_admin(self.client, self.admin)
        for module in ("inventory", "supply", "accounting", "sales", "customers", "purchasing",
                       "purchase_orders", "suppliers"):
            ModuleState.objects.update_or_create(module_id=module, defaults={"enabled": True})
        unit, _ = Unit.objects.get_or_create(code="EA", defaults={"name": "Each"})
        self.item = Item.objects.create(code="TEST-LINER", name="Liner", base_unit=unit)
        self.home = self.location(self.source, "A")
        self.target = self.location(self.dest, "B")
        self.today = timezone.localdate()
        self.day = self.today.isoformat()

    def login_admin(self, client, user):
        client.force_login(user)
        device, _ = Authenticator.objects.get_or_create(user=user, defaults={"encrypted_key":"test-session-only"})
        session = client.session
        session[VERIFIED] = stamp(device)
        session.save()

    def location(self, site, code):
        wh, _ = Warehouse.objects.get_or_create(site=site, code="MAIN", defaults={"name": "Main"})
        return Location.objects.create(warehouse=wh, code=code, name=code)

    def post(self, path, data, key="request", client=None):
        return (client or self.client).post("/api/" + path, data, content_type="application/json",
                                           HTTP_IDEMPOTENCY_KEY=key)

    def employee(self, name, site, permissions, *, accountant=False):
        user = get_user_model().objects.create_user(username=name)
        Assignment.objects.create(user=user, site=site, role=Role.objects.create(name=name, permissions=permissions))
        if accountant:
            Assignment.objects.create(user=user, role=Role.objects.create(name=name+" finance",
                                      permissions=["accounting.view", "accounting.post"]))
        client = Client()
        client.force_login(user)
        return client

    def seed(self, amount="100", location=None):
        response = self.post("inventory/opening", {"site_id": self.source.pk, "lines": [{
            "item_id": self.item.pk, "location_id": (location or self.home).pk, "quantity": amount, "unit_cost":"10"}]},
            f"stock-{(location or self.home).pk}")
        self.assertEqual(response.status_code, 201, response.content)

    def activate(self):
        period = self.post("accounting/periods", {"start":self.today.replace(day=1).isoformat()})
        self.assertEqual(period.status_code, 201, period.content)
        response = self.post("accounting/cutover", {"cutoff":self.day})
        self.assertEqual(response.status_code, 200, response.content)
        response = self.post("accounting/cutover", {"cutoff":self.day,"token":response.json()["token"]})
        self.assertEqual(response.status_code, 201, response.content)
        return period.json()["id"]

    def new(self, amount, key="new"):
        response = self.post("supply/transfers", {"site_id":self.source.pk, "destination_id":self.dest.pk, "lines":[{
            "item_id":self.item.pk, "source_id":self.home.pk, "destination_id":self.target.pk, "quantity":amount}]}, key)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def action(self, row, operation, client=None, key=None, **data):
        return self.post(f"supply/transfers/{row['id']}/{operation}", {"revision":row["revision"], **data},
                         key or f"{operation}-{row['revision']}-{row['id']}", client)

    def done(self, row, operation, **values):
        response = self.action(row, operation, **values)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def reconciliation(self):
        response = self.client.get("/api/supply/reconciliation")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


class SupplyTests(SupplyFixture, TestCase):
    def test_dispatch_receipt_return_discrepancy_and_ledger(self):
        self.activate()
        self.seed()
        source_order = self.done(self.new("30"), "confirm")
        sent = self.done(source_order, "dispatch", lines=[{"line_id":source_order["lines"][0]["id"],"quantity":"20"}],
                         carrier="Truck 12", reference="REF-12", eta=self.day)
        replay = self.action(source_order, "dispatch", lines=[{"line_id":source_order["lines"][0]["id"],"quantity":"20"}],
                             carrier="Truck 12", reference="REF-12", eta=self.day)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(Movement.objects.filter(kind="transfer_out").count(), 1)
        balance = self.client.get("/api/inventory/balances").json()["items"][0]
        self.assertEqual([Decimal(balance[key]) for key in ("on_hand","reserved","available")], [80,10,70])
        self.assertEqual(self.reconciliation()["posted_value"], "200.00")
        ship = sent["shipments"][0]
        shipment_line = ship["lines"][0]["id"]
        customer = self.employee("other", self.outside, ["supply.view"])
        self.assertEqual(customer.get(f"/api/supply/transfers/{sent['id']}").status_code, 404)
        dock = self.employee("dock", self.dest, ["supply.view","supply.receive","supply.settle"], accountant=True)
        finance = self.employee("finance", self.dest, ["supply.view","supply.settle"], accountant=True)
        arrived = self.done(sent, "receive", client=dock, shipment_id=ship["id"],
                            lines=[{"line_id":shipment_line, "quantity":"12"}])
        returned = self.done(arrived, "return", shipment_id=ship["id"], reason="Returned sealed cartons",
                             lines=[{"line_id":shipment_line,"quantity":"2"}])
        reported = self.done(returned, "report", client=dock, shipment_line_id=shipment_line, quantity="6",
                             kind="damage", reason="Forklift damage")
        blocked = self.action(reported, "receive", client=dock, shipment_id=ship["id"],
                              lines=[{"line_id":shipment_line,"quantity":"1"}])
        self.assertEqual(blocked.status_code, 409, blocked.content)
        diff = reported["shipments"][0]["lines"][0]["discrepancies"][0]["id"]
        args = {"discrepancy_id":diff, "decision":"approve", "reason":"Verified count and photos"}
        blocked = self.action(reported, "decide", client=dock, **args)
        self.assertEqual(blocked.status_code, 403, blocked.content)
        self.assertEqual(self.reconciliation()["posted_value"], "60.00")
        closed = self.done(reported, "decide", client=finance, **args)
        self.assertEqual(self.action(reported, "decide", client=finance, key="stale", **args).status_code, 409)
        result = self.done(closed, "cancel", reason="No further shipment")
        self.assertEqual(result["state"], "received")
        self.assertEqual(self.reconciliation()["operational_value"], "0.00")
        self.assertEqual(self.reconciliation()["difference"], "0.00")
        amounts = Posting.objects.get(movement__kind="transfer_loss").entry.lines
        self.assertEqual({(code, site):amount for code,site,amount in amounts.values_list("account__code","site_id","amount")},
                         {("TRANSFER-LOSS",self.dest.pk):Decimal("60"),("INV-TRANSIT",self.source.pk):Decimal("-60")})
        self.assertEqual(Position.objects.get(location=self.target).quantity, 12)
        self.assertEqual(Position.objects.get(location=self.home).quantity, 82)

    def test_split_shipment_and_posting_queue(self):
        period = self.activate()
        self.seed("10")
        response = self.post(f"accounting/periods/{period}/close", {})
        self.assertEqual(response.status_code, 200, response.content)
        row = self.done(self.new("10"), "confirm")
        key = row["lines"][0]["id"]
        for amount in ("4", "6"):
            row = self.done(row, "dispatch", lines=[{"line_id":key,"quantity":amount}], carrier="Line Haul")
        matches = self.client.get("/api/supply/shipments?q=haul&state=in_transit").json()
        self.assertEqual(matches["count"], 2)
        for ship in row["shipments"]:
            line = ship["lines"][0]
            row = self.done(row, "receive", shipment_id=ship["id"],
                            lines=[{"line_id":line["id"],"quantity":line["quantity"]}])
        self.assertEqual(row["state"], "received")
        self.assertEqual(self.reconciliation()["pending_count"], 4)
        self.assertEqual(self.client.get("/api/supply/shipments?state=received").json()["count"], 2)
        later = (self.today.replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()
        self.assertEqual(self.post("accounting/periods", {"start":later}).status_code, 201)
        postings = list(Posting.objects.filter(movement__kind__startswith="transfer_").order_by("movement_id"))
        def retry(posting):
            return self.post(f"accounting/postings/{posting.pk}/retry", {"date":later, "reason":"Closed month"})
        self.assertEqual(retry(postings[-1]).json()["error"], "finance_original_not_posted")
        for posting in postings:
            response = retry(posting)
            self.assertEqual(response.json()["state"], "posted", response.content)
        self.assertEqual(self.reconciliation()["pending_count"], 0)
        self.assertEqual(self.reconciliation()["difference"], "0.00")
        self.assertEqual(Position.objects.get(location=self.target).quantity, 10)

    def test_open_shipment_moves_into_cutover_and_clears_on_receipt(self):
        self.seed("10")
        row = self.done(self.new("4"), "confirm")
        row = self.done(row, "dispatch", lines=[{"line_id":row["lines"][0]["id"],"quantity":"4"}])
        Movement.objects.update(business_date=self.today-timedelta(days=1))
        self.activate()
        self.assertEqual(self.reconciliation()["posted_value"], "40.00")
        shipment = row["shipments"][0]
        self.done(row, "receive", shipment_id=shipment["id"],
                  lines=[{"line_id":shipment["lines"][0]["id"],"quantity":"4"}])
        self.assertEqual(self.reconciliation()["posted_value"], "0.00")
        self.assertEqual(self.reconciliation()["difference"], "0.00")

    def test_sales_holds_and_delivery_from_another_location(self):
        self.activate()
        self.seed("10")
        alternate = self.location(self.source, "C")
        self.seed("5", alternate)
        customer = Customer.objects.create(code="TEST", name="Customer")
        def order():
            return Document.objects.create(kind="order", status="confirmed", site=self.source, customer=customer,
                date=self.today, lines=[{"id":1, "item_id":self.item.pk, "name":self.item.name, "quantity":"10", "amount":"100"}],
                amount=100)
        first, second = order(), order()
        response = self.post(f"supply/sales/{first.pk}/reserve", {"line_id":1,"location_id":self.home.pk,"quantity":"10"})
        self.assertEqual(response.status_code, 201, response.content)
        bad = self.post(f"supply/sales/{second.pk}/reserve", {"line_id":1,"location_id":self.home.pk,"quantity":"1"})
        self.assertEqual(bad.status_code, 409, bad.content)
        def deliver(row, location, key):
            return self.post("sales/documents", {"kind":"delivery","source_id":row.pk, "date":self.day,
                "lines":[{"line_id":1, "location_id":location.pk, "quantity":"3"}]}, key)
        self.assertEqual(deliver(second, self.home, "blocked").status_code, 409)
        shipped = deliver(first, alternate, "first")
        self.assertEqual(shipped.status_code, 201, shipped.content)
        self.assertEqual(Reservation.objects.get(order=first).quantity, 7)
        self.assertEqual(deliver(first, self.home, "first-again").status_code, 201)
        self.assertEqual(Reservation.objects.get(order=first).quantity, 4)


class SupplyConcurrencyTests(SupplyFixture, TransactionTestCase):
    def test_simultaneous_confirmation_only_reserves_once(self):
        self.seed("10")
        first, second = self.new("8", "one"), self.new("8", "two")
        start = Barrier(2, timeout=10)

        def run(row):
            client = Client()
            try:
                self.login_admin(client, get_user_model().objects.get(pk=self.admin.pk))
                start.wait()
                return self.action(row, "confirm", client=client).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(run, (first, second)))
        self.assertEqual(sorted(outcomes), [201, 409])
        self.assertEqual(Reservation.objects.get().quantity, 8)
