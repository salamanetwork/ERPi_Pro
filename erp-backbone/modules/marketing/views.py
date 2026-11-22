import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from backbone.access import audit, has_permission, scopes, visible
from backbone.http import Problem, body, identifier, once, require, site, string
from backbone.models import Site
from backbone.services.pagination import paginate, positive_filter
from backbone.http import endpoint as route
from inventory.models import SiteSettings as InventorySiteSettings

from . import models as m
from . import services as s
from .dns import preflight
from .providers import PROVIDERS, enabled

MODULE = "marketing"
ADMIN = "marketing.admin"
VIEW = "marketing.view"
EDIT = "marketing.edit"


def one(rows, key):
    row = rows.filter(pk=key).first()
    if row is None:
        raise Problem("not_found", 404)
    return row


def choice(data, field, values):
    value = data.get(field)
    if value not in values:
        raise Problem("invalid_" + field, field=field, reason="invalid_choice")
    return value


def integer(data, field, minimum, maximum):
    value = data.get(field)
    if type(value) is not int or not minimum <= value <= maximum:
        raise Problem("invalid_" + field, field=field)
    return value


def address_for(channel, value):
    if channel == "email":
        value = value.lower()
        try:
            validate_email(value)
        except ValidationError:
            raise Problem("invalid_address", field="address") from None
    elif not re.fullmatch(r"\+[1-9][0-9]{6,14}", value):
        raise Problem("invalid_address", field="address")
    return value


def id_list(data, key, maximum=2000):
    values = data.get(key)
    if not isinstance(values, list) or not values or len(values) > maximum:
        raise Problem("invalid_" + key, field=key)
    return set(identifier(value,key) for value in values)


def checked_site(request, data, permission):
    key = site(data)
    require(request.user, permission, key)
    return one(Site.objects.filter(active=True), key)


def collections(request, model, serializer, permission=VIEW):
    require(request.user, permission)
    rows = visible(model.objects.all(), request.user, permission)
    if sid := positive_filter(request.GET, "site_id"):
        rows = rows.filter(site_id=sid)
    return paginate(request, rows.order_by("-pk"), serializer)


def site_zone(site_id):
    return InventorySiteSettings.objects.filter(site_id=site_id).values_list("timezone", flat=True).first() or "UTC"


def contact_data(row, user):
    details = has_permission(user, EDIT, row.site_id) or has_permission(user, ADMIN, row.site_id)
    return {"id":row.pk,"site_id":row.site_id,"first_name":row.first_name if details else "",
        "forgotten":row.forgotten_at is not None,
        "endpoints":[{"id":e.pk,"channel":e.channel,"address":e.address if details else "",
            "consents":list(m.Consent.objects.filter(endpoint=e).values("purpose","status"))} for e in row.endpoints.order_by("id")]}


