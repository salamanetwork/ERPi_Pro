import uuid
from django.db import models
from django.db.models.functions import Lower
from django.conf import settings

class ExternalIdentity(models.Model):
    party = models.ForeignKey("parties.Party", on_delete=models.PROTECT, null=True, blank=True)

    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    normalized_email=models.EmailField(max_length=254,unique=True)
    password_hash=models.CharField(max_length=256)
    display_name=models.CharField(max_length=120)
    status=models.CharField(max_length=12,default="pending")
    verified_at=models.DateTimeField(null=True)
    auth_version=models.PositiveIntegerField(default=1)
    revision=models.PositiveIntegerField(default=1)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(Lower("normalized_email"),name="external_email_lower_unique")]

class ExternalMembership(models.Model):
    identity=models.ForeignKey(ExternalIdentity,on_delete=models.CASCADE)
    space=models.ForeignKey("publication.PublicationSpace",on_delete=models.PROTECT)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["identity","space"],name="external_membership_unique")]

class ExternalSession(models.Model):
    last_activity=models.DateTimeField(null=True)
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    identity=models.ForeignKey(ExternalIdentity,on_delete=models.CASCADE)
    space=models.ForeignKey("publication.PublicationSpace",on_delete=models.PROTECT)
    refresh_jti_hash=models.CharField(max_length=64,unique=True)
    expires_at=models.DateTimeField()
    revoked_at=models.DateTimeField(null=True)
    created_at=models.DateTimeField(auto_now_add=True)

class IdentityChallenge(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    identity=models.ForeignKey(ExternalIdentity,on_delete=models.CASCADE)
    space=models.ForeignKey("publication.PublicationSpace",on_delete=models.PROTECT)
    purpose=models.CharField(max_length=16)
    token_hash=models.CharField(max_length=64)
    expires_at=models.DateTimeField()
    consumed_at=models.DateTimeField(null=True)
    attempts=models.PositiveIntegerField(default=0)
    next_attempt_at=models.DateTimeField()
    sent_at=models.DateTimeField(null=True)
    lease=models.UUIDField(null=True)
    last_error=models.CharField(max_length=40,blank=True)

class IdentityAudit(models.Model):
    identity=models.ForeignKey(ExternalIdentity,on_delete=models.PROTECT,null=True)
    staff_actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True)
    event=models.CharField(max_length=60)
    target=models.CharField(max_length=100,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)

class AuthRateBucket(models.Model):
    key=models.CharField(max_length=64,unique=True)
    window=models.BigIntegerField()
    count=models.PositiveIntegerField(default=0)
