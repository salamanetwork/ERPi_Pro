from pathlib import Path
from time import monotonic, sleep

from django.core.management.base import BaseCommand
from django.db import InterfaceError, OperationalError, close_old_connections, connections

from backbone.registry import enabled_modules
from backbone.services.outbox import pump


class Command(BaseCommand):
    help = "Publish pending notification deliveries to Celery."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--ready-file", type=Path, help="Create this file after the first successful dispatch pass.")

    def handle(self, *args, **options):
        ready = False
        database_unavailable = False
        next_sla_check = 0

        def announce_ready():
            nonlocal ready
            if marker := options["ready_file"]:
                marker.touch()
            self.stdout.write("Outbox Dispatcher ready: PostgreSQL and Redis connected.")
            ready = True

        while True:
            try:
                close_old_connections()
                from backbone.module_events import dispatch as dispatch_modules
                dispatch_modules()
                active = enabled_modules()
                if "publication" in active:
                    from publication.tasks import dispatch as publication_dispatch
                    publication_dispatch()
                if {"publication","public_identity"}.issubset(active):
                    from public_identity.tasks import dispatch as identity_dispatch
                    identity_dispatch()
                if "marketing" in active:
                    from marketing.services import dispatch
                    dispatch()
                if monotonic() >= next_sla_check:
                    if "subscriptions" in active:
                        from subscriptions.tasks import dispatch as dispatch_subscriptions
                        dispatch_subscriptions()
                    if "helpdesk" in active:
                        from helpdesk.sla import schedule_overdue

                        schedule_overdue()
                    if "marketing" in active:
                        from marketing.feedback import timezone_alerts
                        from marketing.services import expire_consents

                        expire_consents()
                        timezone_alerts()
                    next_sla_check = monotonic() + 30
                pump(on_ready=None if ready else announce_ready)
                database_unavailable = False
            except (OperationalError, InterfaceError):
                if options["once"]:
                    raise
                connections.close_all()
                if not database_unavailable:
                    self.stderr.write("Outbox Dispatcher waiting for PostgreSQL; retrying in 2 seconds.")
                database_unavailable = True
            if options["once"]:
                return
            sleep(2)