@never_cache
@route("GET","POST",module=MODULE)
def contacts(request):
    if request.method == "GET":
        return collections(request,m.Contact,lambda row:contact_data(row,request.user))
    data = body(request)
    location = checked_site(request,data,EDIT)

    def create():
        channel = choice(data,"channel",s.CHANNELS)
        name = string(data,"first_name",100,required=False)
        if "\n" in name or "\r" in name:
            raise Problem("invalid_first_name",field="first_name")
        lead = customer = None
        if data.get("lead_id") and data.get("customer_id"):
            raise Problem("invalid_lead_id",field="lead_id")
        value = string(data,"address",254,required=False)
        from backbone.registry import enabled_modules

        if data.get("lead_id"):
            from crm.models import Lead
            from crm.services import scoped
            if "crm" not in enabled_modules():
                raise Problem("module_disabled",404)
            require(request.user,"crm.view",location.pk)
            lead = scoped(Lead.objects.filter(site=location),request.user,identifier(data["lead_id"],"lead_id"))
            value = value or (lead.email if channel=="email" else lead.phone)
            name = name or next(iter(lead.name.split()),"")
        if data.get("customer_id"):
            from sales.models import Customer
            if not {"sales", "customers"} & enabled_modules():
                raise Problem("module_disabled",404)
            require(request.user,"sales.view",location.pk)
            customer = one(Customer.objects.filter(active=True),identifier(data["customer_id"],"customer_id"))
        value = address_for(channel,value)
        key = s.fingerprint(value)
        if m.Suppression.objects.filter(site=location,channel=channel,endpoint_key=key,reason="forgotten").exists():
            raise Problem("marketing_forgotten",409)
        source_key = s.fingerprint(f"lead:{lead.pk}" if lead else f"customer:{customer.pk}") if (lead or customer) else ""
        if source_key and m.Contact.objects.filter(site=location,source_key=source_key,forgotten_at__isnull=False).exists():
            raise Problem("marketing_forgotten",409)
        row=m.Contact.objects.create(site=location,first_name=name,lead=lead,customer=customer,source_key=source_key)
        m.ContactEndpoint.objects.create(contact=row,channel=channel,address=value,endpoint_key=key)
        audit(request.user,"marketing.contact",f"contact:{row.pk}")
        return {"id":row.pk}
    result = once(request,"marketing.contact",{"request":s.fingerprint(json.dumps(data,sort_keys=True))},create)
    return result


@never_cache
@route("POST",module=MODULE)
def add_endpoint(request, pk):
    row=one(visible(m.Contact.objects.all(),request.user,EDIT),pk)
    data=body(request)
    channel=choice(data,"channel",s.CHANNELS)
    address=address_for(channel,string(data,"address",254))
    key=s.fingerprint(address)
    def create():
        locked=m.Contact.objects.select_for_update().get(pk=pk)
        if locked.forgotten_at or m.Suppression.objects.filter(site=row.site,channel=channel,endpoint_key=key,reason="forgotten").exists():
            raise Problem("marketing_forgotten",409)
        item,fresh=m.ContactEndpoint.objects.get_or_create(contact=locked,channel=channel,defaults={"address":address,"endpoint_key":key})
        if not fresh:
            raise Problem("marketing_endpoint_exists",409)
        return {"id":item.pk}
    return once(request,"marketing.endpoint",{"request":s.fingerprint(json.dumps({**data,"contact":pk},sort_keys=True))},create)


@never_cache
@route("GET","POST",module=MODULE)
def consent(request,pk):
    permission=VIEW if request.method=="GET" else EDIT
    item=one(visible(m.Contact.objects.all(),request.user,permission),pk)
    if request.method=="GET":
        return {"items":list(m.ConsentHistory.objects.filter(endpoint__contact=item).order_by("-id")
            .values("channel","purpose","status","source","created_at")[:200])}
    data=body(request)
    def create():
        current=m.Contact.objects.select_for_update().get(pk=pk)
        if current.forgotten_at:
            raise Problem("marketing_forgotten",409)
        target=one(current.endpoints.all(),identifier(data.get("endpoint_id"),"endpoint_id"))
        row=s.apply_consent(target,choice(data,"purpose",s.PURPOSES),choice(data,"change",("in","out")),
            choice(data,"source",("documented","website","import","request")))
        audit(request.user,"marketing.consent",f"consent:{row.pk}")
        return {"status":row.status}
    return once(request,"marketing.consent",{**data,"contact":pk},create)


