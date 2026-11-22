from django.apps import AppConfig


class SalesConfig(AppConfig):
    name = "sales"

    def ready(self):
        from . import events  # noqa: F401
