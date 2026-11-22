import csv
import hashlib
import io
import re
from collections import Counter

from django.views.decorators.cache import never_cache

from backbone.access import audit
from backbone.http import Problem, endpoint, once
from backbone.models import Site
from backbone.registry import enabled_modules
from inventory.models import Warehouse
from purchasing.orders import CURRENCIES, supplier_values
from .csv_export import MASTERS, check_module, csv_response

LIMIT = 1_000
MAX_BYTES = 2 * 1024 * 1024
FIELDS = {
    "asset": ("tag", "name", "site_name"),
    "unit": ("code", "name"),
    "item": ("code", "name", "base_unit_code"),
    "warehouse": ("code", "name", "site_name"),
    "location": ("code", "name", "site_name", "warehouse_code"),
    "supplier": ("code", "name", "currency_default", "legal_name", "contact", "tax_id", "active"),
    "customer": ("code", "name", "legal_name", "contact", "billing", "shipping", "tax_id", "terms", "active"),
}
REQUIRED = {"tag", "code", "name", "base_unit_code", "site_name", "warehouse_code", "currency_default"}


class InvalidImport(Exception):
    def __init__(self, result, status=400):
        self.result = result
        self.status = status


def issue(row, column, reason, **details):
    return {"row": row, "column": column, "reason": reason, **details}


def invalid(errors, row_count=0, status=400):
    return InvalidImport({"row_count": row_count, "errors": errors}, status)


def raw_csv(request):
    upload = request.FILES.get("file")
    if upload is None:
        raise invalid([issue(1, "file", "file_required")])
    if upload.size > MAX_BYTES:
        raise invalid([issue(1, "file", "file_size_limit", limit=MAX_BYTES)])
    return upload.read()


def csv_rows(raw, kind):
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise invalid([issue(1, "file", "utf8_required")]) from None
    reader = csv.reader(io.StringIO(source, newline=""), strict=True)
    number = 1
    try:
        header = [value.strip() for value in next(reader, [])]
        columns = FIELDS[kind]
        errors = [issue(1, key, "duplicate_header") for key, count in Counter(header).items() if count > 1]
        errors += [issue(1, key or "header", "unknown_header") for key in dict.fromkeys(header) if key not in columns]
        errors += [issue(1, key, "missing_header") for key in columns if key in REQUIRED and key not in header and (kind != "asset" or key != "site_name")]
        if errors:
            raise invalid(errors)
        count = 0
        while True:
            number = reader.line_num + 1
            cells = next(reader, None)
            if cells is None:
                break
            if not any(value.strip() for value in cells):
                continue
            count += 1
            if count > LIMIT:
                raise invalid([issue(number, "file", "row_limit", limit=LIMIT)], count)
            if len(cells) != len(header):
                column = header[len(cells)] if len(cells) < len(header) else "extra_columns"
                yield number, {}, [issue(number, column, "column_count", expected=len(header), actual=len(cells))]
            else:
                yield number, dict(zip(header, cells)), []
        if not count:
            raise invalid([issue(2, "file", "no_rows")])
    except csv.Error as failure:
        raise invalid([issue(number, "file", "malformed_csv", detail=str(failure))]) from None


def prepare_row(kind, model, number, cells):
    from inventory.models import Unit, Warehouse

    values, errors = {}, []

    def reject(column, reason, **details):
        errors.append(issue(number, column, reason, **details))

    for column in FIELDS[kind]:
        value = cells.get(column, "").strip()
        if not value and column in REQUIRED and (kind != "asset" or column != "site_name"):
            reject(column, "required_value")
            continue
        if "\x00" in value:
            reject(column, "invalid_text")
            continue
        if column == "active":
            if value.lower() not in ("", "true", "false"):
                reject(column, "invalid_boolean")
            values[column] = value.lower() != "false"
        elif column == "terms":
            if value and (not re.fullmatch(r"[0-9]{1,3}", value) or int(value) > 365):
                reject(column, "invalid_terms")
            else:
                values[column] = int(value) if value else 30
        elif column == "currency_default":
            values[column] = value.upper()
            if values[column] not in CURRENCIES:
                reject(column, "unsupported_currency", detail=", ".join(CURRENCIES))
        elif column in ("base_unit_code", "site_name", "warehouse_code"):
            continue
        else:
            length = model._meta.get_field(column).max_length
            if column == "code":
                value = value.upper()
                if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", value):
                    reject(column, "invalid_code")
            if len(value) > length:
                reject(column, "too_long", limit=length)
            values[column] = value

    def reference(column, queryset, field):
        if any(error["column"] == column for error in errors):
            return None
        found = queryset.values_list("pk", flat=True).first()
        if found is None:
            reject(column, "unknown_reference", detail=cells[column].strip())
        else:
            values[field] = found
        return found

    if kind == "item":
        reference("base_unit_code", Unit.objects.filter(code=cells.get("base_unit_code", "").strip().upper()), "base_unit_id")
    if kind == "asset" and cells.get("site_name", "").strip():
        reference("site_name", Site.objects.filter(active=True, name=cells["site_name"].strip()), "site_id")
    if kind in ("warehouse", "location"):
        site = reference("site_name", Site.objects.filter(active=True, name=cells.get("site_name", "").strip()), "site_id")
        if kind == "location":
            values.pop("site_id", None)
            if site is not None:
                reference("warehouse_code", Warehouse.objects.filter(
                    site_id=site, active=True, code=cells.get("warehouse_code", "").strip().upper()), "warehouse_id")
    if kind == "supplier" and not errors:
        values = supplier_values(values)
    return values, errors


