import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .compatibility import compatible_modules, extension_manifests

def app_mode(default):
    mode = os.getenv("ERP_APP_MODE", default).strip().lower()
    if mode not in {"development", "production"}:
        raise ImproperlyConfigured("ERP_APP_MODE must be development or production.")
    return mode


import sys

ERP_APP_MODE = app_mode("development")
BASE_DIR = Path(__file__).resolve().parent.parent
MODULES_DIR = BASE_DIR / "modules"
if MODULES_DIR.is_dir() and str(MODULES_DIR) not in sys.path:
    sys.path.insert(1, str(MODULES_DIR))

DEBUG = os.getenv("ERP_DEBUG") == "1"
SECRET_KEY = os.getenv("ERP_SECRET_KEY", "")
ALLOWED_HOSTS = os.getenv("ERP_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
ROOT_URLCONF = "backbone.urls"
WSGI_APPLICATION = "backbone.wsgi.application"
ERP_EXTENSION_APPS = ("manufacturing", "projects", "helpdesk", "marketing", "publication", "public_identity", "media_service") + tuple(
    key.strip() for key in os.getenv("ERP_EXTRA_EXTENSIONS", "").split(",") if key.strip()
)
# Flat modules/ discovery: trusted apps under modules/ opt in with erpi_module.marker.
_native_apps = ['accounting', 'assets', 'backbone', 'crm', 'hr', 'inventory', 'purchasing', 'sales', 'supply', 'workorders']
_generated = sorted(
    p.parent.name
    for p in (*MODULES_DIR.glob("*/erpi_module.marker"), *BASE_DIR.glob("*/erpi_module.marker"))
    if p.parent.name not in _native_apps and p.parent.name != "modules"
)
ERP_EXTENSION_APPS = tuple(dict.fromkeys((*ERP_EXTENSION_APPS, *_generated)))
ERP_EXTENSION_MANIFESTS = dict(extension_manifests(MODULES_DIR, ERP_EXTENSION_APPS))
_compatible_extensions = compatible_modules(ERP_EXTENSION_MANIFESTS)
ERP_COMPATIBLE_EXTENSION_APPS = tuple(key for key in ERP_EXTENSION_APPS if key in _compatible_extensions)
ERP_EXTENSION_OPEN_RECORDS = {"helpdesk": "helpdesk.lifecycle.open_tickets", "manufacturing": "manufacturing.lifecycle.open_orders", "projects": "projects.lifecycle.open_projects"}
ERP_EXTENSION_HOOKS = {
    "timesheet_transition":"projects.time.transition",
    "timesheet_detail":"projects.time.selected",
    "timesheet_reviewable":"projects.time.reviewable",
    "project_purchase_new":"projects.links.check_purchases",
    "project_work_issue":"projects.links.check_work",
}
ERP_STOCK_POSTING_HANDLERS = {
    kind: "manufacturing.accounting.effect"
    for kind in ("mfg_issue", "mfg_return", "mfg_output", "mfg_scrap")
}
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.staticfiles", "backbone", "assets", "workorders", "purchasing", "inventory", "accounting", "sales", "hr", "crm", "supply",
]
INSTALLED_APPS += ERP_COMPATIBLE_EXTENSION_APPS
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "backbone.security_health.ActivityMiddleware",
]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.getenv("ERP_DB_NAME", "erp_backbone"),
    "USER": os.getenv("ERP_DB_USER", "erp_backbone"),
    "PASSWORD": os.getenv("ERP_DB_PASSWORD", ""),
    "HOST": os.getenv("ERP_DB_HOST", "127.0.0.1"),
    "PORT": os.getenv("ERP_DB_PORT", "5432"),
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "frontend" / "dist"] if (BASE_DIR / "frontend" / "dist").exists() else []
STATIC_ROOT = BASE_DIR / "staticfiles"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_FAILURE_VIEW = "backbone.http.csrf_failure"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

ERP_MFA_ALL = os.getenv("ERP_MFA_ALL") == "1"
ERP_MFA_ROLES = frozenset(filter(None, (name.strip() for name in os.getenv("ERP_MFA_ROLES", "").split(","))))
ERP_MFA_KEY = os.getenv("ERP_MFA_KEY", "")
MEDIA_ROOT = Path(os.getenv("ERP_MEDIA_ROOT", BASE_DIR / "media"))

TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}]

