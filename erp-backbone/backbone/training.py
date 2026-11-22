from pathlib import Path
from django.conf import settings
from .http import endpoint
from .registry import MANIFESTS,enabled_modules
from .access import has_permission

@endpoint('GET')
def guides(request):
    items=[]
    for module in sorted(enabled_modules()|{'platform'}):
        manifest=MANIFESTS.get(module,{})
        for guide in manifest.get('sop_guides',[]):
            if has_permission(request.user,guide['required_permission']):
                steps=[]
                for i,step in enumerate(guide['steps']):
                    urls={}
                    if step.get('screenshot'):
                        for lang in ('en','ar'):
                            rel=Path(step['screenshot']).with_suffix('.'+lang+'.png')
                            if (settings.BASE_DIR/'backbone/static'/rel).is_file():
                                urls[lang]=settings.STATIC_URL+rel.as_posix()
                    steps.append({**step,'screenshots':urls})
                items.append({**guide,'module':module,'module_name':manifest['name'],'steps':steps})
    return {'results':items}
