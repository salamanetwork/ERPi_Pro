import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation
from django.db.models import Q, Sum, F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from backbone.access import audit, has_permission, scopes, visible
from backbone.http import body, endpoint, identifier, once, Problem, require, string
from backbone.module_events import publish
from backbone.registry import enabled_modules
from backbone.services.pagination import paginate, positive_filter, search_rows
from hr.models import Employee
from projects.access import accessible
from accounting.centers import center_choices, center_for_site
from accounting.models import Account, Ledger
from accounting.services import ledger_lock, period_for, posted_entry
from media_service.services import validate, store_internal_receipt, download_internal_receipt
from .models import ExpenseClaim, ExpenseLine, Settlement

CATEGORIES = ("travel","meals","lodging","supplies","other")
REVIEW = ("delegate","approve","post","reimburse")
PERMISSIONS = dict(submit="create",cancel="create",approve="approve",reject="approve",post="post",reimburse="reimburse")
STATES = dict(submit=("draft",),cancel=("draft","submitted","approved","rejected"),approve=("submitted",),
              reject=("submitted",),post=("approved",),reimburse=("posted","partially_reimbursed"))

def rows_for(user):
    rows=visible(ExpenseClaim.objects.all(),user,"expenses.view")
    roles=set().union(*(scopes(user,"expenses."+r) for r in REVIEW))
    if None not in roles:
        rows=rows.filter(Q(employee__user=user)|Q(site_id__in=roles))
    return rows.filter(Q(project=None)|Q(project__in=accessible(user)))

def get_claim(user,pk,lock=False):
    rows=rows_for(user)
    if lock:
        rows=rows.select_for_update(of=("self",))
    row=rows.filter(pk=pk).first()
    if not row:
        raise Problem("not_found",404)
    return row

def authorize(user,row,action):
    require(user,"expenses."+action,row.site_id)
    if action=="create" and row.employee.user_id!=user.pk:
        require(user,"expenses.delegate",row.site_id)
    if action=="approve" and user.pk in (row.employee.user_id,row.creator_id):
        raise Problem("forbidden",403)

def version(row,data):
    if type(data.get("revision")) is not int or row.revision!=data["revision"]:
        raise Problem("revision_conflict",409)

def money(value,field):
    try:
        if isinstance(value,bool) or not isinstance(value,(int,str)) or len(str(value))>20:raise ValueError()
        result=Decimal(value)
        if not result.is_finite() or result<0 or result>=10**14 or result!=result.quantize(Decimal(".01")):raise ValueError()
        return result
    except (InvalidOperation,ValueError):
        raise Problem("invalid_amount",field=field)

def day(value,field):
    try:
        return date.fromisoformat(value)
    except (TypeError,ValueError):
        raise Problem("invalid_date",field=field)

def changed(user,row,action):
    audit(user,"expenses."+action,row.pk)
    publish("expenses","claim.changed",row.pk,f"expenses:{row.pk}:{row.revision}")

def header(row):
    return dict(id=row.pk,employee_id=row.employee_id,employee=row.employee.name,site_id=row.site_id,project_id=row.project_id,
                cost_center_id=row.cost_center_id,currency=row.currency,method=row.method,status=row.status,
                gross=str(row.gross),amount_paid=str(row.amount_paid),balance_due=format(row.balance_due,".2f"),
                revision=row.revision,entry_id=row.entry_id,review_note=row.review_note)