@csrf_exempt
@never_cache
@route("GET","POST",public=True)
def subscription(request, token, action):
    row=m.Token.objects.filter(digest=s.fingerprint(token),action=action).select_related("consent__endpoint__contact").first()
    if not row and action=="unsubscribe":
        from django.core.signing import BadSignature, Signer
        try:
            test_pk=int(Signer(salt="marketing.test-unsubscribe").unsign(token))
        except (BadSignature,ValueError):
            test_pk=None
        if test_pk and m.Delivery.objects.filter(pk=test_pk,kind="test").exists():
            return {"status":"test_unsubscribed"}
    if not row or row.consent.endpoint.contact.forgotten_at:
        raise Problem("not_found",404)
    if request.method=="GET":
        label="Confirm subscription / تأكيد الاشتراك" if action=="confirm" else "Unsubscribe / إلغاء الاشتراك"
        response=HttpResponse(format_html('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><form method="post"><button>{}</button></form>',label))
        response["Content-Security-Policy"]="default-src 'none'; form-action 'self'; frame-ancestors 'none'"
        return response
    with transaction.atomic():
        contact=m.Contact.objects.select_for_update().get(pk=row.consent.endpoint.contact_id)
        row=m.Token.objects.select_for_update(of=("self",)).select_related("consent__endpoint__contact").get(pk=row.pk)
        current=row.consent
        if contact.forgotten_at:
            raise Problem("not_found",404)
        if action=="confirm":
            if row.expires_at <= timezone.now():
                if not row.used_at and current.status=="pending":
                    s.consent_state(current,"expired","expiry")
                    row.used_at=timezone.now()
                    row.save(update_fields=["used_at"])
                return {"error":"marketing_token_expired"},410
            if not row.used_at and current.status=="pending":
                s.confirm(current,"confirmation_link")
        elif not row.used_at:
            s.apply_consent(current.endpoint,current.purpose,"out","unsubscribe")
        if not row.used_at:
            row.used_at=timezone.now()
            row.save(update_fields=["used_at"])
        return {"status":current.status} if action=="confirm" else {"status":"opt_out"}


@never_cache
@route("POST",module=MODULE)
def forget(request,pk):
    row=one(visible(m.Contact.objects.all(),request.user,ADMIN),pk)
    data=body(request)
    reason=string(data,"reason",500)
    def erase():
        result=s.forget(m.Contact.objects.select_for_update().get(pk=row.pk),request.user,reason)
        return {"id":result.pk,"forgotten":True}
    return once(request,"marketing.forget",{"contact":pk},erase)


@never_cache
@route("GET","POST",module=MODULE)
def segments(request):
    if request.method=="GET":
        return collections(request,m.Segment,lambda row:{"id":row.pk,"site_id":row.site_id,"name":row.name,"count":row.contacts.filter(forgotten_at=None).count()})
    data=body(request)
    location=checked_site(request,data,EDIT)
    def create():
        ids=id_list(data,"contact_ids")
        rows=m.Contact.objects.filter(site=location,pk__in=ids,forgotten_at=None)
        if rows.count()!=len(ids):
            raise Problem("invalid_contact_ids",field="contact_ids")
        item=m.Segment.objects.create(site=location,name=string(data,"name",130))
        item.contacts.set(rows)
        return {"id":item.pk}
    return once(request,"marketing.segment",data,create)


def template_data(row):
    return {"id":row.pk,"site_id":row.site_id,"channel":row.channel,"name":row.name,
        "versions":list(row.templateversion_set.order_by("-number").values("id","number","subject","body","provider_template","locked_at"))}


def new_version(item,data):
    number=(item.templateversion_set.aggregate(latest=Max("number"))["latest"] or 0)+1
    provider_template=string(data,"provider_template",120,required=False)
    if provider_template and not re.fullmatch(r"HX[0-9a-fA-F]{32}",provider_template):
        raise Problem("invalid_provider_template",field="provider_template")
    subject=string(data,"subject",200,required=item.channel=="email")
    if "\r" in subject or "\n" in subject:
        raise Problem("invalid_subject",field="subject")
    return m.TemplateVersion.objects.create(template=item,number=number,subject=subject,
        body=string(data,"body",10000,required=item.channel!="whatsapp"),provider_template=provider_template)


@never_cache
@route("GET","POST",module=MODULE)
def templates(request):
    if request.method=="GET":
        return collections(request,m.Template,template_data)
    data=body(request)
    location=checked_site(request,data,EDIT)
    def create():
        item=m.Template.objects.create(site=location,name=string(data,"name",130),channel=choice(data,"channel",s.CHANNELS))
        return {"id":item.pk,"version":new_version(item,data).pk}
    return once(request,"marketing.template",data,create)


