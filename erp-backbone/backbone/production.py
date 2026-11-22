"""Settings for Gunicorn behind the private Compose Nginx proxy."""
import re

from django.core.exceptions import ImproperlyConfigured

from .settings import *


def required_secret(name):
    value = os.getenv(name, "")
    if len(value) < 32 or value.startswith(("CHANGE_ME", "REPLACE_ME", "django-insecure-")):
        raise ImproperlyConfigured(f"{name} needs a separate generated secret (at least 32 characters).")
    return value


ERP_APP_MODE = app_mode("production")
DEBUG = False
SECRET_KEY = required_secret("ERP_SECRET_KEY")
ERP_CUSTOMER_JWT_KEY = required_secret("ERP_CUSTOMER_JWT_KEY")
if ERP_CUSTOMER_JWT_KEY == SECRET_KEY:
    raise ImproperlyConfigured("Customer JWT key must differ from staff SECRET_KEY.")
ERP_MFA_KEY = required_secret("ERP_MFA_KEY")
if ERP_MFA_KEY == SECRET_KEY:
    raise ImproperlyConfigured("ERP_MFA_KEY and ERP_SECRET_KEY must be different.")
host = os.getenv("ERP_HOST", "")
if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host) or ".." in host:
    raise ImproperlyConfigured("ERP_HOST must be a single hostname or IPv4 address.")
ALLOWED_HOSTS = [host]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = os.getenv("ERP_HSTS_PRELOAD") == "1"
# An operator may explicitly opt into the preload header for a dedicated HTTPS domain.
SILENCED_SYSTEM_CHECKS = [] if SECURE_HSTS_PRELOAD else ["security.W021"]
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"
STATIC_ROOT = Path("/srv/erp/static")


ERP_MARKETING_HMAC_KEY = required_secret("ERP_MARKETING_HMAC_KEY") if ERP_MARKETING_LIVE else SECRET_KEY

from urllib.parse import urlsplit
_public_origin=urlsplit(ERP_PUBLIC_BASE_URL)
if _public_origin.scheme!="https" or not _public_origin.hostname or _public_origin.username or _public_origin.query or _public_origin.fragment or _public_origin.path not in {"","/"}:
    raise ImproperlyConfigured("ERP_PUBLIC_BASE_URL must be an HTTPS origin without a path or credentials.")
