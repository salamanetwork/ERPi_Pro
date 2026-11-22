from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from backbone.mfa import cipher as mfa_cipher
from backbone.services.connector_client import cipher
from backbone.models import ApiConnector,WebhookEndpoint,Authenticator
class Command(BaseCommand):
    help="Rotate encrypted keys within a single transaction during a maintenance window."
    def add_arguments(self,p):
        p.add_argument('--old-mfa-key')
        p.add_argument('--old-connector-key')
    @transaction.atomic
    def handle(self,**opts):
        m,c=opts['old_mfa_key'],opts['old_connector_key']
        if not(m or c):raise CommandError('Supply at least one previous key.')
        columns=[]
        if m:columns.append((Authenticator,'encrypted_key',mfa_cipher(m),mfa_cipher()))
        if c:
            for model,field in ((ApiConnector,'encrypted_credentials'),(WebhookEndpoint,'encrypted_secret')):columns.append((model,field,cipher(c),cipher()))
        try:
            for model,field,old,new in columns:
                for row in model.objects.select_for_update().exclude(**{field:''}).order_by('pk').iterator():
                    setattr(row,field,new.encrypt(old.decrypt(getattr(row,field).encode())).decode())
                    row.save(update_fields=[field])
        except InvalidToken:raise CommandError('Decryption failed. All changes rolled back.') from None
        self.stdout.write('Rotation complete.')
