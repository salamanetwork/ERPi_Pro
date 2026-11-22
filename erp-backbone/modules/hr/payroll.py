import calendar
import hashlib
import json
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from accounting.models import Account
from accounting.services import ledger_lock, period_for, posted_entry
from accounting.views import day_field
from backbone.access import audit, has_permission
from backbone.models import Site
from backbone.http import Problem, body, endpoint, identifier, once, require, string
from purchasing.money import decimal_value
from .access import allowed_rows, one, own_or_scoped
from .models import Employee, Leave, Payment, PayrollInput, PayrollRun, Payslip

ZERO = Decimal("0.00")
EARNINGS = ("bonus", "extra")
DEDUCTIONS = ("deduction", "adjustment")


def run_for(user, pk, *permissions, lock=False):
    rows = PayrollRun.objects.all()
    if lock:
        rows = rows.select_for_update()
    row = one(rows, pk)
    if not any(has_permission(user, permission, row.site_id) for permission in permissions):
        raise Problem("not_found", 404)
    return row


def revision(row, data):
    if identifier(data.get("revision"), "revision") != row.revision:
        raise Problem("revision_conflict", 409)


def aggregates(run):
    totals = dict(gross=ZERO, deduction=ZERO, net=ZERO)
    for gross, deduction, net in run.slips.values_list("gross", "deduction", "net"):
        totals["gross"] += gross
        totals["deduction"] += deduction
        totals["net"] += net
    paid = sum(run.payments.values_list("amount", flat=True), ZERO)
    outstanding = totals["net"] - paid if run.state in ("approved", "posted") else ZERO
    return {**{key: format(value, ".2f") for key, value in totals.items()},
            "paid": format(paid, ".2f"), "outstanding": format(outstanding, ".2f")}


def run_data(run, user, *, include_payments=False):
    preparer = run.prepared_by_id == user.pk
    can_pay = has_permission(user, "payroll.pay", run.site_id)
    can_post = has_permission(user, "payroll.post", run.site_id)
    return {"id": run.pk, "site_id": run.site_id, "month": run.month.isoformat(), "state": run.state,
            "revision": run.revision, "entry_id": run.entry_id, "reversal_id": run.reversal_id,
            "calculated": bool(run.digest),
            "preview": run.preview if run.state == "submitted" else None,
            "review_note": run.review_note if preparer or has_permission(user, "payroll.approve", run.site_id) else "",
            **aggregates(run),
            "can_prepare": preparer and has_permission(user, "payroll.prepare", run.site_id),
            "can_approve": not preparer and has_permission(user, "payroll.approve", run.site_id),
            "can_post": can_post,
            "can_reverse": can_post and not preparer,
            "can_pay": can_pay,
            "payments": [{"amount": str(payment.amount), "reference": payment.reference,
                          "posting_date": payment.entry.date.isoformat()}
                         for payment in run.payments.select_related("entry").order_by("pk")] if can_pay and include_payments else [],
            "can_review_inputs": has_permission(user, "payroll.approve", run.site_id),
            "can_read_inputs": has_permission(user, "payroll.prepare", run.site_id)}


@never_cache
@endpoint("GET", "POST", module="payroll")
def runs(request):
    user = request.user
    if request.method == "GET":
        query = PayrollRun.objects.select_related("prepared_by")
        rows = query.none()
        for permission in ("prepare", "approve", "post", "pay", "summary"):
            rows |= allowed_rows(query, user, "payroll." + permission)
        if "page_size" in request.GET:
            from backbone.services.pagination import operational_page
            return operational_page(request,rows.order_by("-month","-pk"),lambda row:run_data(row,user,include_payments=True),['site__name', 'state'])
        return {"items": [run_data(row, user, include_payments=True) for row in rows.order_by("-month", "-pk")]}
    data = body(request)
    site_id = identifier(data.get("site_id"), "site")
    require(user, "payroll.prepare", site_id)
    month = day_field(data, "month")
    if month.day != 1:
        raise Problem("hr_month_start")

    def create():
        from backbone.site_admin import require_active_site

        require_active_site(site_id, lock=True)
        if PayrollRun.objects.filter(site_id=site_id, month=month).exclude(state="reversed").exists():
            raise Problem("payroll_run_exists", 409)
        row = PayrollRun.objects.create(site_id=site_id, month=month, prepared_by=user)
        audit(user, "payroll.run.create", row.pk)
        return run_data(row, user)

    return once(request, "payroll.run.create", data, create)


