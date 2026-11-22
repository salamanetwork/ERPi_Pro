#!/usr/bin/env python3
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MODULES_DIR = BASE_DIR / "modules"
if MODULES_DIR.is_dir() and str(MODULES_DIR) not in sys.path:
    sys.path.insert(1, str(MODULES_DIR))

from django.core.management import execute_from_command_line

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backbone.settings")
    execute_from_command_line(sys.argv)
