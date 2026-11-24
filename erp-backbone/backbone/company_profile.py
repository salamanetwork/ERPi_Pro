from io import BytesIO
import re
from xml.etree import ElementTree

from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from PIL import Image, ImageOps

from .access import audit
from .http import Problem, endpoint, string
from .models import CompanyProfile

MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_LOGO_PIXELS = 12_000_000
SVG_NS = "http://www.w3.org/2000/svg"
SVG_TAGS = frozenset({
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "defs", "linearGradient", "radialGradient", "stop", "clipPath", "title", "desc",
})
SVG_PAINT = frozenset({
    "color", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray",
    "stroke-dashoffset", "opacity", "clip-path", "clip-rule", "stop-color", "stop-opacity",
    "vector-effect", "display", "visibility",
})
SVG_ATTRS = SVG_PAINT | frozenset({
    "id", "viewBox", "width", "height", "preserveAspectRatio", "x", "y", "rx", "ry",
    "cx", "cy", "r", "x1", "x2", "y1", "y2", "fx", "fy", "fr", "d", "points",
    "transform", "gradientUnits", "gradientTransform", "spreadMethod", "offset", "clipPathUnits",
})
SVG_FRAGMENT = re.compile(r"url\(\s*#[A-Za-z_][\w.:-]*\s*\)", re.ASCII)
SVG_VALUE = re.compile(r"[-\w\s.,%+#()/]*", re.ASCII)


def invalid_logo():
    return Problem("invalid_company_logo")


def svg_value(key, value):
    if key in {"fill", "stroke", "clip-path"} and SVG_FRAGMENT.fullmatch(value):
        return value
    if not SVG_VALUE.fullmatch(value) or "url" in value.lower() or len(value) > 300_000:
        raise invalid_logo()
    return value


def clean_svg(raw):
    try:
        source = raw.decode("utf-8-sig")
        if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", source, re.IGNORECASE):
            raise invalid_logo()
        root = ElementTree.fromstring(source)
    except (UnicodeError, ElementTree.ParseError, ValueError):
        raise invalid_logo() from None
    if root.tag not in {f"{{{SVG_NS}}}svg", "svg"}:
        raise invalid_logo()

    count = 0

    def copy(node, depth):
        nonlocal count
        count += 1
        if count > 4000 or depth > 32:
            raise invalid_logo()
        tag = node.tag.removeprefix(f"{{{SVG_NS}}}")
        if tag not in SVG_TAGS:
            raise invalid_logo()
        safe = ElementTree.Element(tag)
        for key, value in node.attrib.items():
            if key in SVG_ATTRS:
                safe.set(key, svg_value(key, value.strip()))
            elif key not in {"version", "style"} and not key.startswith("data-"):
                raise invalid_logo()
        for declaration in node.attrib.get("style", "").split(";"):
            if not declaration.strip():
                continue
            name, colon, paint = declaration.partition(":")
            name = name.strip()
            if not colon or name not in SVG_PAINT:
                raise invalid_logo()
            safe.set(name, svg_value(name, paint.strip()))
        if tag in {"title", "desc"}:
            safe.text = node.text
        for child in node:
            safe.append(copy(child, depth + 1))
        return safe

    clean = copy(root, 0)
    clean.set("xmlns", SVG_NS)
    return ElementTree.tostring(clean, encoding="utf-8"), "image/svg+xml"


def normalize_logo(file):
    if file is None:
        raise invalid_logo()
    if file.size > MAX_LOGO_BYTES:
        raise Problem("company_logo_too_large", 413)
    raw = file.read(MAX_LOGO_BYTES + 1)
    if len(raw) > MAX_LOGO_BYTES:
        raise Problem("company_logo_too_large", 413)
    if raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
        try:
            with Image.open(BytesIO(raw), formats=("JPEG", "PNG")) as source:
                if source.width * source.height > MAX_LOGO_PIXELS or getattr(source, "is_animated", False):
                    raise invalid_logo()
                result = ImageOps.exif_transpose(source)
                result.thumbnail((512, 512), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                if source.format == "PNG":
                    result.convert("RGBA").save(buffer, "PNG")
                    return buffer.getvalue(), "image/png"
                result.convert("RGB").save(buffer, "JPEG", quality=88)
                return buffer.getvalue(), "image/jpeg"
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            raise invalid_logo() from None
    return clean_svg(raw)


def branding():
    row = CompanyProfile.objects.values("name", "logo_type", "deletion_policy", "revision").filter(pk=1).first()
    if row is None:
        return {"name": "", "logo_url": None, "deletion_policy": "soft", "revision": 0}
    version = row["revision"]
    return {"name": row["name"], "logo_url": f"/api/company-profile/logo?v={version}" if row["logo_type"] else None,
            "deletion_policy": row.get("deletion_policy") or "soft", "revision": version}


@never_cache
@endpoint("GET", "POST", admin=True)
def company_profile(request):
    if request.method == "GET":
        return branding()
    name = string(request.POST, "name", 120, required=False)
    revision = request.POST.get("revision", "")
    remove = request.POST.get("remove_logo", "0")
    deletion_policy = request.POST.get("deletion_policy")
    upload = request.FILES.get("logo")
    if not re.fullmatch(r"[0-9]{1,10}", revision) or remove not in {"0", "1"} or (upload and remove == "1"):
        raise Problem("invalid_company_profile")
    if deletion_policy is not None and deletion_policy not in {"soft", "permanent"}:
        raise Problem("invalid_company_profile")
    logo = normalize_logo(upload) if upload else None
    with transaction.atomic():
        row, _ = CompanyProfile.objects.select_for_update().get_or_create(pk=1)
        if int(revision) != row.revision:
            raise Problem("revision_conflict", 409)
        row.name = name
        if deletion_policy in {"soft", "permanent"}:
            row.deletion_policy = deletion_policy
        if logo:
            row.logo, row.logo_type = logo
        elif remove == "1":
            row.logo, row.logo_type = b"", ""
        row.revision += 1
        row.save(update_fields=["name", "logo", "logo_type", "deletion_policy", "revision"])
        audit(request.user, "company_profile.update", row.pk)
        return branding()


@never_cache
@endpoint("GET")
def company_logo(request):
    row = CompanyProfile.objects.filter(pk=1).first()
    version = request.GET.get("v")
    if not row or not row.logo_type or (version and version != str(row.revision)):
        raise Problem("not_found", 404)
    response = HttpResponse(bytes(row.logo), content_type=row.logo_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return response
