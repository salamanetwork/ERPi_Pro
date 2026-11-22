"""Offline TOTP enrollment and verification for password-authenticated users."""
import base64
from datetime import timedelta
import hashlib
import secrets
import time

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
import pyotp

from .access import audit
from .models import Assignment, Authenticator

CHALLENGE = "erp_mfa_challenge"
VERIFIED = "erp_mfa_verified"
CHALLENGE_SECONDS = 300
MAX_FAILURES = 5


def required(user):
    if user.is_superuser or user.is_staff or settings.ERP_MFA_ALL:
        return True
    assignments = Assignment.objects.filter(user=user).select_related("role")
    return any(
        assignment.role.name in settings.ERP_MFA_ROLES
        or "platform.audit" in assignment.role.permissions
        for assignment in assignments
    )


def cipher(key=None):
    if key is None and (len(settings.ERP_MFA_KEY)<32 or settings.ERP_MFA_KEY in (settings.SECRET_KEY,settings.ERP_CUSTOMER_JWT_KEY,settings.ERP_CONNECTOR_KEY)):
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured("Set an independent ERP_MFA_KEY with at least 32 characters.")
    material = b"erp-backbone/totp/v1\0" + (settings.ERP_MFA_KEY if key is None else key).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def stamp(device):
    return hashlib.sha256(device.encrypted_key.encode()).hexdigest()


def session_allowed(request):
    device = Authenticator.objects.filter(user=request.user).first()
    if device and device.encrypted_key:
        return constant_time_compare(request.session.get(VERIFIED, ""), stamp(device))
    return VERIFIED not in request.session and not required(request.user)


def start(request, user):
    request.session.flush()
    device, _ = Authenticator.objects.get_or_create(user=user)
    if device.blocked_until and device.blocked_until > timezone.now():
        return None
    challenge = {
        "user": user.pk, "backend": user.backend, "expires": int(time.time()) + CHALLENGE_SECONDS,
        "password": user.get_session_auth_hash(),
    }
    if not device.encrypted_key:
        challenge["key"] = cipher().encrypt(pyotp.random_base32().encode()).decode()
    request.session[CHALLENGE] = challenge
    return describe(challenge)


def describe(challenge):
    if not challenge or challenge["expires"] <= time.time():
        return None
    if "key" in challenge:
        return {"mode": "enroll", "setup_key": cipher().decrypt(challenge["key"].encode()).decode()}
    return {"mode": "verify"}


def verify(request, code):
    challenge = request.session.get(CHALLENGE)
    if not challenge or challenge["expires"] <= time.time():
        request.session.pop(CHALLENGE, None)
        return "mfa_expired"
    with transaction.atomic():
        user = get_user_model().objects.filter(pk=challenge["user"], is_active=True).first()
        if not user or not constant_time_compare(user.get_session_auth_hash(), challenge["password"]):
            request.session.pop(CHALLENGE, None)
            return "mfa_expired"
        device = Authenticator.objects.select_for_update().filter(user=user).first()
        enrolling = "key" in challenge
        if not device or enrolling == bool(device.encrypted_key):
            request.session.pop(CHALLENGE, None)
            return "mfa_expired"
        now = timezone.now()
        if device.blocked_until:
            if device.blocked_until > now:
                request.session.pop(CHALLENGE, None)
                return "mfa_locked"
            device.failures = 0
            device.blocked_until = None

        encrypted_key = challenge["key"] if enrolling else device.encrypted_key
        totp = pyotp.TOTP(cipher().decrypt(encrypted_key.encode()).decode())
        current_step = int(now.timestamp()) // totp.interval
        matched = next(
            (step for step in (current_step, current_step - 1, current_step + 1)
             if step > device.last_step and secrets.compare_digest(totp.generate_otp(step), code)),
            None,
        )
        if matched is None:
            device.failures += 1
            locked = device.failures >= MAX_FAILURES
            if locked:
                device.blocked_until = now + timedelta(minutes=5)
                request.session.pop(CHALLENGE, None)
                audit(user, "mfa.lock", user.pk)
            device.save(update_fields=["failures", "blocked_until"])
            return "mfa_locked" if locked else "invalid_mfa_code"

        device.encrypted_key = encrypted_key
        device.last_step = matched
        device.failures = 0
        device.blocked_until = None
        device.save(update_fields=["encrypted_key", "last_step", "failures", "blocked_until"])
        audit(user, "mfa.enroll" if enrolling else "mfa.verify", user.pk)

    request.session.pop(CHALLENGE, None)
    login(request, user, backend=challenge["backend"])
    request.session[VERIFIED] = stamp(device)
    return None
