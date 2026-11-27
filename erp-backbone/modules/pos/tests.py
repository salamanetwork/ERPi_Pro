import json
from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounting.models import Account, Entry, Ledger, Period
from backbone.models import Assignment, ModuleState, Role, Site
from inventory.models import Item, Location, Movement, Position, Unit, Warehouse
from parties.models import Party
from pos.models import PosCategory, PosPayment, PosSale, PosSaleLine, PosShift, PosTerminal
from pos.services import checkout_sale, close_shift, compute_shift_report, open_shift, void_or_refund_sale
from backbone.http import Problem

User = get_user_model()


class PosModuleTestSuite(TestCase):
    def setUp(self):
        ModuleState.objects.update_or_create(module_id="pos", defaults={"enabled": True})
        self.site = Site.objects.create(name="Dubai Retail Branch")
        self.user = User.objects.create_user(username="pos_admin", password="StrongPassword123!", email="pos@example.com")
        role = Role.objects.create(
            name="POS Manager",
            permissions=["pos.view", "pos.admin", "pos.open_shift", "pos.close_shift", "pos.reports", "pos.operate", "pos.refund"],
        )
        Assignment.objects.create(user=self.user, site=self.site, role=role)
        self.client.force_login(self.user)
        self.ledger, _ = Ledger.objects.get_or_create(pk=1)
        today = timezone.localdate()
        self.period, _ = Period.objects.get_or_create(
            ledger=self.ledger,
            start=today.replace(day=1),
            end=(today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1),
        )

        self.cash_acc, _ = Account.objects.get_or_create(ledger=self.ledger, code="1019", defaults={"name": "POS Cash Drawer", "kind": "asset", "detail": True, "active": True})
        self.card_acc, _ = Account.objects.get_or_create(ledger=self.ledger, code="1029", defaults={"name": "POS Card Clearing", "kind": "asset", "detail": True, "active": True})
        self.inv_acc, _ = Account.objects.get_or_create(ledger=self.ledger, code="1309", defaults={"name": "Retail Inventory", "kind": "asset", "detail": True, "active": True})
        self.tax_acc, _ = Account.objects.get_or_create(ledger=self.ledger, code="2109", defaults={"name": "VAT Payable", "kind": "liability", "detail": True, "active": True})
        self.rev_acc, _ = Account.objects.get_or_create(ledger=self.ledger, code="4019", defaults={"name": "POS Sales Revenue", "kind": "revenue", "detail": True, "active": True})
        self.cogs_acc, _ = Account.objects.get_or_create(ledger=self.ledger, code="5019", defaults={"name": "POS COGS", "kind": "expense", "detail": True, "active": True})
        self.overshort_acc, _ = Account.objects.get_or_create(ledger=self.ledger, code="5099", defaults={"name": "Cash Over/Short", "kind": "expense", "detail": True, "active": True})

        self.wh = Warehouse.objects.create(site=self.site, code="WH-POS", name="POS Storefront")
        self.loc = Location.objects.create(warehouse=self.wh, code="LOC-MAIN", name="Shelf A")
        self.unit = Unit.objects.create(code="PCS", name="Pieces")

        self.item1 = Item.objects.create(code="ITM-001", barcode="6291000000011", name="Arabic Coffee 250g", base_unit=self.unit, default_price=Decimal("50.00"))
        self.item2 = Item.objects.create(code="ITM-002", barcode="6291000000028", name="Cardamom Pods 100g", base_unit=self.unit, default_price=Decimal("25.00"))
        Position.objects.create(site=self.site, item=self.item1, location=self.loc, quantity=Decimal("10"))
        Position.objects.create(site=self.site, item=self.item2, location=self.loc, quantity=Decimal("5"))

        self.terminal = PosTerminal.objects.create(
            site=self.site,
            code="POS-01",
            name="Counter 1",
            cash_account=self.cash_acc,
            card_account=self.card_acc,
            over_short_account=self.overshort_acc,
            revenue_account=self.rev_acc,
            tax_account=self.tax_acc,
            cogs_account=self.cogs_acc,
            inventory_account=self.inv_acc,
            warehouse=self.wh,
            location=self.loc,
            tax_rate=Decimal("0.0000"),
            is_active=True,
        )

    def test_01_open_shift_success(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("200.00"))
        self.assertEqual(shift.status, PosShift.STATUS_OPEN)
        self.assertEqual(shift.opening_balance, Decimal("200.00"))

    def test_02_prevent_duplicate_open_shift_on_same_terminal(self):
        open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        with self.assertRaises(Problem) as ctx:
            open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        self.assertEqual(ctx.exception.code, "pos_shift_already_open")

    def test_03_reject_negative_opening_balance(self):
        with self.assertRaises(Problem) as ctx:
            open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("-10.00"))
        self.assertEqual(ctx.exception.code, "pos_invalid_opening_balance")

    def test_04_inactive_terminal_cannot_open_shift(self):
        self.terminal.is_active = False
        self.terminal.save()
        with self.assertRaises(Problem) as ctx:
            open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("50.00"))
        self.assertEqual(ctx.exception.code, "pos_terminal_inactive")

    def test_05_checkout_cash_sale_creates_document_and_gl(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"barcode": "6291000000011", "quantity": "2"}],
            payments_data=[{"method": "cash", "amount": "100.00"}],
        )
        self.assertEqual(sale.total, Decimal("100.00"))
        self.assertIsNotNone(sale.sales_document)
        self.assertEqual(sale.sales_document.kind, "receipt")
        self.assertEqual(sale.sales_document.snapshot["doc_type"], "POS_RECEIPT")
        self.assertIsNotNone(sale.gl_entry)
        self.assertEqual(sum(ln.amount for ln in sale.gl_entry.lines.all()), Decimal("0.00"))

    def test_06_split_payment_cash_and_card_exact(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "2"}],
            payments_data=[
                {"method": "cash", "amount": "50.00"},
                {"method": "card", "amount": "50.00", "reference": "AUTH-991"},
            ],
        )
        self.assertEqual(sale.total, Decimal("100.00"))
        self.assertEqual(sale.payments.count(), 2)
        cash_line = sale.gl_entry.lines.get(account=self.cash_acc)
        card_line = sale.gl_entry.lines.get(account=self.card_acc)
        self.assertEqual(cash_line.amount, Decimal("50.00"))
        self.assertEqual(card_line.amount, Decimal("50.00"))

    def test_07_reject_insufficient_payment(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        with self.assertRaises(Problem) as ctx:
            checkout_sale(
                shift=shift,
                cashier=self.user,
                lines_data=[{"item_id": self.item1.id, "quantity": "2"}],
                payments_data=[{"method": "cash", "amount": "80.00"}],
            )
        self.assertEqual(ctx.exception.code, "pos_insufficient_payment")

    def test_08_prevent_negative_stock_on_checkout(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        with self.assertRaises(Problem) as ctx:
            checkout_sale(
                shift=shift,
                cashier=self.user,
                lines_data=[{"item_id": self.item2.id, "quantity": "6"}],
                payments_data=[{"method": "cash", "amount": "150.00"}],
            )
        self.assertEqual(ctx.exception.code, "pos_insufficient_stock")
        self.assertEqual(Position.objects.get(item=self.item2, location=self.loc).quantity, Decimal("5"))

    def test_09_stock_deduction_and_movement_created(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "3"}],
            payments_data=[{"method": "cash", "amount": "150.00"}],
        )
        self.assertEqual(Position.objects.get(item=self.item1, location=self.loc).quantity, Decimal("7"))
        self.assertTrue(Movement.objects.filter(item=self.item1, kind="pos_sale", quantity=Decimal("-3")).exists())

    def test_10_idempotency_key_returns_same_sale_without_double_deduction(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        s1 = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
            idempotency_key="pos:sale:uuid-12345",
        )
        s2 = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
            idempotency_key="pos:sale:uuid-12345",
        )
        self.assertEqual(s1.pk, s2.pk)
        self.assertEqual(Position.objects.get(item=self.item1, location=self.loc).quantity, Decimal("9"))

    def test_11_walkin_customer_created_automatically(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item2.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "25.00"}],
        )
        self.assertIsNotNone(sale.customer)
        self.assertTrue(sale.sales_document.customer.code.startswith("WALKIN-"))

    def test_12_named_customer_linked_when_provided(self):
        party = Party.objects.create(site=self.site, party_type="customer", name_en="Ahmed Corp", name_ar="شركة أحمد")
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            customer_party=party,
            lines_data=[{"item_id": self.item2.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "25.00"}],
        )
        self.assertEqual(sale.customer_id, party.id)

    def test_13_void_before_shift_close_reverses_gl_and_restores_stock(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "2"}],
            payments_data=[{"method": "cash", "amount": "100.00"}],
        )
        voided = void_or_refund_sale(sale=sale, actor_user=self.user, reason="Customer changed mind")
        self.assertEqual(voided.payment_status, PosSale.STATUS_VOID)
        self.assertIsNone(voided.refund_document)
        self.assertEqual(voided.sales_document.status, "void")
        self.assertIsNotNone(voided.reversal_entry)
        self.assertEqual(sum(ln.amount for ln in voided.reversal_entry.lines.all()), Decimal("0.00"))
        self.assertEqual(Position.objects.get(item=self.item1, location=self.loc).quantity, Decimal("10"))

    def test_14_refund_after_shift_close_creates_credit_note(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
        )
        close_shift(shift=shift, closing_balance=Decimal("150.00"), actor="pos_admin")
        refunded = void_or_refund_sale(sale=sale, actor_user=self.user, reason="Defective package")
        self.assertEqual(refunded.payment_status, PosSale.STATUS_REFUNDED)
        self.assertIsNotNone(refunded.refund_document)
        self.assertEqual(refunded.refund_document.kind, "credit")
        self.assertEqual(refunded.refund_document.source_id, refunded.sales_document_id)

    def test_15_void_or_refund_requires_reason(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
        )
        with self.assertRaises(Problem) as ctx:
            void_or_refund_sale(sale=sale, actor_user=self.user, reason="")
        self.assertEqual(ctx.exception.code, "pos_refund_reason_required")

    def test_16_cannot_double_void_sale(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
        )
        void_or_refund_sale(sale=sale, actor_user=self.user, reason="Mistake")
        with self.assertRaises(Problem) as ctx:
            void_or_refund_sale(sale=sale, actor_user=self.user, reason="Again")
        self.assertEqual(ctx.exception.code, "pos_sale_already_reversed")

    def test_17_shift_close_exact_reconciliation_zero_variance(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "2"}],
            payments_data=[{"method": "cash", "amount": "60.00"}, {"method": "card", "amount": "40.00"}],
        )
        closed = close_shift(shift=shift, closing_balance=Decimal("160.00"), actor="pos_admin")
        self.assertEqual(closed.expected_closing, Decimal("160.00"))
        self.assertEqual(closed.cash_variance, Decimal("0.00"))

    def test_18_shift_close_cash_short_posts_gl_variance(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
        )
        closed = close_shift(shift=shift, closing_balance=Decimal("145.00"), actor="pos_admin")
        self.assertEqual(closed.cash_variance, Decimal("-5.00"))
        entry = Entry.objects.filter(kind="pos_shift_close").latest("id")
        self.assertEqual(entry.lines.get(account=self.overshort_acc).amount, Decimal("5.00"))
        self.assertEqual(entry.lines.get(account=self.cash_acc).amount, Decimal("-5.00"))

    def test_19_shift_close_cash_over_posts_gl_variance(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
        )
        closed = close_shift(shift=shift, closing_balance=Decimal("153.50"), actor="pos_admin")
        self.assertEqual(closed.cash_variance, Decimal("3.50"))
        entry = Entry.objects.filter(kind="pos_shift_close").latest("id")
        self.assertEqual(entry.lines.get(account=self.cash_acc).amount, Decimal("3.50"))
        self.assertEqual(entry.lines.get(account=self.overshort_acc).amount, Decimal("-3.50"))

    def test_20_x_report_and_z_report_numbers_match(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}, {"item_id": self.item2.id, "quantity": "2"}],
            payments_data=[{"method": "cash", "amount": "100.00"}],
        )
        x_rep = compute_shift_report(shift)
        closed = close_shift(shift=shift, closing_balance=Decimal("200.00"), actor="pos_admin")
        z_rep = compute_shift_report(closed)
        self.assertEqual(x_rep["total_sales"], z_rep["total_sales"])
        self.assertEqual(x_rep["expected_closing"], z_rep["expected_closing"])
        self.assertEqual(len(z_rep["top_items"]), 2)

    def test_21_tax_calculation_and_gl_liability_posting(self):
        self.terminal.tax_rate = Decimal("0.0500")
        self.terminal.save()
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "2"}],
            payments_data=[{"method": "cash", "amount": "105.00"}],
        )
        self.assertEqual(sale.subtotal, Decimal("100.00"))
        self.assertEqual(sale.tax, Decimal("5.00"))
        self.assertEqual(sale.total, Decimal("105.00"))
        from pos.services import _resolve_gl_accounts
        _, _, _, _, tax_acc, _, _, _ = _resolve_gl_accounts(self.terminal)
        self.assertEqual(sale.gl_entry.lines.get(account=tax_acc).amount, Decimal("-5.00"))
        self.assertEqual(sale.gl_entry.lines.get(account=self.rev_acc).amount, Decimal("-100.00"))

    def test_22_cogs_and_inventory_gl_posting(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1", "unit_cost": "30.00"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
        )
        self.assertEqual(sale.gl_entry.lines.get(account=self.cogs_acc).amount, Decimal("30.00"))
        from pos.services import _resolve_gl_accounts
        _, _, _, _, _, _, inv_acc, _ = _resolve_gl_accounts(self.terminal)
        self.assertEqual(sale.gl_entry.lines.get(account=inv_acc).amount, Decimal("-30.00"))

    def test_23_checkout_on_closed_shift_rejected(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("50.00"))
        close_shift(shift=shift, closing_balance=Decimal("50.00"), actor="pos_admin")
        with self.assertRaises(Problem) as ctx:
            checkout_sale(
                shift=shift,
                cashier=self.user,
                lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
                payments_data=[{"method": "cash", "amount": "50.00"}],
            )
        self.assertEqual(ctx.exception.code, "pos_shift_closed")

    def test_24_empty_cart_rejected(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("50.00"))
        with self.assertRaises(Problem) as ctx:
            checkout_sale(shift=shift, cashier=self.user, lines_data=[], payments_data=[{"method": "cash", "amount": "10.00"}])
        self.assertEqual(ctx.exception.code, "pos_empty_cart")

    def test_25_line_and_cart_discount_applied(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "2", "discount": "10.00"}],
            discount_amount="5.00",
            payments_data=[{"method": "cash", "amount": "85.00"}],
        )
        self.assertEqual(sale.subtotal, Decimal("100.00"))
        self.assertEqual(sale.discount, Decimal("15.00"))
        self.assertEqual(sale.total, Decimal("85.00"))

    def test_26_api_terminals_list_and_create(self):
        res = self.client.get("/api/pos/terminals")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["items"]), 1)

        create_res = self.client.post(
            "/api/pos/terminals",
            data=json.dumps({
                "site_id": self.site.id,
                "code": "POS-02",
                "name": "Express Lane",
                "cash_account_id": self.cash_acc.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(create_res.status_code, 200)
        self.assertEqual(create_res.json()["code"], "POS-02")

    def test_27_api_catalog_barcode_search(self):
        res = self.client.get(f"/api/pos/catalog?q=6291000000011&terminal_id={self.terminal.id}")
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "ITM-001")
        self.assertEqual(Decimal(items[0]["stock"]), Decimal("10.0000"))

    def test_28_api_shift_open_and_close_flow(self):
        open_res = self.client.post(
            "/api/pos/shifts",
            data=json.dumps({"terminal_id": self.terminal.id, "opening_balance": "120.00"}),
            content_type="application/json",
        )
        self.assertEqual(open_res.status_code, 200)
        shift_id = open_res.json()["id"]

        close_res = self.client.post(
            f"/api/pos/shifts/{shift_id}/close",
            data=json.dumps({"closing_balance": "120.00"}),
            content_type="application/json",
        )
        self.assertEqual(close_res.status_code, 200)
        self.assertEqual(close_res.json()["shift"]["status"], "closed")

    def test_29_api_checkout_and_receipt_html_rtl(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale_res = self.client.post(
            "/api/pos/sales",
            data=json.dumps({
                "shift_id": shift.id,
                "sale_id": "api-sale-900",
                "lines": [{"barcode": "6291000000028", "quantity": "2"}],
                "payments": [{"method": "cash", "amount": "50.00"}],
            }),
            content_type="application/json",
        )
        self.assertEqual(sale_res.status_code, 200)
        sale_id = sale_res.json()["id"]

        from backbone.models import Profile
        Profile.objects.update_or_create(user=self.user, defaults={"language": "ar"})
        html_res = self.client.get(f"/pos/print/receipt/{sale_id}")
        self.assertEqual(html_res.status_code, 200)
        body = html_res.content.decode("utf-8")
        self.assertIn('dir="rtl"', body)
        self.assertIn("إيصال مبيعات نقطة بيع", body)

    def test_30_api_void_sale_endpoint(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "50.00"}],
        )
        res = self.client.post(
            f"/api/pos/sales/{sale.id}/void",
            data=json.dumps({"reason": "Wrong scan"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["payment_status"], "void")

    def test_31_api_shift_x_report_html_and_json(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        j_res = self.client.get(f"/api/pos/shifts/{shift.id}/report?type=X")
        self.assertEqual(j_res.status_code, 200)
        self.assertEqual(j_res.json()["report_type"], "X")

        from backbone.models import Profile
        Profile.objects.update_or_create(user=self.user, defaults={"language": "ar"})
        h_res = self.client.get(f"/api/pos/shifts/{shift.id}/report?type=X&format=html")
        self.assertEqual(h_res.status_code, 200)
        self.assertIn('dir="rtl"', h_res.content.decode("utf-8"))

    def test_32_pos_category_unique_constraint(self):
        cat = PosCategory.objects.create(site=self.site, code="BEV", name_en="Beverages", name_ar="مشروبات")
        self.assertEqual(cat.code, "BEV")

    def test_33_command_palette_includes_pos_commands(self):
        from backbone.models import ModuleState
        ModuleState.objects.update_or_create(module_id="pos", defaults={"enabled": True})
        res = self.client.get("/api/commands?q=POS_N")
        self.assertEqual(res.status_code, 200)
        ids = [item["id"] for item in res.json()["items"]]
        self.assertIn("action.pos_new", ids)

    def test_34_unauthenticated_access_blocked(self):
        anon = Client()
        res = anon.get("/api/pos/terminals")
        self.assertIn(res.status_code, (401, 403))

    def test_35_manual_payment_method_supported(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item2.id, "quantity": "2"}],
            payments_data=[{"method": "manual", "amount": "50.00", "reference": "VOUCHER-77"}],
        )
        self.assertEqual(sale.payment_status, PosSale.STATUS_PAID)
        self.assertEqual(sale.payments.first().method, "manual")

    def test_36_cart_discount_reduces_taxable_base(self):
        self.terminal.tax_rate = Decimal("0.0500")
        self.terminal.save()
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        # 2 * 50 = 100 subtotal, 20 cart discount => 80 taxable => 4.00 VAT => 84.00 total
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "2"}],
            discount_amount="20.00",
            payments_data=[{"method": "cash", "amount": "84.00"}],
        )
        self.assertEqual(sale.tax, Decimal("4.00"))
        self.assertEqual(sale.total, Decimal("84.00"))
        self.assertEqual(sum(ln.amount for ln in sale.gl_entry.lines.all()), Decimal("0.00"))

    def test_37_cash_overpayment_returns_change_and_keeps_gl_balanced(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
            payments_data=[{"method": "cash", "amount": "100.00"}],
        )
        self.assertEqual(sale.total, Decimal("50.00"))
        self.assertEqual(sale.payments.first().amount, Decimal("50.00"))
        self.assertEqual(sale.gl_entry.lines.get(account=self.cash_acc).amount, Decimal("50.00"))
        self.assertEqual(sum(ln.amount for ln in sale.gl_entry.lines.all()), Decimal("0.00"))

    def test_38_moving_average_valuation_drives_cogs_and_return_reversal(self):
        from inventory.models import Valuation
        Valuation.objects.create(site=self.site, item=self.item1, quantity=Decimal("10"), value=Decimal("300.00"))
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "2"}],
            payments_data=[{"method": "cash", "amount": "100.00"}],
        )
        self.assertEqual(sale.gl_entry.lines.get(account=self.cogs_acc).amount, Decimal("60.00"))
        self.assertEqual(Valuation.objects.get(site=self.site, item=self.item1).value, Decimal("240.00"))
        void_or_refund_sale(sale=sale, actor_user=self.user, reason="Returned by customer")
        self.assertEqual(Valuation.objects.get(site=self.site, item=self.item1).value, Decimal("300.00"))

    def test_39_api_terminal_create_and_patch_explicit_gl_accounts(self):
        res = self.client.post(
            "/api/pos/terminals",
            data=json.dumps({
                "site_id": self.site.id,
                "code": "POS-GL",
                "name": "GL Mapped Terminal",
                "cash_account_id": self.cash_acc.id,
                "revenue_account_id": self.rev_acc.id,
                "tax_account_id": self.tax_acc.id,
                "cogs_account_id": self.cogs_acc.id,
                "inventory_account_id": self.inv_acc.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        tid = res.json()["id"]
        self.assertEqual(res.json()["revenue_account_id"], self.rev_acc.id)
        self.assertEqual(res.json()["inventory_account_id"], self.inv_acc.id)

        patch_res = self.client.patch(
            f"/api/pos/terminals/{tid}",
            data=json.dumps({"over_short_account_id": self.overshort_acc.id}),
            content_type="application/json",
        )
        self.assertEqual(patch_res.status_code, 200)
        self.assertEqual(patch_res.json()["over_short_account_id"], self.overshort_acc.id)

    def test_40_multisite_isolation_blocks_unauthorized_site_terminals(self):
        other_site = Site.objects.create(name="Abu Dhabi Branch")
        PosTerminal.objects.create(site=other_site, code="AUH-01", name="AUH Counter", cash_account=self.cash_acc)
        res = self.client.get("/api/pos/terminals")
        self.assertEqual(res.status_code, 200)
        codes = [t["code"] for t in res.json()["items"]]
        self.assertEqual(codes, ["POS-01"])

    def test_41_walkin_customer_reused_per_site(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        s1 = checkout_sale(shift=shift, cashier=self.user, lines_data=[{"item_id": self.item1.id, "quantity": "1"}], payments_data=[{"method": "cash", "amount": "50.00"}])
        s2 = checkout_sale(shift=shift, cashier=self.user, lines_data=[{"item_id": self.item1.id, "quantity": "1"}], payments_data=[{"method": "cash", "amount": "50.00"}])
        self.assertEqual(s1.customer_id, s2.customer_id)

    def test_42_catalog_enforces_terminal_site_permission(self):
        other_site = Site.objects.create(name="Sharjah Branch")
        other_term = PosTerminal.objects.create(
            site=other_site,
            code="SHJ-01",
            name="SHJ Counter",
            cash_account=self.cash_acc,
        )
        res = self.client.get(f"/api/pos/catalog?terminal_id={other_term.id}")
        self.assertEqual(res.status_code, 403)

    def test_43_overpayment_exceeds_cash_rejected(self):
        from backbone.http import Problem
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        with self.assertRaises(Problem) as ctx:
            checkout_sale(
                shift=shift,
                cashier=self.user,
                lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
                payments_data=[{"method": "card", "amount": "999999.00"}],
            )
        self.assertEqual(ctx.exception.code, "pos_overpayment_exceeds_cash")

    def test_44_negative_discount_rejected(self):
        from backbone.http import Problem

        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        with self.assertRaises(Problem) as line_ctx:
            checkout_sale(
                shift=shift,
                cashier=self.user,
                lines_data=[{"item_id": self.item1.id, "quantity": "1", "discount": "-5.00"}],
                payments_data=[{"method": "cash", "amount": "50.00"}],
            )
        self.assertEqual(line_ctx.exception.code, "pos_invalid_discount")

        with self.assertRaises(Problem) as cart_ctx:
            checkout_sale(
                shift=shift,
                cashier=self.user,
                lines_data=[{"item_id": self.item1.id, "quantity": "1"}],
                discount_amount="-5.00",
                payments_data=[{"method": "cash", "amount": "50.00"}],
            )
        self.assertEqual(cart_ctx.exception.code, "pos_invalid_discount")


    def test_45_concurrent_shift_open_rejected(self):
        from backbone.http import Problem

        open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("100.00"))
        with self.assertRaises(Problem) as ctx:
            open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("50.00"))
        self.assertEqual(ctx.exception.code, "pos_shift_already_open")


    def test_46_split_payment_three_methods_with_cash_change(self):
        shift = open_shift(terminal=self.terminal, cashier=self.user, opening_balance=Decimal("0.00"))
        # 2 * 50.00 = 100.00 total; paid 40 card + 30 manual + 50 cash = 120.00 => 20.00 cash change returned
        sale = checkout_sale(
            shift=shift,
            cashier=self.user,
            lines_data=[{"item_id": self.item1.id, "quantity": "2"}],
            payments_data=[
                {"method": "card", "amount": "40.00", "reference": "AUTH-401"},
                {"method": "manual", "amount": "30.00", "reference": "VOUCH-9"},
                {"method": "cash", "amount": "50.00"},
            ],
        )
        self.assertEqual(sale.total, Decimal("100.00"))
        by_method = {p.method: p.amount for p in sale.payments.all()}
        self.assertEqual(
            by_method,
            {
                "card": Decimal("40.00"),
                "manual": Decimal("30.00"),
                "cash": Decimal("30.00"),
            },
        )
        self.assertEqual(sum(by_method.values()), sale.total)
