import hashlib
import json
from decimal import Decimal
from django.db.models import Q, Sum
from backbone.access import audit, visible
from backbone.http import endpoint, require, once, body, Problem, string, identifier, site
from backbone.module_events import publish
from backbone.services.pagination import paginate, positive_filter, search_rows
from accounting.models import Account
from media_service.services import store_internal_receipt, download_internal_receipt
from .models import BankProfile, BankStatement, BankStatementLine, BankMatch, ClearedLine, StatementImport
from . import engine

MODULE = "bank_reconciliation"
ACTIONS = dict(start="reconcile",line="import",import_csv="import",match="reconcile",unmatch="reconcile",adjust="post_adjustment",close="close",edit="import")


def permission(user, action, scope):
    require(user,MODULE+".view",scope)
    require(user,MODULE+"."+action,scope)


def profiles_for(user):
    return visible(BankProfile.objects.all(),user,MODULE+".view")


def header(s):
    return dict(id=s.pk,profile_id=s.profile_id,bank=s.profile.name,currency=s.profile.currency,start=str(s.start),end=str(s.end),
                status=s.status,revision=s.revision,**engine.balances(s))


def get_statement(user,pk,lock=False):
    rows = BankStatement.objects.select_related("profile__account").filter(profile__in=profiles_for(user))
    if lock:
        rows = rows.select_for_update(of=("self",))
    row = rows.filter(pk=pk).first()
    if not row:
        raise Problem("not_found",404)
    return row


@endpoint("GET","POST",module=MODULE)
def profiles(request):
    user = request.user
    if request.method=="GET":
        scope = positive_filter(request.GET,"site")
        permission(user,"view",scope)
        accounts = Account.objects.filter(detail=True,active=True,control=False,ledger_id=1).order_by("code")
        return dict(accounts=list(accounts.values("id","code","name","kind")),profiles=list(profiles_for(user).filter(site_id=scope)
                    .order_by("name").values("id","name","currency","last_four","last_date","last_balance","revision")))
    data = body(request)
    scope = identifier(data.get("site_id"),"site_id")
    permission(user,"import",scope)
    def create():
        site(data)
        def account(key,kind):
            a = Account.objects.select_for_update().filter(pk=identifier(data.get(key),key),kind=kind,ledger_id=1,
                                                          active=True,detail=True,control=False).first()
            if not a:
                raise Problem("invalid_account",field=key)
            return a
        bank = account("account_id","asset")
        if BankProfile.objects.filter(site_id=scope,account=bank).exists():
            raise Problem("profile_exists",409)
        suffix = string(data,"last_four",4)
        tolerance = engine.money(data.get("tolerance",0),"tolerance")
        if len(suffix)!=4 or not suffix.isalnum() or not 0<=tolerance<10**10:
            raise Problem("invalid_profile")
        p = BankProfile.objects.create(site_id=scope,name=string(data,"name",100),last_four=suffix,tolerance=tolerance,
                account=bank,charges=account("charges_id","expense"),interest=account("interest_id","revenue"),currency=bank.ledger.currency)
        audit(user,MODULE+".profile",p.pk)
        return {"id":p.pk}
    return once(request,MODULE+".profile",data,create)


@endpoint("GET","POST",module=MODULE)
def statements(request):
    user = request.user
    if request.method=="GET":
        scope = positive_filter(request.GET,"site")
        permission(user,"view",scope)
        rows = BankStatement.objects.filter(profile__in=profiles_for(user).filter(site_id=scope)).select_related("profile")
        return paginate(request,search_rows(rows,request.GET,["profile__name","status"]).order_by("-end","-id"),header)
    data = body(request)
    profile_id = identifier(data.get("profile_id"),"profile_id")
    p = profiles_for(user).filter(pk=profile_id).first()
    if not p:
        raise Problem("not_found",404)
    permission(user,"import",p.site_id)
    def create():
        profile = BankProfile.objects.select_for_update().get(pk=p.pk)
        start,end = engine.day(data.get("start"),"start"),engine.day(data.get("end"),"end")
        opening = engine.money(data.get("opening"),"opening")
        if start>end:
            raise Problem("invalid_date_range",field="end")
        if profile.last_date and (start<=profile.last_date or opening!=profile.last_balance):
            raise Problem("invalid_continuity",409,field="opening")
        if BankStatement.objects.filter(profile=p).exclude(status="reconciled").exists():
            raise Problem("open_statement_exists",409)
        s = BankStatement.objects.create(profile=profile,start=start,end=end,opening_balance=opening,
                    statement_ending_balance=engine.money(data.get("closing"),"closing"))
        audit(user,MODULE+".create",s.pk)
        return header(s)
    return once(request,MODULE+".create",data,create)


