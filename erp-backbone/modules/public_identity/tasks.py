from datetime import timedelta
from urllib.parse import urlencode
from uuid import uuid4
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from .models import IdentityChallenge
from .services import challenge_token

@shared_task(name="public_identity.deliver_challenge",autoretry_for=())
def deliver_challenge(pk,lease):
    from backbone.registry import enabled_modules
    with transaction.atomic():
        row=IdentityChallenge.objects.select_for_update().select_related("identity","space").filter(pk=pk,lease=lease,sent_at=None,consumed_at=None,expires_at__gt=timezone.now()).first()
        if not row or not row.space.active or not {"publication","public_identity"}.issubset(enabled_modules()): return
        if row.identity.status=="disabled": return
        link=settings.ERP_PUBLIC_BASE_URL+"/public/auth/confirm#"+urlencode({"purpose":row.purpose,"challenge_id":row.pk,"token":challenge_token(row)})
        message=EmailMessage("Confirm your account / تأكيد الحساب" if row.purpose=="verify" else "Reset password / تغيير كلمة المرور",
            "Open the link and press Confirm / افتح الرابط واضغط تأكيد:\n"+link,to=[row.identity.normalized_email])
        try:
            message.send(fail_silently=False)
        except Exception:
            # No provider exceptions/addresses/tokens in audit or logs.
            row.last_error="smtp_unavailable";row.save(update_fields=["last_error"]);return
        row.sent_at=timezone.now();row.last_error="";row.save(update_fields=["sent_at","last_error"])

def dispatch(limit=20):
    from backbone.celeryapp import app
    from kombu.exceptions import OperationalError
    import redis
    count=0
    for _ in range(limit):
        with transaction.atomic():
            row=IdentityChallenge.objects.select_for_update(skip_locked=True).filter(sent_at=None,consumed_at=None,
                expires_at__gt=timezone.now(),next_attempt_at__lte=timezone.now(),attempts__lt=8).order_by("next_attempt_at").first()
            if not row: break
            row.lease=uuid4();row.attempts+=1
            row.next_attempt_at=timezone.now()+timedelta(seconds=min(3600,30*2**row.attempts))
            row.save(update_fields=["lease","attempts","next_attempt_at"])
        try: app.send_task("public_identity.deliver_challenge",args=[str(row.pk),str(row.lease)],queue="notifications",retry=False)
        except (OperationalError,redis.RedisError,OSError): break
        count+=1
    return count