def input_data(row):
    return {"id": row.pk, "employee_id": row.employee_id, "employee": row.employee.name, "kind": row.kind,
            "amount": str(row.amount), "reason": row.reason, "reference": row.reference, "approved": bool(row.approved_by_id)}


@never_cache
@endpoint("GET", "POST", module="payroll")
def inputs(request, pk):
    user = request.user
    row = run_for(user, pk, "payroll.prepare", "payroll.approve")
    if request.method == "GET":
        if row.state not in ("draft", "submitted"):
            raise Problem("hr_invalid_state", 409)
        return {"items": [input_data(item) for item in row.inputs.select_related("employee").order_by("pk")],
                "employees": list(Employee.objects.filter(site_id=row.site_id).order_by("number").values("id","number","name"))}
    data = body(request)

    def create():
        run = run_for(user, pk, "payroll.prepare", "payroll.approve", lock=True)
        if run.state != "draft":
            raise Problem("hr_invalid_state", 409)
        action = data.get("action")
        if action == "approve":
            require(user, "payroll.approve", run.site_id)
            if user.pk == run.prepared_by_id:
                raise Problem("hr_separate_reviewer", 403)
            item = one(run.inputs.select_related("employee"), identifier(data.get("id"), "input"))
            item.approved_by = user
            item.save(update_fields=["approved_by"])
        else:
            require(user, "payroll.prepare", run.site_id)
            if run.prepared_by_id != user.pk or action not in ("add", "remove"):
                raise Problem("forbidden", 403)
            if action == "remove":
                item = one(run.inputs.select_related("employee"), identifier(data.get("id"), "input"))
                result = input_data(item)
                item.delete()
                run.digest = ""
                run.save(update_fields=["digest"])
                audit(user, "payroll.input.remove", pk)
                return result
            employee = one(Employee.objects.filter(site_id=run.site_id), identifier(data.get("employee_id"), "employee"))
            kind = data.get("kind")
            amount = decimal_value(data.get("amount"), 14, 2, "amount")
            if kind not in EARNINGS + DEDUCTIONS + ("review",) or (kind == "review" and amount != ZERO) or (kind != "review" and amount <= ZERO):
                raise Problem("payroll_invalid_input")
            item, fresh = PayrollInput.objects.get_or_create(run=run, employee=employee, reference=string(data,"reference",100),
                defaults={"kind":kind, "amount":amount, "reason":string(data,"reason",500)})
            if not fresh:
                raise Problem("payroll_input_exists", 409)
        run.digest = ""
        run.save(update_fields=["digest"])
        audit(user, "payroll.input." + action, pk)
        return input_data(item)

    return once(request, "payroll.input", {"run":pk, **data}, create)


def projection(run):
    end = run.month.replace(day=calendar.monthrange(run.month.year, run.month.month)[1])
    employees = list(Employee.objects.filter(site_id=run.site_id, joined__lte=end)
        .filter(Q(ended__isnull=True) | Q(ended__gte=run.month)).order_by("pk"))
    inputs_by_employee = defaultdict(list)
    issues = []
    eligible = {employee.pk for employee in employees}
    for item in run.inputs.order_by("pk"):
        inputs_by_employee[item.employee_id].append(item)
        if item.employee_id not in eligible or not item.approved_by_id:
            issues.append({"employee":item.employee.number,"code":"payroll_input_pending"})
    slips = []
    for employee in employees:
        term = employee.terms.filter(effective__lte=end).select_related("center__center_scope").order_by("-effective").first()
        center = getattr(term.center, "center_scope", None) if term else None
        if not term or not center or not center.active or center.site_id != run.site_id:
            issues.append({"employee":employee.number,"code":"payroll_missing_terms"})
            continue
        unpaid = list(Leave.objects.filter(employee=employee, paid=False, state="approved",
                                           start__lte=end, end__gte=run.month).values_list("id", flat=True).order_by("id"))
        items = inputs_by_employee[employee.pk]
        if (unpaid or employee.joined > run.month or employee.ended and employee.ended < end or term.effective > run.month
                ) and not any(i.kind in ("adjustment", "review") and i.approved_by_id for i in items):
            issues.append({"employee":employee.number,"code":"payroll_manual_review_required"})
        extra = sum((i.amount for i in items if i.kind in EARNINGS), ZERO)
        deduction = sum((i.amount for i in items if i.kind in DEDUCTIONS), ZERO)
        gross = term.basic + term.allowance + extra
        if gross < deduction:
            issues.append({"employee":employee.number,"code":"payroll_negative_net"})
            continue
        slips.append({"employee_id":employee.pk,"center_id":term.center_id,"gross":gross,"deduction":deduction,"net":gross-deduction,
            "snapshot":{"name":employee.name,"number":employee.number,"department":employee.department,
                "center":term.center.name,"basic":str(term.basic),"allowance":str(term.allowance),"extra":str(extra),
                "effective":term.effective.isoformat(),"unpaid_requests":unpaid,"inputs":[
                    {"id":i.pk,"kind":i.kind,"amount":str(i.amount),"reason":i.reason,"reference":i.reference,
                     "approved_by":i.approved_by_id} for i in items]}})
    if not employees:
        issues.append({"employee":"","code":"payroll_no_employees"})
    if slips and not any(slip["gross"] for slip in slips):
        issues.append({"employee":"","code":"payroll_zero_total"})
    digest = hashlib.sha256(json.dumps(slips, sort_keys=True, default=str).encode()).hexdigest()
    return slips, issues, digest


