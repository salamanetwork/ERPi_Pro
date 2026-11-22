from django.core.paginator import Paginator
from django.db.models import Q

from backbone.http import Problem, string


ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def positive_filter(params, name):
    value = params.get(name, "").strip().translate(ARABIC_DIGITS)
    if not value:
        return None
    if not value.isascii() or not value.isdecimal() or len(value) > 18 or int(value) < 1:
        raise Problem(f"invalid_{name}")
    return int(value)


def search_rows(rows, params, fields, *, number_fields=()):
    term = string(params, "q", 120, required=False)
    if term:
        match = Q()
        for field in fields:
            match |= Q(**{field + "__icontains": term.translate(ARABIC_DIGITS) if field in number_fields else term})
        rows = rows.filter(match)
    return rows


def directory_rows(rows, params):
    rows = search_rows(rows, params, ("code", "name"))
    if record := positive_filter(params, "record"):
        rows = rows.filter(pk=record)
    status = params.get("status", "")
    if status not in ("", "active", "archived"):
        raise Problem("invalid_status")
    return rows.filter(active=status == "active") if status else rows


def paginate(request, rows, serialize):
    size = positive_filter(request.GET, "page_size") or 25
    if size > 100:
        raise Problem("invalid_page_size")
    page = Paginator(rows, size).get_page(positive_filter(request.GET, "page") or 1)

    def url(number):
        args = request.GET.copy()
        args["page"] = number
        return request.path + "?" + args.urlencode()

    return {
        "count": page.paginator.count, "page": page.number, "page_size": size,
        "next": url(page.next_page_number()) if page.has_next() else None,
        "previous": url(page.previous_page_number()) if page.has_previous() else None,
        "results": [serialize(row) for row in page],
    }

def operational_page(request,rows,serialize,fields):
    """Opt-in numbered pagination preserves the older items/cursor contract."""
    result=paginate(request,search_rows(rows,request.GET,fields),serialize)
    return {**result,'items':result['results'],'next_before':None}

def paginate_items(request, items, searchable):
    """Page an already-authorized computed report after calculating full totals."""
    term=string(request.GET,"q",150,required=False).casefold()
    filtered=[row for row in items if not term or any(term in str(row.get(k,'')).casefold() for k in searchable)]
    result=paginate(request,filtered,lambda row:row)
    return {**result,"items":result["results"]}
