from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from backbone.models import Site
from subscriptions.gl import sync
from subscriptions.models import Cycle

class Command(BaseCommand):
    help = 'Replay historical subscription documents and recognition through the site-local date.'
    def add_arguments(self, parser):
        parser.add_argument('--site',required=True)
        parser.add_argument('--dry-run',action='store_true')

    @transaction.atomic
    def handle(self,site,dry_run,**kwargs):
        branch = Site.objects.filter(name=site).first()
        if not branch:
            raise CommandError('Unknown site')
        cycles = Cycle.objects.filter(contract__site=branch).order_by('start','pk')
        created = set()
        preview = set() if dry_run else None
        pending = 0
        for pk in cycles.values_list('pk',flat=True).iterator():
            schedule = sync(pk, preview=preview, created=created)
            if schedule.warning:
                pending += 1
                self.stdout.write(f'cycle {pk}: {schedule.warning}')
        count = len(preview if dry_run else created)
        self.stdout.write(f'new entries={count}, pending cycles={pending}, dry-run={dry_run}')
