from datetime import datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from backbone.business_api import collection
from backbone.business_scope import one,revision
from backbone.http import string
from backbone.validation import Problem
from parties.models import Party
from .models import Resource,Appointment

def resource(data,scope):
    from backbone.site_admin import require_active_site
    require_active_site(scope['site_id'],lock=True)
    item=one(Resource.objects.select_for_update().filter(**scope),data['id']) if data.get('id') else Resource(**scope)
    if item.pk: revision(item,data)
    item.name=string(data,'name',180)
    item.timezone=string(data,'timezone',80)
    try:ZoneInfo(item.timezone)
    except (ValueError,ZoneInfoNotFoundError):raise Problem('invalid_timezone',field='timezone')
    for key in ('opens','closes'):
        try:
            value=time.fromisoformat(data[key])
            if value.tzinfo or value.second or value.microsecond:raise ValueError
            setattr(item,key,value)
        except (KeyError,TypeError,ValueError):raise Problem('invalid_time',field=key)
    item.weekdays=data.get('weekdays')
    if not isinstance(item.weekdays,list) or not item.weekdays or not all(type(i)==int and 0<=i<=6 for i in item.weekdays):raise Problem('invalid_weekdays',field='weekdays')
    item.slot_minutes=data.get('slot_minutes')
    if type(item.slot_minutes)!=int or not 1<=item.slot_minutes<=1440 or item.opens>=item.closes:raise Problem('invalid_schedule',field='slot_minutes')
    item.save()
    return item,'resource.saved'

def book(data,scope):
    from backbone.site_admin import require_active_site
    require_active_site(scope['site_id'],lock=True)
    if 'id' in data:
        item=one(Appointment.objects.select_for_update().filter(**scope),data['id'])
        revision(item,data)
        new=data.get('status')
        if new not in {'scheduled':('confirmed','cancelled'),'confirmed':('completed','cancelled','no_show')}.get(item.status,()):raise Problem('invalid_transition',409)
        if new=='cancelled':
            from billing_core.models import Invoice
            if Invoice.objects.filter(appointment=item).exclude(status='void').exists():
                raise Problem('appointment_invoiced',409)
        item.status=new
        item.save()
        return item,'appointment.'+new
    provider=one(Resource.objects.select_for_update().filter(**scope),data.get('resource_id'))
    party=one(Party.objects.filter(**scope,active=True),data.get('party_id'))
    try:
        start=datetime.fromisoformat(data['start'])
        if not start.tzinfo:raise ValueError
        start=start.astimezone(timezone.utc)
    except (TypeError,KeyError,ValueError):raise Problem('invalid_start',field='start')
    end=start+timedelta(minutes=provider.slot_minutes)
    zone=ZoneInfo(provider.timezone)
    local=start.astimezone(zone)
    finish=end.astimezone(zone)
    elapsed=local.hour*60+local.minute-provider.opens.hour*60-provider.opens.minute
    if (local.weekday() not in provider.weekdays or local.second or local.microsecond or elapsed<0 or elapsed%provider.slot_minutes or
        local.date()!=finish.date() or finish.time()>provider.closes or local.utcoffset()!=finish.utcoffset()):
        raise Problem('outside_working_hours',field='start')
    if Appointment.objects.filter(resource=provider,start__lt=end,end__gt=start).exclude(status='cancelled').exists():raise Problem('slot_unavailable',409)
    return Appointment.objects.create(**scope,resource=provider,party=party,start=start,end=end),'appointment.created'
resources=collection('scheduling',Resource,[f.attname for f in Resource._meta.fields],['name'],resource)
appointments=collection('scheduling',Appointment,[f.attname for f in Appointment._meta.fields]+['resource__name','party__name_en','party__name_ar'],['resource__name','party__name_en','party__name_ar','status'],book)
