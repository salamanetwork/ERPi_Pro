from datetime import timedelta
from django.db import DatabaseError,connection,transaction
from django.http import JsonResponse
from django.utils import timezone
from redis.exceptions import RedisError
from rest_framework.response import Response
from public_identity.views import CustomerView
from backbone.services.outbox import enqueue
from backbone.validation import Problem
from .delivery import cache,comment_entry,site
from .fields import text
from .models import PublicSubmission


class Submit(CustomerView):
    def dispatch(self,*args,**kwargs):
        try:return super().dispatch(*args,**kwargs)
        except DatabaseError:return JsonResponse({"error":"temporarily_unavailable"},status=503)

    def post(self,request,code,slug):
        space=site(code)
        if space.pk!=request.auth.space_id:return Response({"error":"forbidden"},status=403)
        try:value=text(request.data.get("body_text") if isinstance(request.data,dict) else None,"body_text",2000,2)
        except Problem as ex:return Response(ex.response(),status=ex.status)
        ident=request.user.pk
        client=cache()
        key="erpi:comment:"+str(ident)
        try:
            if client.exists(key):return Response({"error":"rate_limited"},status=429)
        except (RedisError,OSError):pass
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)",[int.from_bytes(ident.bytes[:8],"big",signed=True)])
            row=comment_entry(request,space,slug,lock=True)
            now=timezone.now()
            recent=list(PublicSubmission.objects.filter(external_identity_id=ident,created_at__gt=now-timedelta(hours=1))
                .order_by("-created_at").values_list("created_at",flat=True)[:5])
            if len(recent)>=5 or (recent and recent[0]>now-timedelta(seconds=30)):return Response({"error":"rate_limited"},status=429)
            item=PublicSubmission.objects.create(space=space,entry=row,external_identity_id=ident,author_display_name=request.user.identity.display_name,body_text=value)
            enqueue(key=f"publication:{item.pk}:pending",module="publication",object_id=space.pk,event="comment_pending",recipients=[])
            seconds=30 if len(recent)<4 else max(30,int((recent[-1]+timedelta(hours=1)-now).total_seconds()))
            def remember():
                try:client.setex(key,seconds,'1')
                except (RedisError,OSError):pass
            transaction.on_commit(remember)
        return Response({"id":str(item.pk),"status":"pending"},status=201)
