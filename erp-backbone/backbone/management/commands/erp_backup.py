from pathlib import Path
import time

from django.core.management.base import BaseCommand, CommandError

from backbone.backups import archives, backup_lock, create_backup


class Command(BaseCommand):
    help = "Create an ERP backup now, or run the scheduled backup worker."

    def add_arguments(self, parser):
        parser.add_argument("directory", type=Path)
        parser.add_argument("--schedule", action="store_true")
        parser.add_argument("--interval", type=int, default=86400, help="Seconds between scheduled backups.")
        parser.add_argument("--retain", type=int, default=14)

    def handle(self, directory, schedule, interval, retain, **options):
        if interval < 60 or retain < 1:
            raise CommandError("Interval must be at least 60 seconds and retention must be positive.")
        while True:
            try:
                with backup_lock(directory):
                    previous = archives(directory)
                    if not schedule or not previous or time.time() - previous[0].stat().st_mtime >= interval:
                        archive = create_backup(directory)
                        self.stdout.write(f"Backup created: {archive}")
                        for expired in archives(directory)[retain:]:
                            expired.unlink()
            except Exception as error:
                if not schedule:
                    raise CommandError(str(error)) from error
                self.stderr.write(f"Scheduled backup failed; retrying in one minute: {error}")
            if not schedule:
                return
            time.sleep(60)