def inspect(raw, kind, model):
    entries, errors, seen = [], [], set()
    count = 0
    for number, cells, row_errors in csv_rows(raw, kind):
        count += 1
        if row_errors:
            errors.extend(row_errors)
            continue
        values, row_errors = prepare_row(kind, model, number, cells)
        if row_errors:
            errors.extend(row_errors)
            continue
        from backbone.site_admin import require_active_site

        site_ids = {values["site_id"] for _, _, values in entries if values.get("site_id") is not None}
        if kind == "location":
            warehouse_ids = {values["warehouse_id"] for _, _, values in entries}
            site_ids.update(Warehouse.objects.filter(pk__in=warehouse_ids).values_list("site_id", flat=True))
        for site_id in sorted(site_ids):
            require_active_site(site_id, lock=True)
        if kind == "location" and Warehouse.objects.filter(pk__in=warehouse_ids, active=False).exists():
            raise Problem("invalid_warehouse", 409)
        key = "tag" if kind == "asset" else "code"
        scope = {"warehouse": ("site_id",), "location": ("warehouse_id",)}.get(kind, ())
        match = {field: values[field] for field in (key, *scope)}
        identity = tuple(match.items())
        if identity in seen:
            errors.append(issue(number, key, "duplicate_in_file", detail=values[key]))
        elif model.objects.filter(**match).exists():
            errors.append(issue(number, key, "code_exists", detail=values[key]))
        else:
            entries.append((number, match, values))
        seen.add(identity)
    return entries, {"row_count": count, "errors": errors}


@never_cache
@endpoint("GET", "POST", admin=True)
def imports(request, kind, operation):
    if kind not in FIELDS:
        raise Problem("not_found", 404)
    module, model, _ = MASTERS[kind]
    check_module(module, enabled_modules())
    if request.method == "GET" and operation == "template":
        return csv_response(kind + "-template", FIELDS[kind], ())
    if request.method != "POST" or operation not in ("preview", "commit"):
        raise Problem("not_found", 404)

    def commit():
        entries, result = inspect(raw, kind, model)
        if result["errors"]:
            raise InvalidImport(result)
        from backbone.site_admin import require_active_site

        site_ids = {values["site_id"] for _, _, values in entries if values.get("site_id") is not None}
        if kind == "location":
            warehouse_ids = {values["warehouse_id"] for _, _, values in entries}
            site_ids.update(Warehouse.objects.filter(pk__in=warehouse_ids).values_list("site_id", flat=True))
        for site_id in sorted(site_ids):
            require_active_site(site_id, lock=True)
        if kind == "location" and Warehouse.objects.filter(pk__in=warehouse_ids, active=False).exists():
            raise Problem("invalid_warehouse", 409)
        key = "tag" if kind == "asset" else "code"
        for number, match, values in entries:
            _, created = model.objects.get_or_create(**match, defaults=values)
            if not created:
                raise invalid([issue(number, key, "code_exists", detail=values[key])], result["row_count"], 409)
        audit(request.user, "csv.import", f"{kind} / {len(entries)}")
        return {**result, "imported": len(entries)}

    def preview_page(result):
        if "page_size" not in request.GET:return result
        from backbone.services.pagination import paginate_items
        errors=result["errors"]
        paged=paginate_items(request,errors,["row","column","reason","detail"])
        return {**result,**paged,"errors":paged["results"],"total_errors":len(errors)}
    try:
        raw = raw_csv(request)
        if operation == "preview":
            return preview_page(inspect(raw, kind, model)[1])
        return once(request, "csv.import." + kind, {"sha256": hashlib.sha256(raw).hexdigest()}, commit)
    except InvalidImport as failure:
        return preview_page(failure.result) if operation=="preview" else failure.result, failure.status