CELERY_BROKER_URL = os.getenv("ERP_REDIS_URL", "redis://127.0.0.1:6379/0")
CELERY_BROKER_CONNECTION_TIMEOUT = 2
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = None
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_connect_timeout": 2, "socket_timeout": 2, "visibility_timeout": 120}
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = 20
CELERY_TASK_TIME_LIMIT = 25
CELERY_TASK_DEFAULT_QUEUE = "notifications"
CELERY_TASK_ROUTES = {"backbone.tasks.deliver_notification": {"queue": "notifications"}}
ERP_SMTP_ENABLED = os.getenv("ERP_SMTP_ENABLED") == "1"
EMAIL_HOST = os.getenv("ERP_SMTP_HOST", "localhost")
EMAIL_PORT = int(os.getenv("ERP_SMTP_PORT", "587"))
EMAIL_HOST_USER = os.getenv("ERP_SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("ERP_SMTP_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("ERP_SMTP_TLS", "1") == "1"
EMAIL_TIMEOUT = 8
DEFAULT_FROM_EMAIL = os.getenv("ERP_SMTP_FROM", "erp@localhost")

ERP_MARKETING_LOCAL = ERP_APP_MODE == "development" and os.getenv("ERP_MARKETING_LOCAL", "1") == "1"
ERP_MARKETING_LIVE = ERP_APP_MODE == "production" and os.getenv("ERP_MARKETING_LIVE") == "1"
ERP_MARKETING_SMTP_HOST = os.getenv("ERP_MARKETING_SMTP_HOST", "127.0.0.1")
ERP_MARKETING_SMTP_PORT = int(os.getenv("ERP_MARKETING_SMTP_PORT", "1025"))
ERP_MARKETING_PUBLIC_URL = os.getenv("ERP_MARKETING_PUBLIC_URL", "http://127.0.0.1:8001").rstrip("/")
ERP_MARKETING_HMAC_KEY = os.getenv("ERP_MARKETING_HMAC_KEY", SECRET_KEY)
ERP_MARKETING_TEST_RECIPIENTS = frozenset(value.strip() for value in os.getenv("ERP_MARKETING_TEST_RECIPIENTS", "").split(",") if value.strip())
ERP_MARKETING_CHANNEL_RATE = {channel: int(os.getenv("ERP_MARKETING_RATE_" + channel.upper(), "10")) for channel in ("email", "sms", "whatsapp")}
ERP_TWILIO_ACCOUNT_SID = os.getenv("ERP_TWILIO_ACCOUNT_SID", "")
ERP_TWILIO_AUTH_TOKEN = os.getenv("ERP_TWILIO_AUTH_TOKEN", "")

# Platform Services Foundation: external identities do not share Django User.
INSTALLED_APPS += ["rest_framework", "storages"]
ERP_CUSTOMER_JWT_KEY = os.getenv("ERP_CUSTOMER_JWT_KEY", "")
if not ERP_CUSTOMER_JWT_KEY:
    if ERP_APP_MODE == "production":
        raise ImproperlyConfigured("ERP_CUSTOMER_JWT_KEY is required in production (32+ random characters).")
    from django.utils.crypto import salted_hmac
    ERP_CUSTOMER_JWT_KEY = salted_hmac("erpi.external.jwt", SECRET_KEY).hexdigest()
if len(ERP_CUSTOMER_JWT_KEY) < 32:
    raise ImproperlyConfigured("ERP_CUSTOMER_JWT_KEY must contain at least 32 characters.")
ERP_PUBLIC_BASE_URL = os.getenv("ERP_PUBLIC_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
if ERP_APP_MODE == "production" and not ERP_PUBLIC_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured("ERP_PUBLIC_BASE_URL must be an HTTPS origin in production.")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"file_permissions_mode":0o600,"directory_permissions_mode":0o700}},
    "public_media": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"file_permissions_mode":0o600,"directory_permissions_mode":0o700}},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Mailpit only in Development. Production uses the existing SMTP settings.
if ERP_APP_MODE == "development":
    EMAIL_HOST = os.getenv("ERP_SMTP_HOST", "127.0.0.1")
    EMAIL_PORT = int(os.getenv("ERP_SMTP_PORT", "1025"))
    EMAIL_USE_TLS = os.getenv("ERP_SMTP_TLS", "0") == "1"
ERP_CONNECTOR_KEY = os.getenv("ERP_CONNECTOR_KEY","")
MIDDLEWARE = ["backbone.correlation.RequestID", *MIDDLEWARE]
