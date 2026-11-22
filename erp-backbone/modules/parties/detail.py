from backbone.http import endpoint
from backbone.access import has_permission
from backbone.business_scope import select_scope,one
from backbone.registry import enabled_modules
from billing_core.models import Invoice,Receipt
from scheduling.models import Appointment
from crm.models import Lead
from sales.models import Customer,Document
from purchasing.models import Supplier, PurchaseOrder
from public_identity.models import ExternalIdentity
from .models import Party

@endpoint('GET',module='parties')
def detail(request,pk):
    scope=select_scope(request,request.GET,'parties.view')
    party=one(Party.objects.filter(**scope),pk)
    active=enabled_modules()
    sections={}
    def add(key,module,permission,query,*fields):
        if module in active and has_permission(request.user,permission,party.site_id):
            sections[key]=list(query.order_by('-pk').values(*fields))
    invoices=Invoice.objects.filter(party=party,**scope)
    add('appointments','scheduling','scheduling.view',Appointment.objects.filter(party=party,**scope),'resource__name','start','status')
    add('invoices','billing_core','billing_core.view',invoices,'number','total','currency','status')
    add('receipts','billing_core','billing_core.view',Receipt.objects.filter(invoice__in=invoices,**scope),'number','amount','invoice__currency')
    add('customers','customers','sales.view',Customer.objects.filter(party=party),'name','code')
    add('suppliers','suppliers','suppliers.view',Supplier.objects.filter(party=party),'name','code')
    add('sales','sales','sales.view',Document.objects.filter(customer__party=party,site_id=party.site_id),'id','kind','status')
    add('purchases','purchase_orders','purchasing.view',PurchaseOrder.objects.filter(supplier__party=party,site_id=party.site_id),'number','title','status')
    add('leads','crm','crm.view',Lead.objects.filter(party=party,site_id=party.site_id),'id','name','state')
    add('portal','public_identity','public_identity.view',ExternalIdentity.objects.filter(party=party,externalmembership__space_id=party.space_id,externalmembership__active=True),'display_name','status')
    return {'id':party.pk,'account_id':party.account_id,'account_label':(party.account.display_name+' · '+party.account.normalized_email) if party.account_id and has_permission(request.user,'public_identity.view',party.site_id) else '', 'name_en':party.name_en,'name_ar':party.name_ar,'sections':sections}