def ledger_day(data, run):
    day = day_field(data, "posting_date")
    ledger = ledger_lock()
    if ledger.currency != "AED" or not ledger.activated_at or not ledger.cutoff or day < ledger.cutoff or day < run.month:
        raise Problem("payroll_period_unavailable", 409)
    return ledger, period_for(ledger, day), day


def account(ledger, code, kind):
    row = Account.objects.filter(ledger=ledger, code=code, detail=True, active=True, kind=kind).first()
    if row is None or code.startswith("PAY-") and not row.control:
        raise Problem("finance_account_unavailable", 409)
    return row


def payroll_entry(row, user, data):
    ledger, period, day = ledger_day(data, row)
    totals = defaultdict(lambda: ZERO)
    for slip in row.slips.all():
        for key, code in (("basic","PAY-BASIC"),("allowance","PAY-ALLOW"),("extra","PAY-EXTRA")):
            totals[code, slip.center_id] += Decimal(slip.snapshot[key])
        totals["PAY-NET", None] -= slip.net
        totals["PAY-DEDUCT", None] -= slip.deduction
    accounts = {code:account(ledger,code,"liability" if code in ("PAY-NET","PAY-DEDUCT") else "expense")
                for code in {code for code, _ in totals}}
    from accounting.models import Reference

    centers = Reference.objects.in_bulk([center for _,center in totals if center])
    lines = [(accounts[code], amount, row.site_id, centers.get(center))
             for (code,center),amount in totals.items()]
    return posted_entry(ledger, period, day, user.get_username(), f"Payroll batch #{row.pk}", "hr_payroll", lines)


