"""Populate a fresh benchmark-only database with deterministic synthetic data."""
from datetime import timedelta
from decimal import Decimal
from itertools import islice
import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from accounting.models import Account, Entry, Ledger, Line, Period
from backbone.models import Assignment, ModuleState, Role, Site
from inventory.models import Item, Location, Movement, Position, Unit, Valuation, Warehouse
from workorders.models import WorkOrder


class Command(BaseCommand):
    help = "Seed a fresh staging database named benchmark_* for performance testing."

    def add_arguments(self, parser):
        parser.add_argument("--pairs", type=int, default=50000)

    @transaction.atomic
    def handle(self, *, pairs, **options):
        if connection.vendor != "postgresql" or not connection.settings_dict["NAME"].startswith("benchmark_"):
            raise CommandError("Use a fresh PostgreSQL database named benchmark_*.")
        users = get_user_model()
        password = os.environ.get("ERP_BENCH_PASSWORD") or secrets.token_urlsafe(24)
        if not 1 <= pairs <= 500000 or Site.objects.exists() or users.objects.exists() or Entry.objects.exists():
            raise CommandError("Benchmark seed requires a fresh database and 1 to 500000 pairs.")

        people = users.objects.create_user(username="benchmark", password=password)
        role = Role.objects.create(name="Benchmark operator", permissions=[
            "workorders.view", "workorders.create", "inventory.view", "accounting.view",
        ])
        Assignment.objects.create(user=people, role=role)
        for key in ("workorders", "inventory", "accounting"):
            ModuleState.objects.update_or_create(module_id=key, defaults={"enabled": True})

        now = timezone.now()
        first = day = now.date().replace(day=1)
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        ledger = Ledger.objects.get(pk=1)
        ledger.cutoff, ledger.activated_at, ledger.activated_by = day, now, "benchmark"
        ledger.save(update_fields=["cutoff", "activated_at", "activated_by"])
        period = Period.objects.create(ledger=ledger, start=first, end=last)
        bank = Account.objects.get(ledger=ledger, code="1100")
        expense = Account.objects.create(ledger=ledger, code="BENCH-EXP", name="Benchmark expense",
                                         kind="expense", detail=True)
        unit, _ = Unit.objects.get_or_create(code="PC", defaults={"name": "Piece"})
        sites = [Site.objects.create(name=f"Benchmark site {index}") for index in range(1, 5)]
        locations = [Location.objects.create(warehouse=Warehouse.objects.create(site=site, code="WH", name="Test"),
                                           code="R1", name="Test shelf") for site in sites]
        items = [Item.objects.create(code=f"BENCH-{index:04}", name=f"Benchmark item {index}", base_unit=unit)
                 for index in range(min(1000, pairs))]
        one = Decimal("1.00")
        quantities = {}
        pending = iter(range(pairs))
        while batch := list(islice(pending, 1000)):
            entries = Entry.objects.bulk_create([
                Entry(ledger=ledger, period=period, date=day, description=f"Benchmark journal {index}",
                      kind="manual", created_by=people.username, posted_by=people.username, posted_at=now)
                for index in batch])
            movements, lines = [], []
            for index, entry in zip(batch, entries):
                site_index = index % len(sites)
                site, location, item = sites[site_index], locations[site_index], items[index % len(items)]
                for sign in (one, -one):
                    movements.append(Movement(site=site, item=item, location=location, kind="opening",
                        quantity=one, value=one, actor=people.username, actor_id=people.pk, occurred_at=now,
                        business_date=day, facts={"currency": "AED", "synthetic": True}))
                    lines.append(Line(entry=entry, account=expense if sign > 0 else bank, amount=sign, site=site))
                key = (site.pk, location.pk, item.pk)
                quantities[key] = quantities.get(key, 0) + 2
            Movement.objects.bulk_create(movements)
            Line.objects.bulk_create(lines)
        Position.objects.bulk_create([Position(site_id=s, location_id=l, item_id=i, quantity=q)
                                      for (s, l, i), q in quantities.items()])
        Valuation.objects.bulk_create([Valuation(site_id=s, item_id=i, quantity=q, value=q)
                                       for (s, _, i), q in quantities.items()])
        WorkOrder.objects.bulk_create([WorkOrder(site=sites[index % len(sites)], title=f"Benchmark WO {index}")
                                       for index in range(min(2000, pairs))])
        self.stdout.write(self.style.SUCCESS(f"Seeded {pairs * 2} movements and {pairs * 2} lines."))
        self.stdout.write(f"K6_USERNAME=benchmark\nK6_SITE_ID={sites[0].pk}\nK6_MONTH={day:%Y-%m}\nK6_PASSWORD={password}")
