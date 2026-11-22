import json
from datetime import timedelta
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from redis.exceptions import RedisError
from kombu.exceptions import OperationalError
from .models import ContentEntry,PublicationSpace,SitemapRefresh


def sitemap_key(space):
    return f"erpi:cms:sitemap:{space.pk}:{space.cache_epoch}"


def sitemap_paths(space):
    from .delivery import path_for
    rows=ContentEntry.objects.filter(space=space,status="published").order_by("pk").iterator()
    return ["/public/site/"+space.code]+[path_for(space,r) for r in rows if not r.published_data["seo_meta"]["robots"].startswith("noindex")]


@shared_task
def warm_sitemap(space_id,epoch):
    from .delivery import cache
    space=PublicationSpace.objects.filter(pk=space_id,cache_epoch=epoch,active=True).first()
    if space:cache().setex(sitemap_key(space),300,json.dumps(sitemap_paths(space)))
    SitemapRefresh.objects.filter(space_id=space_id,epoch=epoch).delete()


def dispatch():
    from backbone.celeryapp import app
    with transaction.atomic():
        job=SitemapRefresh.objects.select_for_update(skip_locked=True).filter(due_at__lte=timezone.now()).order_by("due_at").first()
        if job is None:return
        job.due_at=timezone.now()+timedelta(seconds=60)
        job.save(update_fields=["due_at"])
    try:app.send_task("publication.tasks.warm_sitemap",args=(job.space_id,job.epoch),queue="notifications",retry=False)
    except (OSError,RedisError,OperationalError):
        SitemapRefresh.objects.filter(space_id=job.space_id,epoch=job.epoch).update(due_at=timezone.now()+timedelta(seconds=5))
