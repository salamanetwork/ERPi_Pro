from io import BytesIO
from uuid import UUID
from PIL import Image,ImageOps
from django.core.files.storage import storages
from django.core.paginator import Paginator
from django.db import IntegrityError,transaction
from django.db.models import Count,F,Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError,NotFound
from rest_framework.response import Response
from public_identity.security import enforce_csrf
from public_identity.services import event
from .models import MediaAsset,MediaFolder
from .services import metadata
from .views import CustomerMedia


def owned(request):
    return dict(space_id=request.auth.space_id,external_owner_id=request.user.pk,deleted_at=None)

def summaries(request):
    return list(MediaFolder.objects.filter(**owned(request)).annotate(count=Count('mediaasset',filter=Q(mediaasset__deleted_at=None,mediaasset__external_owner_id=request.user.pk,mediaasset__space_id=request.auth.space_id))).order_by('name','id').values('id','name','count','revision'))

def folder(request,value,lock=False):
    try: pk=UUID(str(value))
    except ValueError:raise NotFound()
    rows=MediaFolder.objects.select_for_update() if lock else MediaFolder.objects
    return get_object_or_404(rows,pk=pk,**owned(request))

def stale(request,row):
    return 'revision' in request.data and request.data['revision']!=row.revision

def listing(request,rows):
    raw=request.GET.get('folder')
    if raw:rows=rows.filter(folder=None if raw=='unfiled' else folder(request,raw))
    rows=rows.filter(original_name__icontains=request.GET.get('q','')[:150]).order_by('-created_at','pk')
    try:
        size=int(request.GET.get('page_size',25));index=int(request.GET.get('page',1))
        if size not in (10,25,50) or index<1:raise ValueError
    except ValueError:raise ValidationError({'page':['Invalid pagination.']})
    p=Paginator(rows,size).get_page(index)
    return Response(dict(count=p.paginator.count,page=p.number,page_size=size,next=f'?page={p.number+1}' if p.has_next() else None,previous=f'?page={p.number-1}' if p.has_previous() else None,results=[metadata(r) for r in p],folders=summaries(request)))

from public_identity.views import CustomerView
from backbone.registry import enabled_modules

class Folders(CustomerView):
    def initial(self,request,*args,**kwargs):
        super().initial(request,*args,**kwargs)
        if 'media_service' not in enabled_modules():raise NotFound()
        if request.method not in ('GET','HEAD','OPTIONS'):enforce_csrf(request)
    def get(self,request,pk=None):
        if pk:raise NotFound()
        return Response({'results':summaries(request)})
    @transaction.atomic
    def save_folder(self,request,pk=None):
        row=folder(request,pk,True) if pk else MediaFolder(**owned(request))
        if pk and stale(request,row):return Response({'error':'revision_conflict'},status=409)
        name=request.data.get('name')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise ValidationError({'name':['Invalid name.']})
        row.name=name.strip()
        if pk:row.revision+=1
        try:
            with transaction.atomic():row.save()
        except IntegrityError:return Response({'error':'duplicate_name'},status=409)
        event(request.user.identity,'folder_saved',row.pk)
        return Response({'id':row.pk,'name':row.name,'revision':row.revision},status=200 if pk else 201)
    def post(self,request,pk=None):
        if pk:raise NotFound()
        return self.save_folder(request)
    def patch(self,request,pk=None):
        if not pk:raise NotFound()
        return self.save_folder(request,pk)
    @transaction.atomic
    def delete(self,request,pk=None):
        row=folder(request,pk,True)
        if stale(request,row):return Response({'error':'revision_conflict'},status=409)
        MediaAsset.objects.filter(folder=row,**owned(request)).update(folder=None,revision=F('revision')+1)
        row.deleted_at=timezone.now();row.revision+=1;row.save()
        event(request.user.identity,'folder_deleted',row.pk)
        return Response(status=204)

class Files(CustomerMedia):
    @transaction.atomic
    def patch(self,request,pk=None):
        enforce_csrf(request)
        if 'folder_id' not in request.data:raise ValidationError({'folder_id':['Required.']})
        target=folder(request,request.data['folder_id'],True) if request.data['folder_id'] is not None else None
        asset=get_object_or_404(MediaAsset.objects.select_for_update(),pk=pk,**owned(request))
        if stale(request,asset):return Response({'error':'revision_conflict'},status=409)
        asset.folder=target;asset.revision+=1;asset.save(update_fields=['revision','folder'])
        event(request.user.identity,'file_moved',asset.pk)
        return Response(metadata(asset))

class Thumbnail(CustomerView):
    def get(self,request,pk):
        if 'media_service' not in enabled_modules():raise NotFound()
        row=get_object_or_404(MediaAsset,pk=pk,mime__in=['image/png','image/jpeg','image/webp'],**owned(request))
        try:
            with storages[row.storage_alias].open(row.object_key,'rb') as f, Image.open(f) as img:
                if img.width*img.height>25000000:raise NotFound()
                img.thumbnail((160,160));im=ImageOps.exif_transpose(img).convert('RGBA')
                out=BytesIO();im.save(out,format='PNG')
        except (OSError,Image.DecompressionBombError):raise NotFound()
        r=HttpResponse(out.getvalue(),content_type='image/png')
        r['Content-Security-Policy']="default-src 'none'; sandbox"
        r['X-Content-Type-Options']='nosniff'
        return r
    def finalize_response(self,*args,**kw):
        r=super().finalize_response(*args,**kw);r['Cache-Control']='private, no-store';return r
