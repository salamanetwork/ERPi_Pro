from django.db import transaction
from .http import endpoint,body,Problem
from .models import PersonalMenu
from .navigation import validate_layout

@endpoint('GET','PATCH')
def personal_menu(request):
    if request.method=='GET':
        return PersonalMenu.objects.filter(user=request.user).values('revision','layout').first() or {'revision':0,'layout':{}}
    d=body(request)
    value=validate_layout(d.get('layout'))
    with transaction.atomic():
        row,_=PersonalMenu.objects.select_for_update().get_or_create(user=request.user)
        if type(d.get('revision')) is not int or d['revision']!=row.revision:raise Problem('revision_conflict',409)
        row.layout=value
        row.revision+=1
        row.save(update_fields=['layout','revision'])
        return {'layout':row.layout,'revision':row.revision}