@never_cache
@route("POST",module=MODULE)
def versions(request,pk):
    template=one(visible(m.Template.objects.all(),request.user,EDIT),pk)
    data=body(request)
    return once(request,"marketing.version",{**data,"template":pk},
        lambda:{"id":new_version(m.Template.objects.select_for_update().get(pk=template.pk),data).pk})


def campaign_data(row, user=None):
    actions = []
    if user and has_permission(user, EDIT, row.site_id):
        actions.append("preview")
    if user and has_permission(user, ADMIN, row.site_id):
        actions.extend(["test-send", "preflight"])
        if row.status == "draft" and row.creator_id != user.pk:
            actions.append("approve")
        if row.status in {"approved", "scheduled", "paused"}:
            actions.extend(["schedule", "cancel"])
        if row.status == "approved":
            actions.append("send")
        if row.status == "scheduled":
            actions.append("pause")
        if row.status == "paused":
            actions.append("resume")
    zone=site_zone(row.site_id)
    return {"id":row.pk,"site_id":row.site_id,"title":row.title,"channel":row.channel,"purpose":row.purpose,
        "actions":actions,"is_creator":bool(user and row.creator_id == user.pk),
        "template_version_id":row.template_version_id,"status":row.status,"revision":row.revision,"scheduled_at":row.scheduled_at.isoformat() if row.scheduled_at else None,
        "schedule_timezone":row.schedule_timezone,"site_timezone":zone,
        "scheduled_local":row.scheduled_at.astimezone(ZoneInfo(zone)).isoformat() if row.scheduled_at else None,
        "timezone_changed":bool(row.scheduled_at and row.schedule_timezone!=zone)}


@never_cache
@route("GET","POST",module=MODULE)
def campaigns(request):
    if request.method=="GET":
        return collections(request,m.Campaign,lambda row:campaign_data(row,request.user))
    data=body(request)
    location=checked_site(request,data,EDIT)
    def create():
        channel=choice(data,"channel",s.CHANNELS)
        version=one(m.TemplateVersion.objects.filter(template__site=location,template__channel=channel),
            identifier(data.get("template_version_id"),"template_version_id"))
        ids=id_list(data,"segment_ids",100)
        segments=m.Segment.objects.filter(site=location,pk__in=ids)
        if segments.count()!=len(ids):
            raise Problem("invalid_segment_ids",field="segment_ids")
        row=m.Campaign.objects.create(site=location,title=string(data,"title",160),creator=request.user,
            template_version=version,channel=channel,purpose=choice(data,"purpose",s.PURPOSES))
        row.segments.set(segments)
        audit(request.user,"marketing.create",f"campaign:{row.pk}")
        return campaign_data(row,request.user)
    return once(request,"marketing.campaign",data,create)


@never_cache
@route("GET",module=MODULE)
def campaign(request,pk):
    return campaign_data(one(visible(m.Campaign.objects.all(),request.user,VIEW),pk),request.user)


@never_cache
@route("POST",module=MODULE)
def preview(request,pk):
    row=one(visible(m.Campaign.objects.all(),request.user,EDIT),pk)
    dataset=s.audience(row).filter(contact__forgotten_at=None)
    shown=[]
    for e in dataset[:5]:
        subject,text,missing=s.render(row.template_version,e.contact)
        shown.append({"contact_id":e.contact_id,"subject":subject,"body":text,"missing_variables":missing})
    return {"samples":shown}


