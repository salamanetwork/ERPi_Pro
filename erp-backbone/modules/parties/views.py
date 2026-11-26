from uuid import UUID
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from backbone.business_api import collection,choice
from backbone.business_scope import one,revision
from backbone.http import string
from backbone.validation import Problem
from public_identity.models import ExternalMembership
from .models import Party

def save(data,scope):
    item=one(Party.objects.select_for_update().filter(**scope),data['id']) if 'id' in data else Party(**scope)
    event='party.updated' if item.pk else 'party.created'
    if item.pk: revision(item,data)
    item.party_type=choice(data,'party_type',('customer','supplier','contact','both'))
    for key,limit in [('name_en',180),('name_ar',180),('phone',40),('email',254),('identity_number',80),('address',2000),('notes',4000)]:
        setattr(item,key,string(data,key,limit,required=key.startswith('name_')))
    if item.email:
        try:validate_email(item.email)
        except ValidationError:raise Problem('invalid_email',field='email')
    item.active=data.get('active',True)
    if type(item.active)!=bool:raise Problem('invalid_active',field='active')
    account=data.get('account_id')
    if account:
        try:account=UUID(str(account))
        except ValueError:raise Problem('invalid_account',field='account_id')
        if not ExternalMembership.objects.filter(identity_id=account,space_id=scope['space_id'],active=True,identity__status='active').exists():
            raise Problem('invalid_account',field='account_id')
    from django.db.models import F
    from public_identity.models import ExternalIdentity
    account_ids = sorted({uid for uid in (item.account_id, account) if uid})
    locked_identities = {
        obj.pk: obj for obj in ExternalIdentity.objects.select_for_update().filter(pk__in=account_ids).order_by("pk")
    }
    if item.account_id and item.account_id != account:
        ExternalIdentity.objects.filter(pk=item.account_id,party=item).update(party=None,revision=F('revision')+1)
    identity = locked_identities.get(account) if account else None
    if identity and (identity.party_id not in (None,item.pk) or Party.objects.select_for_update().filter(account=identity).exclude(pk=item.pk).exists()):
        raise Problem('account_already_linked',409,field='account_id')
    item.account_id=account or None
    item.save()
    if identity and identity.party_id != item.pk:
        ExternalIdentity.objects.filter(pk=identity.pk).update(party=item,revision=F('revision')+1)
    return item,event
_records=collection('parties',Party,[f.attname for f in Party._meta.fields],['name_en','name_ar','email','phone'],save)

from backbone.http import endpoint
from backbone.business_scope import select_scope,page
from backbone.access import has_permission

@endpoint('GET','POST',module='parties')
def records(request):
    if request.method=='POST':return _records(request)
    scope=select_scope(request,request.GET,'parties.view')
    rows=Party.objects.filter(**scope).select_related('account').order_by('-pk')
    data=page(request.GET,rows,['name_en','name_ar','email','phone'])
    permitted=has_permission(request.user,'public_identity.view',scope['site_id'])
    def serialize(row):
        return {**{f.attname:getattr(row,f.attname) for f in Party._meta.fields},
            'account_label':(row.account.display_name+' · '+row.account.normalized_email) if permitted and row.account_id else ''}
    data['results']=[serialize(r) for r in data['results']]
    return data
