from django.utils import timezone
from .models import Contract

def handle_event(event):
    """Wake collection checks after a payment or void in the document owner."""
    if event.module=='billing_core' and event.event in ('invoice.paid','invoice.void','receipt.recorded'):
        lookup='cycle__invoice__receipt__pk' if event.event=='receipt.recorded' else 'cycle__invoice_id'
        Contract.objects.filter(**{lookup:event.object_id}).update(next_check=timezone.now())
    from .models import Cycle
    from .gl import sync
    if event.module == 'subscriptions' and event.event in ('cycle.billed','recognition.due'):
        sync(event.object_id)
    elif event.module == 'billing_core':
        lookup = {'invoice__receipt__pk':event.object_id} if event.event=='receipt.recorded' else {'invoice_id':event.object_id}
        for pk in Cycle.objects.filter(**lookup).values_list('pk',flat=True):
            sync(pk)
    elif event.module == 'subscriptions' and event.event == 'credit.created':
        from .models import Adjustment
        row = Adjustment.objects.get(pk=event.object_id)
        for cycle in Cycle.objects.filter(contract=row.contract,start__lte=row.effective,end__gt=row.effective):
            sync(cycle.pk)