def parse_time(value, zone):
    try:
        dt=datetime.fromisoformat(value)
        target=ZoneInfo(zone)
        if dt.tzinfo is not None:
            return dt
        candidates=set()
        from datetime import UTC
        for fold in (0,1):
            instant=dt.replace(tzinfo=target,fold=fold).astimezone(UTC)
            if instant.astimezone(target).replace(tzinfo=None)==dt:
                candidates.add(instant)
        if len(candidates)==1:
            return candidates.pop()
    except (ValueError, OverflowError):
        pass
    raise Problem("invalid_scheduled_at",field="scheduled_at")


@never_cache
@route("POST",module=MODULE)
def campaign_action(request,pk,action):
    initial=one(visible(m.Campaign.objects.all(),request.user,ADMIN),pk)
    data=body(request)
    if action not in {"approve","send","schedule","pause","resume","cancel","template","test-send","preflight"}:
        raise Problem("not_found",404)
    account=(initial.provider or s.provider_for(initial.site_id,initial.channel)) if action in {"test-send","preflight"} else initial.provider
    if action=="preflight":
        return preflight(account)
    if action in {"send","schedule","resume"} and (account is None or not enabled(account) or not preflight(account)["ok"]):
        raise Problem("marketing_provider_preflight_failed",409)

    def apply():
        row=m.Campaign.objects.select_for_update().select_related("template_version","provider").select_for_update(of=("self",)).get(pk=pk)
        if action=="test-send":
            current=account
            destination=address_for(row.channel,string(data,"address",254))
            if current.adapter not in {"mailpit","mock"} and destination not in settings.ERP_MARKETING_TEST_RECIPIENTS:
                raise Problem("forbidden",403)
            sample=None
            if data.get("contact_id"):
                sample=one(m.Contact.objects.filter(site=row.site,forgotten_at=None),identifier(data["contact_id"],"contact_id"))
            subject,text,missing=s.render(row.template_version,sample)
            if missing:
                raise Problem("marketing_missing_variables",field="contact_id")
            endpoint=sample.endpoints.filter(channel=row.channel).first() if sample else None
            if sample and endpoint is None:
                raise Problem("invalid_contact_id",field="contact_id")
            delivery=s.enqueue(site=row.site,provider=current,campaign=row,kind="test",address=destination,
                endpoint=endpoint,template_version=row.template_version,provider_template=row.template_version.provider_template)
            audit(request.user,"marketing.test_send",f"campaign:{row.pk}")
            return {"delivery_id":delivery.pk,"adapter":current.adapter}
        if row.revision != identifier(data.get("revision"),"revision"):
            raise Problem("revision_conflict",409)
        if action=="approve" and row.status=="draft":
            if row.creator_id==request.user.pk:
                raise Problem("marketing_separate_approver",403)
            version=m.TemplateVersion.objects.select_for_update().get(pk=row.template_version_id)
            row.provider=s.provider_for(row.site_id,row.channel)
            if row.provider.adapter=="twilio" and row.channel=="whatsapp" and not version.provider_template:
                raise Problem("invalid_provider_template",field="provider_template")
            if not version.locked_at:
                version.locked_at=timezone.now()
                version.save(update_fields=["locked_at"])
            row.approver,row.status=request.user,"approved"
        elif action=="template" and row.status in {"draft","approved","scheduled","paused"}:
            if row.delivery_set.filter(kind="campaign",attempts__gt=0).exists():
                raise Problem("marketing_invalid_state",409)
            row.schedule_revision+=1
            row.scheduled_at,row.schedule_timezone=None,""
            row.delivery_set.filter(kind="campaign",state__in=["pending","queued"]).update(state="pending",lease=None)
            row.template_version=one(m.TemplateVersion.objects.filter(template__site=row.site,template__channel=row.channel),
                identifier(data.get("template_version_id"),"template_version_id"))
            row.status,row.approver="draft",None
        elif (action=="send" and row.status=="approved") or (action=="schedule" and row.status in {"approved","scheduled","paused"}):
            if row.provider_id!=account.pk:
                raise Problem("revision_conflict",409)
            zone=site_zone(row.site_id)
            dt=timezone.now() if action=="send" else parse_time(string(data,"scheduled_at",80),zone)
            row.scheduled_at,row.schedule_timezone,row.status=dt,zone,"scheduled"
            row.schedule_revision+=1
            row.delivery_set.filter(kind="campaign",state__in=["pending","queued"]).update(
                due_at=dt,state="pending",lease=None,schedule_revision=row.schedule_revision)
            if not row.recipients.exists():
                s.scheduled_deliveries(row)
        elif action=="pause" and row.status=="scheduled":
            row.status="paused"
        elif action=="resume" and row.status=="paused":
            row.status="scheduled"
        elif action=="cancel" and row.status in {"approved","scheduled","paused"}:
            row.status="cancelled"
            row.delivery_set.filter(kind="campaign",state__in=["pending","queued"]).update(state="cancelled")
        else:
            raise Problem("marketing_invalid_state",409)
        row.revision+=1
        row.save()
        audit(request.user,"marketing."+action,f"campaign:{row.pk}")
        return campaign_data(row,request.user)
    return once(request,"marketing."+action,{"request":s.fingerprint(json.dumps({**data,"campaign":pk},sort_keys=True))},apply)


