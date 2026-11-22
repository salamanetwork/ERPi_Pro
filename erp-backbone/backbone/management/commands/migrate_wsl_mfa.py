from cryptography.fernet import InvalidToken
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from backbone.mfa import cipher
from backbone.models import Authenticator

class Command(BaseCommand):
    help="Rewrap legacy Django-key TOTP secrets, preserving already migrated rows."
    @transaction.atomic
    def handle(self,**opts):
        if settings.ERP_MFA_KEY == settings.SECRET_KEY:
            raise CommandError("Configure the independent MFA key first.")
        new,old=cipher(),cipher(settings.SECRET_KEY)
        count=0
        for device in Authenticator.objects.select_for_update().exclude(encrypted_key="").order_by("pk"):
            token=device.encrypted_key.encode()
            try:new.decrypt(token)
            except InvalidToken:
                try:plain=old.decrypt(token)
                except InvalidToken:raise CommandError("Unknown MFA key. All database changes rolled back.") from None
                device.encrypted_key=new.encrypt(plain).decode()
                device.save(update_fields=["encrypted_key"])
                count+=1
        self.stdout.write(f"MFA credentials rewrapped: {count}")