def detail(row,user):
    def permitted(action):
        perm=PERMISSIONS[action]
        if row.status not in STATES[action] or not has_permission(user,"expenses."+perm,row.site_id):return False
        if perm=="approve" and user.pk in (row.creator_id,row.employee.user_id):return False
        if perm=="create" and user.pk!=row.employee.user_id and not has_permission(user,"expenses.delegate",row.site_id):return False
        return action!="reimburse" or row.method=="out_of_pocket"
    return dict(**header(row),actions=[action for action in PERMISSIONS if permitted(action)],lines=[dict(id=x.pk,category=x.category,date=x.date.isoformat(),description=x.description,
            net=str(x.net),tax=str(x.tax),gross=str(x.net+x.tax),recoverable=x.recoverable,receipt=bool(x.receipt_id))
            for x in row.lines.order_by("id")],settlements=[dict(id=x.pk,reference=x.reference,date=x.date.isoformat(),
            amount=str(x.amount),entry_id=x.entry_id) for x in row.settlements.order_by("id")])

@endpoint("GET",module="expenses")
def choices(request):
    site=positive_filter(request.GET,"site")
    if not site:raise Problem("invalid_site",field="site")
    require(request.user,"expenses.view",site)
    require(request.user,"expenses.create",site)
    staff=Employee.objects.filter(site_id=site,site__active=True,archived_at=None,ended=None)
    if not has_permission(request.user,"expenses.delegate",site):
        staff=staff.filter(user=request.user)
    return dict(employees=list(staff.order_by("name").values("id","name")),centers=center_choices(site),
                projects=list(accessible(request.user).filter(site_id=site,state="active").values("id","name")) if "projects" in enabled_modules() else [],
                currency=Ledger.objects.get(pk=1).currency)

def write_claim(user,data,row=None):
    if row:
        version(row,data)
        if row.status!="draft":raise Problem("invalid_transition",409)
    site=identifier(data.get("site_id"),"site_id")
    require(user,"expenses.view",site)
    require(user,"expenses.create",site)
    from backbone.site_admin import require_active_site
    require_active_site(site,lock=True)
    employee=Employee.objects.filter(pk=identifier(data.get("employee_id"),"employee_id"),site_id=site,site__active=True,archived_at=None,ended=None).first()
    if not employee or employee.user_id!=user.pk and not has_permission(user,"expenses.delegate",site):
        raise Problem("forbidden",403)
    if row and (site!=row.site_id or employee.pk!=row.employee_id):raise Problem("invalid_employee",field="employee_id")
    project=identifier(data.get("project_id"),"project_id",optional=True)
    if project and ("projects" not in enabled_modules() or not accessible(user).filter(pk=project,site_id=site,state="active").exists()):
        raise Problem("invalid_project",field="project_id")
    center=center_for_site(data.get("cost_center_id"),site)
    currency=string(data,"currency",3).upper()
    if currency!=Ledger.objects.get(pk=1).currency:raise Problem("invalid_currency",field="currency")
    method=data.get("method")
    if method not in ("company_paid","out_of_pocket"):raise Problem("invalid_method",field="method")
    lines=data.get("lines")
    if not isinstance(lines,list) or not 1<=len(lines)<=100:raise Problem("invalid_lines",field="lines")
    row=row or ExpenseClaim(creator=user,employee=employee,site_id=site)
    old={x.pk:x for x in row.lines.all()} if row.pk else {}
    prepared=[]
    for i,line in enumerate(lines):
        prefix=f"lines.{i}."
        if not isinstance(line,dict) or line.get("category") not in CATEGORIES:raise Problem("invalid_category",field=prefix+"category")
        net=money(line.get("net"),prefix+"net")
        tax=money(line.get("tax","0"),prefix+"tax")
        if not net:raise Problem("invalid_amount",field=prefix+"net")
        obj=old.pop(identifier(line["id"],prefix+"id"),None) if "id" in line else ExpenseLine()
        if obj is None:raise Problem("invalid_line",field=prefix+"id")
        obj.net,obj.tax,obj.date=net,tax,day(line.get("date"),prefix+"date")
        obj.recoverable=line.get("recoverable",False)
        if type(obj.recoverable)!=bool:raise Problem("invalid_choice",field=prefix+"recoverable")
        obj.category=line["category"]
        try:
            obj.description=string(line,"description",240)
        except Problem:
            raise Problem("invalid_description",field=prefix+"description")
        prepared.append(obj)
    if any(x.receipt_id for x in old.values()):raise Problem("receipt_attached",409)
    if row.pk:row.revision+=1
    row.project_id,row.cost_center,row.method,row.currency=project,center,method,currency
    row.gross=sum(x.net+x.tax for x in prepared)
    if row.gross>=10**16:raise Problem("invalid_amount",field="lines")
    row.save()
    row.lines.filter(pk__in=old).delete()
    for obj in prepared:
        obj.claim=row
        obj.save()
    changed(user,row,"saved")
    return detail(row,user)