@never_cache
@route("GET",module=MODULE)
def metrics(request,pk):
    row=one(visible(m.Campaign.objects.all(),request.user,VIEW),pk)
    grouped=row.delivery_set.filter(kind="campaign").values("state").annotate(total=Count("id"))
    events=m.ProviderEvent.objects.filter(delivery__campaign=row,delivery__kind="campaign").values("category").annotate(total=Count("delivery",distinct=True))
    return {"states":{r["state"]:r["total"] for r in grouped},"events":{r["category"]:r["total"] for r in events}}


@never_cache
@route("GET",module=MODULE)
def suppressions(request):
    permission="marketing.suppressions"
    return collections(request,m.Suppression,lambda row:{"id":row.pk,"site_id":row.site_id,"channel":row.channel,
        "fingerprint":row.endpoint_key[:12],"reason":row.reason,"purpose":row.purpose,"created_at":row.created_at},permission)


@never_cache
@route("GET","POST",module=MODULE)
def settings_view(request,pk):
    require(request.user,ADMIN,pk)
    location=one(Site.objects.filter(active=True),pk)
    row=m.SiteSettings.objects.filter(site=location).first()
    if request.method=="GET":
        return {"consent_mode":row.consent_mode if row else "single","timezone":site_zone(pk),
            "providers":list(m.ProviderAccount.objects.filter(site=location).values("id","adapter","channel","sender","rate","enabled","config"))}
    data=body(request)
    def update():
        if "consent_mode" in data:
            m.SiteSettings.objects.update_or_create(site=location,defaults={"consent_mode":choice(data,"consent_mode",("single","double"))})
        else:
            channel=choice(data,"channel",s.CHANNELS)
            adapter=choice(data,"adapter",("mailpit","ses") if channel=="email" else ("mock","twilio"))
            sender=address_for(channel,string(data,"sender",160))
            config={}
            if adapter=="ses":
                config={name:string(data,name,240) for name in ("region","configuration_set","topic_arn")}
                if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-[0-9]",config["region"]) or not config["topic_arn"].startswith(f"arn:aws:sns:{config['region']}:"):
                    raise Problem("invalid_topic_arn",field="topic_arn")
            Site.objects.select_for_update().get(pk=pk)
            if (m.Campaign.objects.filter(site=location,channel=channel,status="approved").exists()
                or m.Delivery.objects.filter(site=location,provider__channel=channel,state__in=["pending","queued","running","accepted","unknown"]).exists()):
                raise Problem("marketing_provider_busy",409)
            previous=m.ProviderAccount.objects.filter(site=location,channel=channel).first()
            m.ProviderAccount.objects.update_or_create(site=location,channel=channel,defaults={
                "adapter":adapter,"sender":sender,"config":config,"rate":integer(data,"rate",1,1000),
                "revision":(previous.revision+1 if previous else 1)})
        audit(request.user,"marketing.settings",f"site:{pk}")
        return {"saved":True}
    return once(request,"marketing.settings",{**data,"site":pk},update)


