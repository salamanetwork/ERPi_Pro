from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from backbone.models import AuditEvent, Authenticator


class Command(BaseCommand):
    help = "Revoke an account's authenticator so it can enroll again."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--confirm", action="store_true", required=True)

    def handle(self, username, **options):
        user = get_user_model().objects.filter(username=username).first()
        if not user:
            raise CommandError("Account not found.")
        with transaction.atomic():
            device = Authenticator.objects.select_for_update().filter(user=user).first()
            if device:
                device.delete()
            AuditEvent.objects.create(actor="local console", action="mfa.reset", target=str(user.pk))
        self.stdout.write(self.style.SUCCESS("Authenticator revoked. Sign in and enroll the replacement."))
