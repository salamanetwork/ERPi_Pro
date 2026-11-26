from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
from unittest.mock import patch

from django.apps import apps
from django.db import connections


from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.test import Client, TestCase, TransactionTestCase

from accounting.models import Line
from backbone.events import customer_created
from backbone.http import Problem
from backbone.models import ModuleState
from inventory.models import Item, Position, Valuation
from sales.models import Customer
from purchasing.models import PurchaseOrder, Requisition, Supplier
from supply.tests import SupplyFixture
from supply.models import Reservation

from .models import Bill, Material
from . import production


class ManufacturingTests(SupplyFixture, TestCase):
    def setUp(self):
        super().setUp()
        ModuleState.objects.create(module_id="manufacturing", enabled=True)
        self.finished = Item.objects.create(code="FINAL", name="Finished", base_unit=self.item.base_unit)
        self.component = Item.objects.create(code="SUB", name="Assembly", base_unit=self.item.base_unit)
        self.last = self.location(self.source, "D")
        self.out = self.location(self.source, "OUT")
        self.activate()

    def check_post(self, path, body, key="test", client=None, status=201):
        result = self.post("manufacturing/"+path,body,key,client)
        self.assertEqual(result.status_code,status,result.content)
        return result.json()

    def bill(self, product, part, amount="1", key="recipe"):
        row = self.check_post("boms",{"site_id":self.source.pk,"item_id":product.pk,"quantity":"1","effective":self.day,
                              "components":[{"item_id":part.pk,"quantity":amount}]},key)
        return self.check_post(f"boms/{row['id']}",{"action":"release","revision":row["revision"]},key)

    def new_order(self, amount, key="order"):
        return self.check_post("orders",{"site_id":self.source.pk,"item_id":self.finished.pk,"source_id":self.home.pk,
                           "destination_id":self.out.pk,"quantity":amount,"due":self.day},key)

    def action(self, row, operation, *, client=None, key=None, status=201, **fields):
        return self.check_post(f"orders/{row['id']}/{operation}",{"revision":row["revision"],**fields},
                               key or f"{row['id']}:{row['revision']}",client,status)

    def test_component_racks_and_return_use_original_movement(self):
        self.seed("10")
        stocked = self.post("inventory/opening", {"site_id": self.source.pk, "lines": [
            {"item_id": self.component.pk, "location_id": self.last.pk, "quantity": "30", "unit_cost": "2"},
        ]}, "nails-opening")
        self.assertEqual(stocked.status_code, 201, stocked.content)
        parts = [{"item_id": self.item.pk, "quantity": "2"},
                 {"item_id": self.component.pk, "quantity": "3", "source_id": self.last.pk}]
        bill_values = {"site_id": self.source.pk, "item_id": self.finished.pk, "quantity": "1",
                       "effective": self.day, "components": parts}
        wrong_parts = [{**parts[1], "source_id": self.target.pk}]
        invalid = self.check_post("boms", {**bill_values, "components": wrong_parts}, "wrong-rack", status=400)
        self.assertIn("components.0.source_id", invalid["field_errors"])
        bill = self.check_post("boms", bill_values)
        self.check_post(f"boms/{bill['id']}", {"action": "release", "revision": bill["revision"]})
        row = self.action(self.new_order("2"), "release")
        wood, nails = (next(line for line in row["materials"] if line["item"] == name)
                      for name in (self.item.name, self.component.name))
        self.assertEqual((wood["source_id"], nails["source_id"]), (self.home.pk, self.last.pk))
        self.assertEqual((Decimal(wood["reserved"]), Decimal(nails["reserved"])), (4, 6))
        row = self.action(row, "issue", material_id=nails["id"], quantity="3")
        issue = row["activities"][0]
        self.assertEqual(issue["source_id"], self.last.pk)
        row = self.action(row, "change-source", material_id=nails["id"], source_id=self.home.pk)
        returned = self.action(row, "return", original_id=issue["id"], quantity="2", reason="Unused")
        self.assertEqual(returned["activities"][-1]["source_id"], self.last.pk)
        self.assertEqual(Position.objects.get(location=self.last, item=self.component).quantity, 29)
        self.assertFalse(Position.objects.filter(location=self.home, item=self.component).exists())

    def test_change_source_moves_existing_hold_and_rolls_back_on_failure(self):
        self.seed("20")
        self.seed("3", self.last)
        self.bill(self.finished,self.item,"2")
        row = self.action(self.new_order("5"), "release")
        material_id = row["materials"][0]["id"]
        key = f"manufacturing.material:{material_id}"
        self.assertEqual(Reservation.objects.get(owner_key=key, location=self.home).quantity, 10)

        def fail_after_reserving(*args, **kwargs):
            production_reserve(*args, **kwargs)
            raise Problem("revision_conflict", 409)

        production_reserve = production.reserve
        with patch("manufacturing.production.reserve", side_effect=fail_after_reserving):
            self.action(row, "change-source", material_id=material_id, source_id=self.last.pk, status=409)
        self.assertEqual(Material.objects.get(pk=material_id).source_id, self.home.pk)
        self.assertEqual(Reservation.objects.get(owner_key=key, location=self.home).quantity, 10)
        self.assertFalse(Reservation.objects.filter(owner_key=key, location=self.last).exists())

        outsider = self.employee("other-site",self.dest,["manufacturing.view","manufacturing.prepare"])
        self.action(row, "change-source", client=outsider, material_id=material_id, source_id=self.last.pk, status=404)
        self.action(row, "change-source", material_id=material_id, source_id=self.target.pk, status=400)
        changed = self.action(row, "change-source", material_id=material_id, source_id=self.last.pk)
        self.assertEqual(Decimal(changed["materials"][0]["shortage"]), 7)
        self.assertEqual(Reservation.objects.get(owner_key=key, location=self.home).quantity, 0)
        self.assertEqual(Reservation.objects.get(owner_key=key, location=self.last).quantity, 3)
        replay = self.action(row, "change-source", material_id=material_id, source_id=self.last.pk, status=200)
        self.assertEqual(replay, changed)

    def test_costs_and_partial_batches(self):
        self.seed("20")
        bill = self.bill(self.finished,self.item,"2")
        row = self.action(self.new_order("5"),"release")
        self.assertEqual(row["bill_id"],bill["id"])
        material = row["materials"][0]["id"]
        self.assertEqual(Decimal(row["materials"][0]["reserved"]),10)
        row = self.action(row,"issue",material_id=material,quantity="10")
        initial_issue = row["activities"][0]["id"]
        row = self.action(row,"return",original_id=initial_issue,quantity="2",reason="Unused units")
        self.assertEqual(Decimal(row["cost"]),80)
        row = self.action(row,"receive",quantity="2")
        row = self.action(row,"receive",quantity="2")
        self.assertTrue(all(batch["held"] for batch in row["batches"]))
        self.assertFalse(Position.objects.filter(item=self.finished).exists())
        stale = row
        row = self.action(row,"close",reason="Stopped at four accepted units")
        replay = self.action(stale,"close",reason="Stopped at four accepted units",status=200)
        self.assertEqual(row,replay)
        self.assertEqual(row["state"],"closed")
        self.assertEqual(Valuation.objects.get(item=self.finished).value,80)
        self.assertEqual(Valuation.objects.get(item=self.item).value,120)
        self.assertEqual(sum(Line.objects.filter(account__code="INV-WIP").values_list("amount",flat=True)),0)
        self.assertEqual(sum(Line.objects.filter(entry__kind="mfg_output",account__code="1200").values_list("amount",flat=True)),80)

    def test_approval_and_failed_cross_order_withdrawal(self):
        self.seed("8")
        self.bill(self.finished,self.item,"2")
        first = self.action(self.new_order("3","first"),"release")
        second = self.action(self.new_order("2","second"),"release")
        self.assertEqual(Decimal(second["materials"][0]["shortage"]),2)
        material = second["materials"][0]["id"]
        failure = self.action(second,"issue",status=409,material_id=material,quantity="4")
        self.assertEqual(failure["error"],"supply_insufficient_available")
        second = self.action(second,"issue",material_id=material,quantity="2")
        second = self.action(second,"report",original_id=second["activities"][0]["id"],kind="scrap",
                             quantity="1",reason="Destroyed on setup")
        args={"exception_id":second["exceptions"][0]["id"],"decision":"approve","reason":"Checked scrap"}
        self.assertEqual(self.action(second,"decide",status=403,**args)["error"],"mfg_separate_reviewer")
        manager = self.employee("supervisor",self.source,["manufacturing.approve","manufacturing.view"])
        self.action(second,"decide",client=manager,**args)
        self.assertEqual(Line.objects.get(account__code="MFG-VARIANCE").amount,10)
        self.assertEqual(Decimal(first["materials"][0]["reserved"]),6)

    def test_mrp_nets_intermediate_stock_before_exploding(self):
        self.bill(self.finished,self.component,"2","assembly")
        self.bill(self.component,self.item,"3","raw")
        result = self.post("inventory/opening",{"site_id":self.source.pk,"lines":[
            {"item_id":self.component.pk,"location_id":self.home.pk,"quantity":"1","unit_cost":"10"}]},"assembly-opening")
        self.assertEqual(result.status_code,201,result.content)
        manual = [{"item_id":self.finished.pk,"date":self.day,"quantity":"2"}]
        plan = self.check_post("plans",{"site_id":self.source.pk,"requirements":manual})
        items = {row["item_id"]: (row["method"],Decimal(row["quantity"])) for row in plan["proposals"]}
        self.assertEqual(items,{self.finished.pk:("make",2),self.component.pk:("make",3),self.item.pk:("buy",9)})
        root = next(row for row in plan["proposals"] if row["item_id"]==self.finished.pk)
        path = f"proposals/{root['id']}/convert"
        values = {"source_id":self.home.pk,"destination_id":self.out.pk}
        created = self.check_post(path,values,"convert")
        self.assertRegex(created["target"],r"^/manufacturing/orders/\d+$")
        self.assertEqual(self.check_post(path,values,"convert",status=200),created)
        self.assertEqual(self.check_post(path,values,"retry"),created)

    def test_mrp_inbound_date_does_not_cover_prior_shortage(self):
        self.seed("3")
        self.client.post("/api/inventory/opening",{"site_id":self.dest.pk,"lines":[
            {"item_id":self.item.pk,"location_id":self.target.pk,"quantity":"10","unit_cost":"10"}]},
            content_type="application/json",HTTP_IDEMPOTENCY_KEY="ship_stock")
        response = self.post("supply/transfers",{"site_id":self.dest.pk,"destination_id":self.source.pk,
                     "lines":[{"item_id":self.item.pk,"source_id":self.target.pk,"destination_id":self.home.pk,"quantity":"5"}]},
                     "transfer")
        self.assertEqual(response.status_code,201,response.content)
        transfer = response.json()
        path=f"supply/transfers/{transfer['id']}/"
        confirm=self.post(path+"confirm",{"revision":transfer["revision"]},"confirm")
        self.assertEqual(confirm.status_code,201,confirm.content)
        eta=(self.today+timedelta(days=2)).isoformat()
        ship=self.post(path+"dispatch",{"revision":confirm.json()["revision"],"eta":eta,
                      "lines":[{"line_id":transfer["lines"][0]["id"],"quantity":"5"}]},"dispatch")
        self.assertEqual(ship.status_code,201,ship.content)
        plan = self.check_post("plans",{"site_id":self.source.pk,"requirements":[
            {"item_id":self.item.pk,"date":self.day,"quantity":"10"}]})
        self.assertEqual(len(plan["proposals"]),1)
        self.assertEqual(Decimal(plan["proposals"][0]["quantity"]),7)
        self.assertEqual(plan["proposals"][0]["facts"]["next_supply"],eta)

    def test_mrp_maps_unreceived_purchase_line_and_creates_buy_draft(self):
        supplier = Supplier.objects.create(code="MFG-S",name="Vendor",currency_default="AED")
        pr = Requisition.objects.create(title="Already ordered",site=self.source,currency="AED",lines=[],total=80)
        po = PurchaseOrder.objects.create(number="PO-MFG",source_pr=pr,site=self.source,supplier=supplier,
             title=pr.title,currency="AED",lines=[{"id":1,"quantity":"8.000","description":"Liner"}],total=80,status="issued")
        manual = [{"item_id":self.item.pk,"quantity":"10","date":self.day}]
        data = {"site_id":self.source.pk,"requirements":manual,"purchase_dates":{str(po.pk):self.day}}
        before = self.check_post("plans",data,"unmapped")
        self.assertEqual(before["inputs"]["excluded"]["unmapped_purchase_lines"],1)
        self.assertEqual(Decimal(before["proposals"][0]["quantity"]),10)
        data["purchase_mappings"] = [{"order_id":po.pk,"line_id":1,"item_id":self.item.pk}]
        after = self.check_post("plans",data,"mapped")
        result = after["proposals"][0]
        self.assertEqual(Decimal(result["quantity"]),2)
        draft = self.check_post(f"proposals/{result['id']}/convert",{"unit_price":"10"},"buy")
        self.assertRegex(draft["target"],r"^/purchase-requests/\d+$")
        self.assertEqual(Requisition.objects.exclude(pk=pr.pk).get().total,20)

    def test_mrp_transfer_draft_checks_source_stock(self):
        stocked = self.post("inventory/opening",{"site_id":self.dest.pk,"lines":[
            {"item_id":self.item.pk,"location_id":self.target.pk,"quantity":"10","unit_cost":"10"}]},"remote")
        self.assertEqual(stocked.status_code,201,stocked.content)
        self.check_post("policies",{"site_id":self.source.pk,"item_id":self.item.pk,"method":"transfer","lead_days":2,
                                   "source_id":self.target.pk,"destination_id":self.home.pk},status=200)
        plan = self.check_post("plans",{"site_id":self.source.pk,"requirements":[
            {"item_id":self.item.pk,"date":self.day,"quantity":"5"}]})
        proposal = plan["proposals"][0]
        self.assertEqual(proposal["method"],"transfer")
        self.assertTrue(proposal["facts"]["late"])
        draft = self.check_post(f"proposals/{proposal['id']}/convert",{},"transfer")
        self.assertRegex(draft["target"],r"^/supply/transfers/\d+$")

    def test_bom_cycle_and_site_permissions(self):
        self.bill(self.finished,self.component)
        row = self.check_post("boms",{"site_id":self.source.pk,"item_id":self.component.pk,"quantity":"1",
               "effective":self.day,"components":[{"item_id":self.finished.pk,"quantity":"1"}]},"cycle")
        result = self.check_post(f"boms/{row['id']}",{"revision":row["revision"],"action":"release"},"cycle",status=409)
        self.assertEqual(result["error"],"mfg_bom_cycle")
        self.assertEqual(Bill.objects.get(pk=row["id"]).state,"draft")
        viewer = self.employee("outside-site",self.dest,["manufacturing.view"])
        self.assertEqual(viewer.get(f"/api/manufacturing/boms/{row['id']}").status_code,404)

    def test_customer_event_only_after_commit(self):
        deliveries = []
        def receiver(sender,customer_id,**kwargs):
            deliveries.append(customer_id)
        customer_created.connect(receiver)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                row = Customer.objects.create(code="EXT001",name="Test event")
                self.assertFalse(deliveries)
                with transaction.atomic():
                    Customer.objects.create(code="EXT002",name="Rolled back")
                    transaction.set_rollback(True)
            self.assertEqual(deliveries,[row.pk])
        finally:
            customer_created.disconnect(receiver)

    def test_audit_deleted_item_location_and_inactive_site_guards(self):
        from backbone.models import Site
        from inventory.models import Location

        del_item = Item.objects.create(
            code="DEL-PART", name="Deleted Part", base_unit=self.item.base_unit, active=True, is_deleted=True
        )
        res_bom = self.post(
            "manufacturing/boms",
            {
                "site_id": self.source.pk,
                "item_id": self.finished.pk,
                "quantity": "1",
                "effective": self.day,
                "components": [{"item_id": del_item.pk, "quantity": "1"}],
            },
            "audit-del-item",
        )
        self.assertEqual(res_bom.status_code, 404)

        # Inactive location rejected in BOM component source_id
        Location.objects.filter(pk=self.last.pk).update(active=False)
        try:
            res_loc = self.post(
                "manufacturing/boms",
                {
                    "site_id": self.source.pk,
                    "item_id": self.finished.pk,
                    "quantity": "1",
                    "effective": self.day,
                    "components": [{"item_id": self.component.pk, "quantity": "1", "source_id": self.last.pk}],
                },
                "audit-inactive-loc",
            )
            self.assertEqual(res_loc.status_code, 400)
            self.assertEqual(res_loc.json()["error"], "invalid_source_id")
        finally:
            Location.objects.filter(pk=self.last.pk).update(active=True)

        # Inactive site rejected on BOM and production order creation
        Site.objects.filter(pk=self.source.pk).update(active=False)
        try:
            res_site_bom = self.post(
                "manufacturing/boms",
                {
                    "site_id": self.source.pk,
                    "item_id": self.finished.pk,
                    "quantity": "1",
                    "effective": self.day,
                    "components": [{"item_id": self.component.pk, "quantity": "1"}],
                },
                "audit-inactive-site-bom",
            )
            self.assertIn(res_site_bom.status_code, (403, 409))

            res_site_ord = self.post(
                "manufacturing/orders",
                {
                    "site_id": self.source.pk,
                    "item_id": self.finished.pk,
                    "source_id": self.home.pk,
                    "destination_id": self.out.pk,
                    "quantity": "1",
                    "due": self.day,
                },
                "audit-inactive-site-ord",
            )
            self.assertIn(res_site_ord.status_code, (403, 409))
        finally:
            Site.objects.filter(pk=self.source.pk).update(active=True)