@never_cache
@route("GET","POST",module=MODULE)
def dead_letters(request,pk=None):
    require(request.user,ADMIN)
    rows=m.DeadLetter.objects.select_related("delivery")
    if sid := positive_filter(request.GET,"site_id"):
        rows=rows.filter(delivery__site_id=sid)
    values=scopes(request.user,ADMIN)
    if None not in values:
        rows=rows.filter(delivery__site_id__in=values)
    if request.method=="GET":
        return paginate(request,rows.order_by("-pk"),lambda row:{"id":row.pk,"delivery_id":row.delivery_id,
            "reason":row.reason,"created_at":row.created_at})
    row=one(rows,pk)
    def requeue():
        delivery=m.Delivery.objects.select_for_update().get(pk=row.delivery_id)
        if delivery.state!="dead":
            raise Problem("marketing_invalid_state",409)
        delivery.state,delivery.due_at="pending",timezone.now()
        delivery.save(update_fields=["state","due_at"])
        row.delete()
        audit(request.user,"marketing.requeue",f"delivery:{delivery.pk}")
        return {"queued":True}
    return once(request,"marketing.requeue",{"entry":pk},requeue)


@never_cache
@route("GET","POST",module=MODULE)
def surveys(request):
    if request.method=="GET":
        return collections(request,m.SurveyVersion,lambda row:{"id":row.pk,"site_id":row.site_id,"title":row.title,
            "questions":row.questions,"responses":m.SurveyResponse.objects.filter(invitation__survey=row).count()})
    data=body(request)
    location=checked_site(request,data,EDIT)
    questions=data.get("questions")
    if not isinstance(questions,list) or not 1<=len(questions)<=10 or any(not isinstance(q,str) or not q.strip() or len(q)>240 for q in questions):
        raise Problem("invalid_questions",field="questions")
    return once(request,"marketing.survey",data,lambda:{"id":m.SurveyVersion.objects.create(site=location,
        title=string(data,"title",140),questions=questions).pk})


@never_cache
@route("GET","POST",module=MODULE)
def automations(request):
    if request.method=="GET":
        return collections(request,m.AutomationRule,lambda row:{"id":row.pk,"site_id":row.site_id,"campaign_id":row.campaign_id,
            "survey_id":row.survey_id,"delay_minutes":row.delay_minutes})
    data=body(request)
    location=checked_site(request,data,ADMIN)
    row=one(m.Campaign.objects.filter(site=location,channel="email"),identifier(data.get("campaign_id"),"campaign_id"))
    survey=one(m.SurveyVersion.objects.filter(site=location),identifier(data.get("survey_id"),"survey_id"))
    delay=integer(data,"delay_minutes",0,43200)
    return once(request,"marketing.automation",data,lambda:{"id":m.AutomationRule.objects.create(
        site=location,campaign=row,survey=survey,delay_minutes=delay).pk})


@csrf_exempt
@never_cache
@route("GET","POST",public=True,module=MODULE)
def survey_public(request,token):
    row=m.SurveyInvitation.objects.filter(digest=s.fingerprint(token),expires_at__gt=timezone.now(),
        endpoint__contact__forgotten_at=None).select_related("survey","endpoint__contact").first()
    if not row:
        raise Problem("not_found",404)
    if request.method=="GET":
        from django.utils.html import format_html_join

        fields=format_html_join("",'<label>{}<br><textarea name="a{}" required maxlength="500"></textarea></label><br>',
            ((question,n) for n,question in enumerate(row.survey.questions)))
        response=HttpResponse(format_html('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><h1>{}</h1><form method="post">{}<button>Submit</button></form>',row.survey.title,fields))
        response["Content-Security-Policy"]="default-src 'none'; form-action 'self'; frame-ancestors 'none'"
        return response
    answers=[request.POST.get(f"a{n}","").strip() for n in range(len(row.survey.questions))]
    if any(not answer or len(answer)>500 for answer in answers):
        raise Problem("invalid_answers")
    with transaction.atomic():
        contact=m.Contact.objects.select_for_update().get(pk=row.endpoint.contact_id)
        if contact.forgotten_at:
            raise Problem("not_found",404)
        _,created=m.SurveyResponse.objects.get_or_create(invitation=row,defaults={"answers":answers})
    return {"recorded":True},201 if created else 200


