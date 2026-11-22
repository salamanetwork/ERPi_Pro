"""Expose the ERP Celery app for the conventional `celery -A backbone` command."""

from .celeryapp import app

__all__ = ["app"]
