import logging,json
from uuid import uuid4
from contextvars import ContextVar
current=ContextVar('request_id',default=None)
def record(event,**fields):
    logging.getLogger('django.server').info(json.dumps(dict(request_id=current.get(),event=event,**fields)))
class RequestID:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        token=current.set(str(uuid4()))
        try:
            response=self.get_response(request)
            response['X-Request-ID']=current.get()
            if response.status_code in (401,403):record('security.denied',status=response.status_code)
            return response
        finally:current.reset(token)