class ManufacturingConcurrencyTests(SupplyFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()

    def test_two_production_orders_compete_for_same_available_stock(self):
        with connections["default"].schema_editor() as editor:
            for version in ("0002_default_ledger","0012_manufacturing_accounts"):
                import_module("accounting.migrations."+version).seed(apps,editor)
        ModuleState.objects.create(module_id="manufacturing",enabled=True)
        self.activate()
        finished = Item.objects.create(code="RACE-FIN",name="Race finished",base_unit=self.item.base_unit)
        output = self.location(self.source,"RACE")
        def call(path,data,key,client=None):
            return self.post("manufacturing/"+path,data,key,client)
        bill = call("boms",{"site_id":self.source.pk,"item_id":finished.pk,"quantity":"1","effective":self.day,
                    "components":[{"item_id":self.item.pk,"quantity":"1"}]},"bill").json()
        self.assertEqual(call(f"boms/{bill['id']}",{"action":"release","revision":bill["revision"]},"release-bill").status_code,201)
        released=[]
        for index in range(2):
            draft=call("orders",{"site_id":self.source.pk,"item_id":finished.pk,"source_id":self.home.pk,
                       "destination_id":output.pk,"quantity":"1","due":self.day},f"draft-{index}").json()
            status=call(f"orders/{draft['id']}/release",{"revision":draft["revision"]},f"release-{index}")
            self.assertEqual(status.status_code,201,status.content)
            released.append(status.json())
        self.seed("1")
        gate=Barrier(2,timeout=10)

        def run(row):
            client=Client()
            try:
                self.login_admin(client,self.admin)
                gate.wait()
                return call(f"orders/{row['id']}/issue",{"revision":row["revision"],"quantity":"1",
                            "material_id":row["materials"][0]["id"]},f"issue-{row['id']}",client).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(run,released)),[201,409])
        self.assertEqual(Position.objects.get(item=self.item,location=self.home).quantity,0)
        self.assertEqual(Line.objects.filter(entry__kind="mfg_issue",account__code="INV-WIP").count(),1)
