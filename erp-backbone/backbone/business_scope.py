from django.core.paginator import Paginator
from django.db import models
from django.db.models import Q
from .http import require, identifier
from .validation import Problem

class ScopedRecord(models.Model):
    site = models.ForeignKey('backbone.Site', on_delete=models.PROTECT, related_name='+')
    space = models.ForeignKey('publication.PublicationSpace', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    revision = models.PositiveIntegerField(default=1)
    class Meta:
        abstract = True

def select_scope(request, data, permission):
    from .models import Site
    from publication.models import PublicationSpace
    def number(key):
        value = data.get(key)
        if isinstance(value,str) and value.isascii() and value.isdigit() and len(value)<=19:
            value = int(value)
        return identifier(value,key)
    site = number('site_id')
    require(request.user, permission, site)
    if not Site.objects.filter(pk=site, active=True).exists():
        raise Problem('invalid_site',field='site_id')
    space = number('space_id') if data.get('space_id') not in (None,'') else None
    if space and not PublicationSpace.objects.filter(pk=space, internal_site_id=site, active=True).exists():
        raise Problem('forbidden',403)
    return {'site_id':site,'space_id':space}

def page(query, rows, fields=()):
    search = query.get('q','').strip()[:200]
    if search and fields:
        match=Q()
        for field in fields:
            match |= Q(**{field+'__icontains':search})
        rows=rows.filter(match)
    try:
        size=int(query.get('page_size',25))
        index=int(query.get('page',1))
        if size not in (10,25,50) or index<1: raise ValueError
    except (ValueError,TypeError):
        raise Problem('invalid_page',field='page')
    paginator=Paginator(rows,size)
    result=paginator.get_page(index)
    return dict(count=paginator.count,page=result.number,page_size=size,next=result.next_page_number() if result.has_next() else None,
                previous=result.previous_page_number() if result.has_previous() else None,results=list(result))

def one(rows,pk):
    item=rows.filter(pk=identifier(pk,'id')).first()
    if item is None: raise Problem('not_found',404)
    return item

def revision(record,data):
    if type(data.get('revision')) is not int or record.revision != data['revision']:
        raise Problem('revision_conflict',409)
    record.revision+=1