@never_cache
@endpoint("POST", module="payroll")
def advance(request, pk):
    user = request.user
    data = body(request)
    action = data.get("action")
    permissions = {"calculate":"prepare","submit":"prepare","approve":"approve","return":"approve",
                   "post":"post","pay":"pay","reverse":"post"}
    if not isinstance(action,str) or action not in permissions:
        raise Problem("invalid_action")
    permission = "payroll." + permissions[action]
    run_for(user, pk, permission)

    def create():
        row = run_for(user, pk, permission, lock=True)
        revision(row,data)
        wanted = {"calculate":"draft","submit":"draft","approve":"submitted","return":"submitted",
                  "post":"approved","pay":"posted","reverse":"posted"}
        if row.state != wanted[action]:
            raise Problem("hr_invalid_state",409)
        if action in ("calculate","submit") and row.prepared_by_id != user.pk:
            raise Problem("forbidden",403)
        if action in ("approve", "return", "reverse") and row.prepared_by_id == user.pk:
            raise Problem("hr_separate_reviewer",403)
        if action == "return":
            row.review_note = string(data,"reason",500)
            row.state, row.digest = "draft", ""
        elif action in ("calculate","submit","approve"):
            Site.objects.select_for_update().get(pk=row.site_id)
            if action == "approve":
                # Serialize cross-site reservations; employee rows also serialize source edits.
                ledger_lock()
            list(Employee.objects.select_for_update().filter(site_id=row.site_id).order_by("pk").values_list("pk",flat=True))
            slips, issues, digest = projection(row)
            if action == "calculate":
                row.digest = "" if issues else digest
                row.preview = {field: format(sum((slip[field] for slip in slips), ZERO), ".2f")
                               for field in ("gross", "deduction", "net")}
                row.revision += 1
                row.save(update_fields=["digest", "preview", "revision"])
                return {**run_data(row, user), "issues": issues, "preview": row.preview}
            if issues or not row.digest or digest != row.digest:
                raise Problem("payroll_recalculate",409)
            if action == "submit":
                row.state = "submitted"
            else:
                if Payslip.objects.filter(employee_id__in=[s["employee_id"] for s in slips],month=row.month,reserved=True).exists():
                    raise Problem("payroll_duplicate_employee",409)
                Payslip.objects.bulk_create([Payslip(run=row,month=row.month,**slip) for slip in slips])
                row.approved_by, row.state = user, "approved"
                row.review_note = string(data, "reason", 500, required=False)
        elif action == "post":
            row.entry, row.state = payroll_entry(row,user,data), "posted"
        elif action == "pay":
            reference = string(data,"reference",100).upper()
            amount = decimal_value(data.get("amount"), 18,2,"amount")
            if amount <= ZERO or amount > Decimal(aggregates(row)["outstanding"]):
                raise Problem("payroll_payment_exceeds",409)
            ledger, period, day = ledger_day(data,row)
            if day < row.entry.date:
                raise Problem("finance_before_original",409)
            if Payment.objects.filter(reference=reference).exists():
                raise Problem("payroll_payment_reference",409)
            payable = row.entry.lines.get(account__code="PAY-NET").account
            bank = account(ledger,"1100","asset")
            if bank.control:
                raise Problem("finance_account_unavailable", 409)
            entry = posted_entry(ledger,period,day,user.get_username(),f"Payroll payment batch #{row.pk}", "hr_pay",
                [(payable,amount,row.site_id,None),(bank,-amount,row.site_id,None)])
            Payment.objects.create(run=row,entry=entry,amount=amount,reference=reference)
        else:
            reason = string(data,"reason",500)
            if row.payments.exists():
                raise Problem("payroll_reverse_paid",409)
            ledger, period, day = ledger_day(data,row)
            if day < row.entry.date:
                raise Problem("finance_before_original",409)
            row.reversal = posted_entry(ledger,period,day,user.get_username(),f"Payroll reversal batch #{row.pk}", "hr_reversal",
                [(line.account,-line.amount,line.site_id,line.cost_center) for line in row.entry.lines.select_related("account","cost_center")],
                reversal_of=row.entry)
            row.state, row.review_note = "reversed",reason
            row.slips.update(reserved=False)
        row.revision += 1
        row.save()
        audit(user,"payroll.run." + action,row.pk)
        return run_data(row,user)

    return once(request,"payroll.run.action",{"run":pk,**data},create)


def visible_slips(user):
    return own_or_scoped(Payslip.objects.filter(run__state__in=("approved","posted","reversed")).select_related("run"),
                         user,"payroll.payslip","payroll.payslip.self","employee__user_id","run__site_id")


def slip_data(row):
    return {"id":row.pk,"run_id":row.run_id,"state":row.run.state,"month":row.month.isoformat(),
            **row.snapshot, "gross":str(row.gross),"deduction":str(row.deduction),"net":str(row.net)}


@never_cache
@endpoint("GET", module="payroll")
def slips(request):
    if "page_size" in request.GET:
        from backbone.services.pagination import operational_page
        return operational_page(request,visible_slips(request.user).order_by("-month","pk"),slip_data,['employee__name', 'run__state'])
    return {"items":[slip_data(row) for row in visible_slips(request.user).order_by("-month","pk")]}


@never_cache
@endpoint("GET", module="payroll")
def slip(request, pk):
    return slip_data(one(visible_slips(request.user),pk))


@never_cache
@endpoint("GET", module="payroll")
def print_slip(request, pk):
    row = one(visible_slips(request.user),pk)
    language = request.GET.get("language","en")
    response = render(request,"hr/payslip.html",{"slip":slip_data(row),"arabic":language=="ar"})
    response["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, noarchive"
    return response
