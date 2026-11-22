from io import BytesIO
import hashlib
import warnings
from uuid import uuid4
from pathlib import PurePath
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from PIL import Image,UnidentifiedImageError
from rest_framework.exceptions import ValidationError
from .models import MediaAsset

def metadata(row):
    return {"folder_id":str(row.folder_id) if row.folder_id else None,"id":str(row.pk),"space_id":row.space_id,"name":row.original_name,"mime":row.mime,"size":row.size,
            "staff_uploaded":bool(row.staff_owner_id and not row.external_owner_id),"visibility":row.visibility,"revision":row.revision,"created_at":row.created_at.isoformat()}

def validate(upload):
    if upload is None: raise ValidationError({"file":["Select an image or PDF."]})
    if upload.size>10*1024*1024: raise ValidationError({"file":["Maximum PDF size is 10 MB; images 5 MB."]})
    name=PurePath(upload.name.replace("\\","/")).name[:150]
    content=upload.read(10*1024*1024+1)
    suffix=PurePath(name).suffix.lower()
    if suffix==".pdf" and content.startswith(b"%PDF-") and b"%%EOF" in content[-2048:]:
        return name,"application/pdf",content
    if suffix not in {".png",".jpg",".jpeg",".webp"}: raise ValidationError({"file":["Use PNG, JPEG, WebP or PDF. SVG is not enabled."]})
    if len(content)>5*1024*1024: raise ValidationError({"file":["Maximum image size is 5 MB."]})
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error",Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as picture:
                if picture.width*picture.height>20_000_000 or picture.format not in {"PNG","JPEG","WEBP"}: raise ValueError()
                picture.verify()
            with Image.open(BytesIO(content)) as picture:
                # Re-encode to drop EXIF and unexpected trailing payloads.
                fmt=picture.format;out=BytesIO();picture.save(out,format=fmt)
                clean=out.getvalue()
                if len(clean)>5*1024*1024: raise ValueError()
                return name,Image.MIME[fmt],clean
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombWarning,Image.DecompressionBombError):
        raise ValidationError({"file":["Invalid or oversized image."]})

def create(site,name,mime,content,*,identity=None,staff=None,visibility="private"):
    storage=storages["public_media"]
    key="assets/"+uuid4().hex
    actual=storage.save(key,ContentFile(content))
    try:
        return MediaAsset.objects.create(space=site,external_owner=identity,staff_owner=staff,object_key=actual,
            original_name=name,mime=mime,size=len(content),sha256=hashlib.sha256(content).hexdigest(),visibility=visibility)
    except Exception:
        storage.delete(actual)
        raise


def public_image(space_id,asset_id):
    return MediaAsset.objects.filter(pk=asset_id,space_id=space_id,deleted_at=None,visibility="public",external_owner=None,
        staff_owner__isnull=False,mime__in=("image/png","image/jpeg","image/webp")).exists()

def store_internal_receipt(validated):
    from .models import InternalReceipt
    name, mime, content = validated
    return InternalReceipt.objects.create(name=name, mime=mime, content=content)

def download_internal_receipt(receipt):
    from django.http import FileResponse
    response = FileResponse(BytesIO(bytes(receipt.content)), as_attachment=True, filename=receipt.name, content_type=receipt.mime)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
