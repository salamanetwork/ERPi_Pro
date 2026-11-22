import uuid
from django.conf import settings
from django.db import models

class MediaAsset(models.Model):
    folder=models.ForeignKey("MediaFolder",null=True,blank=True,on_delete=models.PROTECT)
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    space=models.ForeignKey("publication.PublicationSpace",on_delete=models.PROTECT)
    external_owner=models.ForeignKey("public_identity.ExternalIdentity",on_delete=models.PROTECT,null=True)
    staff_owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True)
    storage_alias=models.CharField(max_length=40,default="public_media")
    object_key=models.CharField(max_length=200,unique=True)
    original_name=models.CharField(max_length=150)
    mime=models.CharField(max_length=50)
    size=models.PositiveIntegerField()
    sha256=models.CharField(max_length=64)
    visibility=models.CharField(max_length=10,default="private")
    revision=models.PositiveIntegerField(default=1)
    deleted_at=models.DateTimeField(null=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=(models.Q(external_owner__isnull=False,staff_owner__isnull=True)|models.Q(external_owner__isnull=True,staff_owner__isnull=False)),name="media_exactly_one_owner")]

class UploadReceipt(models.Model):
    identity=models.ForeignKey("public_identity.ExternalIdentity",on_delete=models.PROTECT)
    key=models.CharField(max_length=120)
    fingerprint=models.CharField(max_length=64)
    asset=models.ForeignKey(MediaAsset,on_delete=models.PROTECT,null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["identity","key"],name="external_upload_receipt_unique")]

class MediaFolder(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    space=models.ForeignKey("publication.PublicationSpace",on_delete=models.PROTECT)
    external_owner=models.ForeignKey("public_identity.ExternalIdentity",on_delete=models.PROTECT)
    name=models.CharField(max_length=100)
    created_at=models.DateTimeField(auto_now_add=True)
    deleted_at=models.DateTimeField(null=True)
    revision=models.PositiveIntegerField(default=1)
    class Meta:
        constraints=[models.UniqueConstraint(models.F('space'),models.F('external_owner'),models.functions.Lower('name'),
            condition=models.Q(deleted_at=None),name='unique_active_media_folder')]

class InternalReceipt(models.Model):
    """Private evidence. Only the owning workflow exposes a download route."""
    name = models.CharField(max_length=150)
    mime = models.CharField(max_length=50)
    content = models.BinaryField()
