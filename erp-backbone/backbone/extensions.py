"""Deployment-time registration for trusted, installed ERP applications."""
from importlib import import_module

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def url_patterns():
    for package in settings.ERP_COMPATIBLE_EXTENSION_APPS:
        yield from import_module(f"{package}.urls").urlpatterns


def allowed_extension_path(path):
    package = path.split(".", 1)[0]
    return package not in settings.ERP_EXTENSION_MANIFESTS or package in settings.ERP_COMPATIBLE_EXTENSION_APPS


def posting_handler(kind):
    path = settings.ERP_STOCK_POSTING_HANDLERS.get(kind)
    return import_string(path) if path and allowed_extension_path(path) else None


def spa_prefixes():
    for package in settings.ERP_COMPATIBLE_EXTENSION_APPS:
        if not package.isidentifier():
            raise ImproperlyConfigured("ERP extension package names must be simple Python identifiers.")
        yield package


def module_can_disable(module_id):
    check = settings.ERP_EXTENSION_OPEN_RECORDS.get(module_id)
    return not check or not allowed_extension_path(check) or not import_string(check)().exists()


def site_blockers(site_id):
    return {f"{module_id}_orders": import_string(check)(site_id).count()
            for module_id, check in settings.ERP_EXTENSION_OPEN_RECORDS.items() if allowed_extension_path(check)}


def extension_hook(name):
    path = settings.ERP_EXTENSION_HOOKS.get(name)
    return import_string(path) if path and allowed_extension_path(path) else None


def invoke_extension(name, *args):
    hook = extension_hook(name)
    if hook:
        return hook(*args)