@endpoint("GET","POST",module="expenses")
def claims(request):
    if request.method=="POST":
        data=body(request)
        return once(request,"expenses.create",data,lambda:write_claim(request.user,data))
    require(request.user,"expenses.view")
    rows=rows_for(request.user).select_related("employee")
    if site:=positive_filter(request.GET,"site"):rows=rows.filter(site_id=site)
    if status:=request.GET.get("status"):rows=rows.filter(status=status)
    return paginate(request,search_rows(rows,request.GET,("employee__name",)).order_by("-pk"),header)

@endpoint("GET","PATCH",module="expenses")
def claim(request,pk):
    row=get_claim(request.user,pk)
    if request.method=="GET":return detail(row,request.user)
    authorize(request.user,row,"create")
    data=body(request)
    return once(request,f"expenses.edit.{pk}",data,lambda:write_claim(request.user,data,get_claim(request.user,pk,True)))

def journal(row,user,data,payout=None):
    ledger=ledger_lock()
    when=day(data.get("posting_date"),"posting_date")
    if not ledger.activated_at or not ledger.cutoff or when<ledger.cutoff or ledger.currency!=row.currency:
        raise Problem("finance_period_unavailable",409)
    if when<max(x.date for x in row.lines.all()) or payout and when<row.entry.date:
        raise Problem("invalid_date",field="posting_date")
    period=period_for(ledger,when)
    if payout:
        amounts={"EMP-PAY":payout,"1100":-payout}
    else:
        tax=sum(x.tax for x in row.lines.all() if x.recoverable)
        amounts={"EMP-EXP":row.gross-tax,"EXP-TAX":tax,("EMP-PAY" if row.method=="out_of_pocket" else "1100"):-row.gross}
    kinds={"EMP-PAY":"liability","EMP-EXP":"expense","EXP-TAX":"asset","1100":"asset"}
    values=[]
    for code,amount in amounts.items():
        if not amount:continue
        account=Account.objects.filter(code=code,ledger=ledger,kind=kinds[code],detail=True,active=True).first()
        if not account:raise Problem("finance_account_unavailable",409)
        values.append((account,amount,row.site_id,row.cost_center if code=="EMP-EXP" else None))
    return posted_entry(ledger,period,when,user.username,f"Employee expense EC-{row.pk}", "expense_payment" if payout else "employee_expense",values)

@endpoint("POST",module="expenses")
def transition(request,pk,action):
    if action not in PERMISSIONS:raise Problem("not_found",404)
    user=request.user
    initial=get_claim(user,pk)
    authorize(user,initial,PERMISSIONS[action])
    data=body(request)
    def change():
        from backbone.site_admin import require_active_site
        if action in ("post","reimburse"):
            ledger_lock()
        require_active_site(initial.site_id,lock=True)
        row=get_claim(user,pk,True)
        authorize(user,row,PERMISSIONS[action])
        version(row,data)
        if row.status not in STATES[action]:raise Problem("invalid_transition",409)
        if action=="post":row.entry=journal(row,user,data)
        if action=="reimburse":
            amount=money(data.get("amount"),"amount")
            if not 0<amount<=row.balance_due:raise Problem("invalid_amount",field="amount")
            reference=string(data,"reference",120)
            entry=journal(row,user,data,amount)
            settlement=Settlement.objects.create(claim=row,date=entry.date,amount=amount,reference=reference,entry=entry,actor=user)
            row.amount_paid+=amount
            row.status="partially_reimbursed" if row.balance_due else "reimbursed"
            publish("expenses","settlement.recorded",settlement.pk,f"expense-payment:{settlement.pk}")
        else:
            row.status=dict(post="posted",cancel="cancelled",submit="submitted",approve="approved",reject="rejected")[action]
        if action=="reject":row.review_note=string(data,"reason",500)
        row.revision+=1
        row.save()
        changed(user,row,action)
        return detail(row,user)
    return once(request,f"expenses.{action}.{pk}",data,change)

