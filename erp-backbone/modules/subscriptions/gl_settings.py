from django.db import transaction
from accounting.models import Account, Ledger
from backbone.access import audit
from backbone.business_scope import revision, select_scope
from backbone.http import body, endpoint, once, require, identifier
from backbone.models import Site
from backbone.validation import Problem
from .gl import KINDS, validate
from .models import Cycle, SubscriptionGLMapping, SubscriptionGLPosting

@endpoint('GET','POST',module='subscriptions')
def settings(request):
    data = request.GET if request.method=='GET' else body(request)
    scope = select_scope(request,data,'subscriptions.view')
    site = scope['site_id']
    if request.method == 'GET':
        mapping = SubscriptionGLMapping.objects.filter(site_id=site).first()
        rows = Cycle.objects.filter(contract__site_id=site,contract__space_id=scope['space_id'])
        from django.db.models import Q
        pending = rows.filter(Q(schedule__isnull=True)|~Q(schedule__warning=''))
        accounts = Account.objects.filter(ledger_id=1,active=True,detail=True).order_by('code')
        require(request.user,'accounting.post',site)
        return dict(revision=mapping.revision if mapping else 0,
                    values={key:getattr(mapping,key+'_id') for key in KINDS} if mapping else {},
                    accounts=list(accounts.values('id','code','name','kind')),
                    pending_count=pending.count(), pending=list(pending.values('id','invoice__number','schedule__warning')[:50]))
    require(request.user,'accounting.post',site)
    require(request.user,'subscriptions.override',site)
    def save():
        Site.objects.select_for_update().get(pk=site)
        mapping = SubscriptionGLMapping.objects.select_for_update().filter(site_id=site).first() or SubscriptionGLMapping(site_id=site)
        revision(mapping,data)
        for key in KINDS:
            account = Account.objects.filter(pk=identifier(data.get(key),key)).first()
            if account is None:
                raise Problem('invalid_gl_account',field=key)
            if mapping.pk and getattr(mapping,key+'_id') != account.pk and SubscriptionGLPosting.objects.filter(cycle__contract__site_id=site).exists():
                raise Problem('subscription_mapping_in_use',409)
            setattr(mapping,key,account)
        ledger = Ledger.objects.get(pk=1)
        validate(mapping, ledger.currency)
        mapping.save()
        audit(request.user,'subscriptions.gl_mapping',site)
        return dict(revision=mapping.revision)
    return once(request,'subscriptions.gl_mapping',data,save)