def serialize_line(x):
    return dict(id=x.pk,date=str(x.date),description=x.description,reference=x.reference,amount=format(x.amount,".2f"),
                running_balance=None if x.running_balance is None else str(x.running_balance),match_id=x.match_id)


@endpoint("GET",module=MODULE)
def detail(request,pk):
    s=get_statement(request.user,pk)
    lines=search_rows(s.lines.all(),request.GET,["reference","description"]).order_by("date","id")
    return dict(**header(s),lines=paginate(request,lines,serialize_line),attachments=list(StatementImport.objects.filter(
            statement=s,attachment__isnull=False).values("id","attachment__name")))


@endpoint("GET",module=MODULE)
def attachment(request,pk,upload):
    s=get_statement(request.user,pk)
    item=StatementImport.objects.filter(statement=s,pk=upload,attachment__isnull=False).first()
    if not item:
        raise Problem("not_found",404)
    return download_internal_receipt(item.attachment)


@endpoint("GET",module=MODULE)
def candidates(request,pk):
    from django.db.models import Case,When,Value,IntegerField,F
    from django.db.models.functions import Abs
    s=get_statement(request.user,pk)
    rows=engine.eligible(s).select_related("entry__settlement__claim__employee","entry__supplier_settlement__supplier","entry__posting__sale__document__customer")
    rows=search_rows(rows,request.GET,["entry__description","entry__settlement__reference","entry__settlement__claim__employee__name",
            "entry__supplier_settlement__reference","entry__supplier_settlement__supplier__name","entry__posting__sale__document__customer__name"],number_fields=("entry_id",))
    bank_id=positive_filter(request.GET,"line")
    b=None
    if bank_id:
        b=s.lines.filter(pk=bank_id,match=None).first()
        if not b:
            raise Problem("not_found",404)
        from datetime import timedelta
        ref=Q(entry__description__icontains=b.description)
        if b.reference:
            ref|=Q(entry__description__icontains=b.reference)|Q(entry__settlement__reference__iexact=b.reference)|Q(entry__supplier_settlement__reference__iexact=b.reference)
        for word in b.description.split()[:8]:
            if len(word)>=3:
                ref|=Q(entry__posting__sale__document__customer__name__icontains=word)|Q(entry__supplier_settlement__supplier__name__icontains=word)|Q(entry__settlement__claim__employee__name__icontains=word)
        rows=rows.annotate(delta=Abs(F("amount")-b.amount),affinity=Case(When(ref,then=Value(0)),default=Value(1),output_field=IntegerField()),
                           near=Case(When(entry__date=b.date,then=Value(0)),When(entry__date__range=(b.date-timedelta(days=3),b.date+timedelta(days=3)),then=Value(1)),default=Value(2))).order_by("delta","affinity","near","-entry__date","pk")
    else:
        rows=rows.order_by("-entry__date","id")
    def serialize(x):
        ref,party=engine.source(x)
        return dict(id=x.pk,entry=x.entry_id,date=str(x.entry.date),description=x.entry.description,reference=ref,party=party,
                    amount=str(x.amount),exact=bool(b and x.amount==b.amount),suggested=bool(b and abs(x.amount-b.amount)<=s.profile.tolerance))
    return paginate(request,rows,serialize)


