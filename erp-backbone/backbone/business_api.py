from .access import audit
from .business_scope import page,select_scope
from .http import endpoint,body,once
from .validation import Problem
from .module_events import publish

def collection(module, model, fields, search, write, permission='manage'):
    @endpoint('GET','POST',module=module)
    def view(request):
        if request.method=='GET':
            scope=select_scope(request,request.GET,module+'.view')
            rows=model.objects.filter(**scope).order_by('-pk').values(*fields)
            return page(request.GET,rows,search)
        data=body(request)
        scope=select_scope(request,data,module+'.'+permission)
        def change():
            item,event=write(data,scope)
            audit(request.user,module+'.'+event,item.pk)
            from .registry import MANIFESTS
            if event in MANIFESTS[module]['events_published']:
                publish(module,event,item.pk,f'{module}:{event}:{item.pk}:{item.revision}')
            return {'id':item.pk,'revision':item.revision}
        return once(request,module+'.'+model._meta.model_name,data,change)
    return view

def choice(data,field,options):
    value=data.get(field)
    if value not in options:raise Problem('validation_failed',field=field)
    return value

def choices(module):
    @endpoint('GET',module=module)
    def lookup(request,kind):
        from publication.models import PublicationSpace
        from parties.models import Party
        from scheduling.models import Appointment,Resource
        from .http import require
        scope=select_scope(request,request.GET,module+'.view')
        if kind=='spaces':
            rows=PublicationSpace.objects.filter(internal_site_id=scope['site_id'],active=True)
            return page(request.GET,rows.order_by('name','id').values('id','name'),['name'])
        if kind=='parties' and module in ('scheduling','billing_core'):
            rows=Party.objects.filter(**scope,active=True)
            return page(request.GET,rows.order_by('id').values('id','name_en','name_ar'),['name_en','name_ar'])
        if kind=='resources' and module=='scheduling':
            return page(request.GET,Resource.objects.filter(**scope).order_by('id').values('id','name'),['name'])
        if kind=='appointments' and module=='billing_core':
            from .http import identifier
            raw=request.GET.get('party_id','')
            if not raw.isascii() or not raw.isdigit() or len(raw)>18:raise Problem('invalid_party',field='party_id')
            rows=Appointment.objects.filter(**scope,party_id=int(raw))
            return page(request.GET,rows.order_by('-start').values('id','start','resource__name'),['resource__name'])
        raise Problem('not_found',404)
    return lookup
