from datetime import timedelta
from celery import shared_task
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import F,Q
from django.utils import timezone
from kombu.exceptions import OperationalError
from redis import RedisError
from backbone.celeryapp import app
from backbone.module_events import publish
from backbone.registry import enabled_modules
from . import engine
from .models import Contract,Cycle,DunningPolicy,Notice

def outstanding(c):
    return Cycle.objects.filter(contract=c,invoice__status__in=('issued','partially_paid'),invoice__total__gt=F('invoice__paid')).order_by('due','pk')

@shared_task(name='subscriptions.process')
@transaction.atomic
def process(pk,expected_revision=None):
    if not {'subscriptions','billing_core','parties','accounting'}<=enabled_modules():return
    c=Contract.objects.select_for_update().get(pk=pk)
    if not c.site.active or not c.party.active or (c.space_id and not c.space.active):return
    if expected_revision is not None and c.revision!=expected_revision:return
    day=engine.today(c)
    if c.status=='cancelled':return
    if c.cancel_on and c.cancel_on<=day:
        c.status='cancelled'
    else:
        policy=DunningPolicy.objects.filter(site=c.site).first() or DunningPolicy()
        debt=outstanding(c).filter(due__lt=day).first()
        status=c.status
        if debt:
            age=(day-debt.due).days
            if status=='active':status='past_due'
            for stage,threshold in enumerate((policy.first,policy.second,policy.final),1):
                if age<threshold:break
                notice,_=Notice.objects.get_or_create(cycle=debt,stage=stage)
                if notice.delivered_at:continue
                publish('subscriptions','notice.queued',notice.pk,f'subscriptions:notice:{notice.pk}')
                break
            if age>=policy.suspend and Notice.objects.filter(cycle=debt,stage=3,delivered_at__isnull=False).exists():
                status='suspended'
        elif status=='past_due':status='active'
        if c.status==status:
            if c.next_date<=day and status in ('active','trial','past_due'):
                engine.bill(pk,c.next_date)
            return
        c.status=status
    c.revision+=1
    c.save(update_fields=['status','revision'])
    engine.record(c,c.status)

@shared_task(name='subscriptions.send_notice')
@transaction.atomic
def send_notice(pk):
    if 'subscriptions' not in enabled_modules():return
    notice=Notice.objects.select_for_update().get(pk=pk)
    cycle=notice.cycle
    c=cycle.contract
    if notice.delivered_at or c.status=='cancelled' or not outstanding(c).filter(pk=cycle.pk).exists():return
    address=c.party.email
    if not address or not c.party.active or not c.site.active:return
    doc=cycle.invoice
    msg=EmailMessage('Invoice reminder / تذكير بالفاتورة',
        f'{doc.number}\nOutstanding / المبلغ المستحق: {doc.total-doc.paid} {doc.currency}',
        to=[address],headers={'Message-ID':f'<subscription-notice-{pk}@erpi.local>'})
    if msg.send()==1:
        notice.delivered_at=timezone.now()
        notice.save(update_fields=['delivered_at'])

def dispatch(limit=25):
    # The DB lease expires even if the broker drops the message after acknowledgement.
    from .models import SubscriptionRevenueSchedule
    now=timezone.now()
    with transaction.atomic():
        schedules=SubscriptionRevenueSchedule.objects.select_for_update(skip_locked=True).filter(
            complete=False).filter(Q(next_check=None)|Q(next_check__lte=now)).order_by(
                F('next_check').asc(nulls_first=True),'pk')[:limit]
        for schedule in schedules:
            day=engine.today(schedule.cycle.contract)
            schedule.next_check=now+timedelta(hours=1)
            schedule.save(update_fields=['next_check'])
            publish('subscriptions','recognition.due',schedule.cycle_id,
                    f'subscription:recognize:{schedule.cycle_id}:{day}:{now.hour}')

    with transaction.atomic():
        due=list(Contract.objects.select_for_update(skip_locked=True).exclude(status='cancelled')
                 .filter(Q(next_check=None)|Q(next_check__lte=now)).order_by(F('next_check').asc(nulls_first=True),'pk')[:limit])
        for c in due:
            c.next_check=now+timedelta(minutes=2)
            c.save(update_fields=['next_check'])
            publish('subscriptions','cycle.queued',c.pk,f'subscriptions:check:{c.pk}:{c.revision}:{engine.today(c)}')
    try:
        for c in due:
            app.send_task('subscriptions.process',args=[c.pk,c.revision],queue='notifications',retry=False)
        with transaction.atomic():
            pending=list(Notice.objects.select_for_update(skip_locked=True,of=('self',))
                .filter(delivered_at=None,cycle__invoice__status__in=('issued','partially_paid'))
                .exclude(cycle__contract__status='cancelled').filter(Q(retry_at=None)|Q(retry_at__lte=now))
                .order_by(F('retry_at').asc(nulls_first=True),'pk')[:limit])
            for row in pending:
                row.retry_at=now+timedelta(minutes=5)
                row.save(update_fields=['retry_at'])
        for row in pending:
            app.send_task('subscriptions.send_notice',args=[row.pk],queue='notifications',retry=False)
    except (OperationalError,RedisError,OSError):
        return
