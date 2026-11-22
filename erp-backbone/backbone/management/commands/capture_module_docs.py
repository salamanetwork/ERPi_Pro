import os
from pathlib import Path
from urllib.parse import urlsplit
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError

class Command(BaseCommand):
    help='Capture bilingual guide illustrations from a local test installation with synthetic records.'
    def add_arguments(self,p):
        p.add_argument('--base-url',default='http://127.0.0.1:8001')
        p.add_argument('--module')
    def handle(self,base_url,module,**kwargs):
        u=urlsplit(base_url)
        if u.scheme not in ('http','https') or u.hostname not in ('localhost','127.0.0.1','::1') or u.username or u.path.strip('/'):
            raise CommandError('Use a localhost test origin.')
        try:from playwright.sync_api import sync_playwright, Error
        except ImportError:raise CommandError('Install Playwright with python -m pip install playwright and python -m playwright install chromium.')
        with sync_playwright() as p:
            try:browser=p.chromium.launch()
            except Error:raise CommandError('Install a usable browser with python -m playwright install --with-deps chromium.')
            try:
                ctx=browser.new_context(base_url=base_url,viewport={'width':1440,'height':960},timezone_id='UTC',reduced_motion='reduce')
                ctx.request.get('/api/session')
                csrf=lambda: next((v['value'] for v in ctx.cookies() if v['name']=='csrftoken'),'')
                def post(path,data):
                    return ctx.request.post(path,data=data,headers={'X-CSRFToken':csrf()})
                login=post('/api/login',dict(username=os.getenv('ERP_DOCS_USER',''),password=os.getenv('ERP_DOCS_PASSWORD','')))
                if not login.ok:raise CommandError('Set ERP_DOCS_USER and ERP_DOCS_PASSWORD for the test account.')
                if login.json().get('mfa'):
                    import pyotp
                    secret=os.getenv('ERP_DOCS_TOTP')
                    if not secret or not post('/api/login/mfa',{'code':pyotp.TOTP(secret).now()}).ok:raise CommandError('Provide the enrolled test account ERP_DOCS_TOTP.')
                response=ctx.request.get('/api/platform/v1/sop-guides')
                if not response.ok:raise CommandError('Could not load permitted guides.')
                guides=response.json()['results']
                page=ctx.new_page()
                for lang in ('en','ar'):
                    # Override only the screenshot browser's workspace language.
                    def localize(route):
                        r=route.fetch();data=r.json();data['language']=lang;route.fulfill(response=r,json=data)
                    page.route('**/api/workspace',localize)
                    for guide in guides:
                        if module and guide['module']!=module:continue
                        for step in guide['steps']:
                            if not step.get('screenshot'):continue
                            dest=settings.BASE_DIR/'backbone/static'/Path(step['screenshot']).with_suffix('.'+lang+'.png')
                            page.goto(guide['route'],wait_until='networkidle')
                            page.wait_for_selector('html[lang="'+lang+'"] h1')
                            dest.parent.mkdir(parents=True,exist_ok=True)
                            page.screenshot(path=str(dest),animations='disabled')
                            self.stdout.write(str(dest))
                    page.unroute('**/api/workspace',localize)
            finally:browser.close()