@endpoint("GET","POST",module="expenses")
def receipt(request,pk,line):
    row=get_claim(request.user,pk)
    item=row.lines.filter(pk=line).first()
    if not item:raise Problem("not_found",404)
    if request.method=="GET":
        if not item.receipt_id:raise Problem("not_found",404)
        return download_internal_receipt(item.receipt)
    authorize(request.user,row,"create")
    try:validated=validate(request.FILES.get("file"))
    except ValidationError:raise Problem("invalid_file",field="file")
    rev=request.POST.get("revision","")
    def attach():
        from backbone.site_admin import require_active_site
        require_active_site(row.site_id,lock=True)
        locked=get_claim(request.user,pk,True)
        authorize(request.user,locked,"create")
        version(locked,{"revision":int(rev) if rev.isascii() and rev.isdigit() and len(rev)<10 else None})
        locked_item=locked.lines.select_for_update().filter(pk=line).first()
        if not locked_item or locked.status!="draft" or locked_item.receipt_id:raise Problem("invalid_transition",409)
        locked_item.receipt=store_internal_receipt(validated)
        locked_item.save(update_fields=["receipt"])
        locked.revision+=1
        locked.save(update_fields=["revision"])
        changed(request.user,locked,"receipt")
        return detail(locked,request.user)
    return once(request,f"expenses.receipt.{line}",{"revision":rev,"name":validated[0],"sha":hashlib.sha256(validated[2]).hexdigest()},attach)

def dashboard(user,site):
    rows=rows_for(user)
    if site:rows=rows.filter(site_id=site)
    result={}
    if has_permission(user,"expenses.approve",site if site else ...):
        pending=visible(rows,user,"expenses.approve").filter(status="submitted").exclude(Q(employee__user=user)|Q(creator=user))
        result["pending-expenses"]={"count":pending.count(),"items":[{"reference":f"EC-{pk}","party":name,"amount":amount,"currency":currency,"status":"submitted","path":f"/expenses?claim={pk}"} for pk,name,amount,currency in pending.order_by("pk").values_list("pk","employee__name","gross","currency")[:5]]}
    if has_permission(user,"expenses.reimburse",site if site else ...):
        pending=visible(rows,user,"expenses.reimburse").filter(status__in=("posted","partially_reimbursed"),method="out_of_pocket")
        result["expense-due"]={"amount":pending.aggregate(total=Sum(F("gross")-F("amount_paid")))["total"] or Decimal("0.00"),"currency":Ledger.objects.get(pk=1).currency}
    return result

@endpoint("GET",module="expenses")
def settlements(request):
    require(request.user,"expenses.reimburse")
    claims=visible(rows_for(request.user),request.user,"expenses.reimburse")
    if site:=positive_filter(request.GET,"site"):claims=claims.filter(site_id=site)
    rows=Settlement.objects.filter(claim__in=claims).select_related("claim")
    rows=search_rows(rows,request.GET,("reference",))
    return paginate(request,rows.order_by("-date","-pk"),lambda x:dict(id=x.pk,claim_id=x.claim_id,
        employee_id=x.claim.employee_id,site_id=x.claim.site_id,date=x.date.isoformat(),currency=x.claim.currency,
        amount=str(x.amount),reference=x.reference,entry_id=x.entry_id))
