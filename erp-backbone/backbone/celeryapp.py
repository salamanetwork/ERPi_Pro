import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODULES_DIR = BASE_DIR / "modules"
if MODULES_DIR.is_dir() and str(MODULES_DIR) not in sys.path:
    sys.path.insert(1, str(MODULES_DIR))

from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backbone.settings")
app = Celery("erp")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

if ready_file := os.getenv("ERP_WORKER_READY_FILE"):
    @worker_ready.connect
    def mark_worker_ready(**kwargs):
        Path(ready_file).touch()
