from pathlib import Path

from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand, CommandError

from backbone.backups import restore_backup


class Command(BaseCommand):
    help = "Restore an ERP backup to an explicitly named, empty PostgreSQL database."

    def add_arguments(self, parser):
        parser.add_argument("archive", type=Path)
        parser.add_argument("--target-db", required=True)
        parser.add_argument("--target-media", required=True, type=Path)

    def handle(self, archive, target_db, target_media, **options):
        try:
            count = restore_backup(archive, target_db, target_media)
        except InvalidToken as error:
            raise CommandError("The backup requires the original MFA key.") from error
        except Exception as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(
            f"Restored database {target_db}: {count} work order(s). Attachments: {target_media}"
        ))