def csv_input(request):
    f=request.FILES.get("file")
    if not f or f.size>2*1024*1024:
        raise Problem("invalid_csv_file",field="file")
    content=f.read()
    try:
        mapping=json.loads(request.POST.get("mapping","null"))
    except ValueError:
        raise Problem("invalid_column_mapping")
    columns,rows=engine.csv_rows(content,mapping)
    return f.name,content,columns,rows,mapping


@endpoint("POST",module=MODULE)
def preview(request,pk):
    s=get_statement(request.user,pk)
    permission(request.user,"import",s.profile.site_id)
    _,_,columns,rows,mapping=csv_input(request)
    return dict(columns=columns,count=len(rows),preview=[serialize_line(x) for x in engine.prepare_lines(s,rows)[:10]] if mapping else [])


@endpoint("POST",module=MODULE)
def transition(request,pk,action):
    if action not in ACTIONS:
        raise Problem("not_found",404)
    user=request.user
    existing=get_statement(user,pk)
    permission(user,ACTIONS[action],existing.profile.site_id)
    if action=="import_csv":
        name,content,_,rows,mapping=csv_input(request)
        if not rows:
            raise Problem("invalid_column_mapping")
        digest=hashlib.sha256(content).hexdigest()
        value=request.POST.get("revision","")
        data=dict(revision=int(value) if value.isascii() and value.isdecimal() and len(value)<10 else None,
                  digest=digest,mapping=mapping,attach=request.POST.get("attach")=="true")
    else:
        data=body(request)
    def change():
        s=get_statement(user,pk,True)
        if s.status=="reconciled" or type(data.get("revision"))!=int or s.revision!=data["revision"]:
            raise Problem("revision_conflict",409)
        if action in ("match","unmatch","adjust","close") and s.status!="in_progress" or action=="start" and s.status!="draft":
            raise Problem("invalid_transition",409)
        target=str(s.pk)
        if action=="start":
            s.status="in_progress"
        elif action=="edit":
            s.statement_ending_balance=engine.money(data.get("closing"),"closing")
        elif action=="line":
            engine.prepare_lines(s,[data])[0].save()
        elif action=="import_csv":
            if StatementImport.objects.filter(statement=s,digest=digest).exists():
                raise Problem("duplicate_import",409)
            lines=engine.prepare_lines(s,rows)
            asset=store_internal_receipt((name,"text/csv",content)) if data["attach"] else None
            StatementImport.objects.create(statement=s,digest=digest,attachment=asset)
            BankStatementLine.objects.bulk_create(lines)
        elif action=="match":
            group=engine.match(s,engine.id_list(data,"bank_ids"),engine.id_list(data,"gl_ids"),user)
            target+=f"/match/{group.pk}"
        elif action=="unmatch":
            group=BankMatch.objects.filter(statement=s,pk=identifier(data.get("match_id"),"match_id")).first()
            if not group or not s.lines.filter(match=group).exists():
                raise Problem("line_unavailable",409)
            s.lines.filter(match=group).update(match=None)
            ClearedLine.objects.filter(match=group).delete()
            target+=f"/match/{group.pk}"
        elif action=="adjust":
            target+=f"/JE/{engine.adjust(s,data,user)}"
        elif action=="close":
            sums=engine.balances(s)
            if any(Decimal(sums[k]) for k in ("difference","integrity_difference")) or s.lines.filter(match=None).exists():
                raise Problem("statement_not_cleared",409)
            p=BankProfile.objects.select_for_update().get(pk=s.profile_id)
            p.last_date,p.last_balance=s.end,s.statement_ending_balance
            p.revision+=1
            p.save()
            s.status="reconciled"
        s.revision+=1
        s.save()
        audit(user,MODULE+"."+action,target)
        publish(MODULE,"statement.changed",s.pk,f"bank:{s.pk}:{s.revision}")
        return header(s)
    return once(request,f"bank:{pk}:{action}",data,change)
