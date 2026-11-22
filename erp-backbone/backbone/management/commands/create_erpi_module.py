"""Generate a trusted Django app and its local Vue workspace."""
import json
import keyword
import os
import re
import tempfile
from pathlib import Path
from importlib.util import find_spec
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from backbone.registry import MANIFESTS


class Command(BaseCommand):
    help="Generate an installed but disabled ERPi module with a bilingual contract, migration, Vue view and tests."

    def add_arguments(self,parser):
        parser.add_argument("module_slug")
        parser.add_argument("--name-en",required=True)
        parser.add_argument("--name-ar",required=True)
        parser.add_argument("--scope",choices=["site","space","global"],default="site")

    def handle(self,module_slug,name_en,name_ar,scope,**options):
        root=settings.BASE_DIR
        modules_root=getattr(settings,"MODULES_DIR",root/"modules")
        modules_root.mkdir(exist_ok=True)
        target=modules_root/module_slug
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,29}",module_slug) or keyword.iskeyword(module_slug):
            raise CommandError("Use a Python module name with 2–30 lowercase letters, digits or underscores.")
        if target.exists() or module_slug in MANIFESTS or find_spec(module_slug) is not None:
            raise CommandError("This module name is already in use.")
        name={"en":name_en.strip(),"ar":name_ar.strip()}
        if not all(0<len(n)<=120 for n in name.values()):raise CommandError("Provide both module names (1–120 characters).")
        slug=module_slug
        permissions={}
        for action,en,ar,level in (("view","View","عرض","read"),("create","Create","إنشاء","write")):
            permissions[f"{slug}.{action}"]={"name_en":f"{en} {name['en']} records","name_ar":f"{ar} سجلات {name['ar']}",
                "description_en":f"Allows the role holder to {action} records in {name['en']} within the assigned scope.",
                "description_ar":f"يتيح {ar} سجلات {name['ar']} ضمن النطاق المعين.","action_type":level}
        deps=["publication"] if scope=="space" else []
        manifest={"contract_version":"1.0","id":slug,"name":name,"version":"0.1.0","description":name,"category":"domain_module","group":"General",
            "scope_mode":scope+"_scoped" if scope!="global" else "global","platform_compatibility":"0.1","core_version_range":">=2.8,<2.10",
            "dependencies":deps,"required_dependencies":deps,"optional_dependencies":[],"runtime":"internal","permissions":list(permissions),
            "permission_definitions":permissions,"ui_entries":["/"+slug],"navigation":[{"route":"/"+slug,"label":name,"required_permission":slug+".view"}],
            "events_published":["created"],"events_subscribed":[slug+".created"],"requested_service_permissions":[],"offline_requirements":[]}
        manifest["sop_guides"]=[{"id":"browse","title_en":"Browse "+name_en,"title_ar":"استعراض "+name_ar,"keywords_en":["list","search"],"keywords_ar":["قائمة","بحث"],"required_permission":slug+".view","steps":[{"id":"browse-records","text_en":"Select the scope and search the records.","text_ar":"حدد النطاق وابحث في السجلات.","fields":[]}]}]
        model={"site":"backbone.Site","space":"publication.PublicationSpace"}.get(scope)
        field=f'{scope} = models.ForeignKey("{model}",on_delete=models.PROTECT,related_name="+")' if model else ""
        with tempfile.TemporaryDirectory(prefix=".erpi-module-",dir=modules_root) as temporary:
            folder=Path(temporary)/slug
            folder.mkdir()
            def put(path,data):
                file=folder/path
                file.parent.mkdir(parents=True,exist_ok=True)
                file.write_text(data,encoding="utf-8")
            put("manifest.json",json.dumps(manifest,indent=2,ensure_ascii=False)+"\n")
            put("__init__.py","")
            put("erpi_module.marker","1.0\n")
            put("migrations/__init__.py","")
            for src,dest in [("models.py","models.py"),("events.py","events.py"),("views.py","views.py"),("tests.py","tests.py"),("page.vue","ui/ModulePage.vue")]:
                template=(root/"backbone/scaffold"/(src+".tpl")).read_text()
                put(dest,template.replace("__SLUG__",slug).replace("__SCOPE_FIELD__",field).replace("__SCOPE__",scope))
            scope_path=""
            if scope=="space":
                with (folder/"views.py").open("a") as f:
                    f.write('''
@endpoint("GET",module=MODULE)
def available_spaces(request):
    from publication.models import PublicationSpace
    allowed=scopes(request.user,MODULE+".create") | scopes(request.user,MODULE+".view")
    if not allowed:raise Problem("forbidden",403)
    rows=PublicationSpace.objects.filter(active=True,internal_site__active=True)
    if None not in allowed:rows=rows.filter(internal_site_id__in=allowed)
    return {"results":list(rows.order_by("name","pk").values("id","name","internal_site_id"))}
''')
                scope_path=f',path("api/{slug}/scopes",views.available_spaces)'
            put("urls.py",f'from django.urls import path\nfrom . import views\nurlpatterns=[path("api/{slug}/records",views.records){scope_path}]\n')
            dependencies=[("backbone","0017_module_event_delivery")]
            if scope=="space":dependencies.append(("publication","0004_sitemap_outbox"))
            fk=f',("{scope}",models.ForeignKey(to="{model.lower()}",on_delete=models.PROTECT,related_name="+"))' if model else ""
            put("migrations/0001_initial.py",f'''from django.db import migrations,models


class Migration(migrations.Migration):
    initial=True
    dependencies={dependencies!r}
    operations=[
        migrations.CreateModel(name="Record",fields=[("id",models.BigAutoField(primary_key=True,serialize=False)),("name",models.CharField(max_length=160)){fk}]),
        migrations.CreateModel(name="EventReceipt",fields=[("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
            ("event",models.OneToOneField(to="backbone.outboxevent",on_delete=models.PROTECT,related_name="+")),("received_at",models.DateTimeField(auto_now_add=True))]),
    ]
''')
            os.rename(folder,target)
        self.stdout.write(self.style.SUCCESS(f"Created {target}. Run migrate, build frontend/dist and restart Django and workers; enable the module in Module Manager."))
