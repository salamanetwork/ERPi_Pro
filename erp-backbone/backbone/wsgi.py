import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODULES_DIR = BASE_DIR / "modules"
if MODULES_DIR.is_dir() and str(MODULES_DIR) not in sys.path:
    sys.path.insert(1, str(MODULES_DIR))

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backbone.settings")
application = get_wsgi_application()
