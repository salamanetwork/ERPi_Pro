from io import BytesIO
from uuid import uuid4

from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from PIL import Image, ImageOps, UnidentifiedImageError

from .access import audit
from .http import Problem, endpoint
from .models import ProfilePicture

MAX_UPLOAD = 2 * 1024 * 1024
MAX_PIXELS = 12_000_000


def picture_url(user_id):
    revision = ProfilePicture.objects.filter(user_id=user_id).values_list("revision", flat=True).first()
    return f"/api/profile/picture?v={revision}" if revision else None


def normalized_picture(file):
    if file is None or file.size > MAX_UPLOAD:
        raise Problem("invalid_profile_picture")
    try:
        with Image.open(file, formats=("JPEG", "PNG", "WEBP")) as source:
            if source.width * source.height > MAX_PIXELS or getattr(source, "is_animated", False):
                raise Problem("invalid_profile_picture")
            result = ImageOps.exif_transpose(source)
            result.thumbnail((384, 384), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            result.convert("RGB").save(buffer, "JPEG", quality=88)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise Problem("invalid_profile_picture") from None


@never_cache
@endpoint("GET", "POST", "DELETE")
def picture(request):
    user = request.user
    if request.method == "GET":
        row = ProfilePicture.objects.filter(user=user).first()
        version = request.GET.get("v")
        if row is None or (version and version != str(row.revision)):
            raise Problem("not_found", 404)
        response = HttpResponse(bytes(row.image), content_type="image/jpeg")
        response["X-Content-Type-Options"] = "nosniff"
        return response
    if request.method == "POST":
        image = normalized_picture(request.FILES.get("picture"))
        with transaction.atomic():
            row, _ = ProfilePicture.objects.update_or_create(
                user=user, defaults={"image": image, "revision": uuid4()},
            )
            audit(user, "profile.picture.update", user.pk)
        return {"picture_url": f"/api/profile/picture?v={row.revision}"}
    with transaction.atomic():
        removed, _ = ProfilePicture.objects.filter(user=user).delete()
        if removed:
            audit(user, "profile.picture.remove", user.pk)
    return {"picture_url": None}