@csrf_exempt
@never_cache
@route("POST",public=True,module=MODULE)
def webhook(request,adapter,pk):
    account=one(m.ProviderAccount.objects.filter(adapter=adapter),pk)
    if adapter=="ses":
        data=body(request)
        if not PROVIDERS[adapter].verify_webhook(account,request,data):
            raise Problem("forbidden",403)
        if data["Type"]=="SubscriptionConfirmation":
            import boto3
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError

            try:
                sns=boto3.client("sns",region_name=account.config["region"],config=Config(connect_timeout=3,read_timeout=4,retries={"total_max_attempts":1}))
                sns.confirm_subscription(TopicArn=account.config["topic_arn"],Token=data["Token"],AuthenticateOnUnsubscribe="true")
            except (BotoCoreError,ClientError):
                return {"error":"marketing_sns_confirmation_failed"},503
            return {"confirmed":True}
        try:
            payload=json.loads(data["Message"])
            message_id=payload["mail"]["messageId"]
            event=payload.get("eventType",payload.get("notificationType"))
        except (ValueError,TypeError,KeyError):
            raise Problem("invalid_event") from None
        subtype=""
        category={"Delivery":"delivered","DeliveryDelay":"delay","Complaint":"complaint","Open":"open","Click":"click","Send":"accepted"}.get(event)
        if event=="Bounce":
            bounce=payload.get("bounce",{})
            category={"Permanent":"hard_bounce","Transient":"soft_bounce"}.get(bounce.get("bounceType"),"bounce_unknown")
            subtype=str(bounce.get("bounceSubType",""))[:60]
        identifier_key=str(data["MessageId"])
    elif adapter=="twilio":
        if not PROVIDERS[adapter].verify_webhook(account,request):
            raise Problem("forbidden",403)
        message_id=request.POST.get("MessageSid","")
        status=request.POST.get("MessageStatus","")
        category={"sent":"accepted","delivered":"delivered","read":"delivered","failed":"failed","undelivered":"failed"}.get(status)
        subtype=request.POST.get("ErrorCode","")[:60]
        identifier_key=f"{message_id}:{status}:{subtype}"
    else:
        raise Problem("not_found",404)
    if not category or not isinstance(message_id,str) or not message_id or len(message_id)>200:
        return {"ignored":True},202
    key=hashlib.sha256(identifier_key.encode()).hexdigest()
    from .feedback import record
    record(account,key,message_id,category,subtype)
    return {"recorded":True}


@never_cache
@route("GET",module=MODULE)
def delivery_status(request,pk):
    row=one(visible(m.Delivery.objects.all(),request.user,ADMIN).select_related("provider"),pk)
    return {"id":row.pk,"kind":row.kind,"state":row.state,"attempts":row.attempts,"provider":row.provider.adapter,
        "events":list(m.ProviderEvent.objects.filter(delivery=row).order_by("-pk").values("category","subtype","created_at")[:50])}


@never_cache
@route("GET",module=MODULE)
def survey_results(request,pk):
    row=one(visible(m.SurveyVersion.objects.all(),request.user,VIEW),pk)
    responses=m.SurveyResponse.objects.filter(invitation__survey=row).exclude(answers=[]).order_by("-pk")
    return paginate(request,responses,lambda result:{"id":result.pk,"answers":result.answers,"submitted_at":result.submitted_at})
