from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import Client,TestCase
from django.utils import timezone
from redis.exceptions import ConnectionError as Offline
from backbone import mfa
from backbone.models import Authenticator,Site,ModuleState
from media_service.models import MediaAsset
from public_identity.models import ExternalIdentity,ExternalSession,ExternalMembership
from public_identity.services import issue
from .models import PublicationSpace,PublicSubmission,ContentRevision,SitemapRefresh


class PublishingTests(TestCase):
    def setUp(self):
        for name in ("publication","public_identity","media_service"):
            ModuleState.objects.update_or_create(module_id=name,defaults={"enabled":True})
        self.space=PublicationSpace.objects.create(name="Press",code="press",internal_site=Site.objects.create(name="Branch"))
        self.owner=get_user_model().objects.create_superuser("editor")
        self.staff=Client(enforce_csrf_checks=True)
        self.staff.force_login(self.owner)
        device=Authenticator.objects.create(user=self.owner,encrypted_key="test-session-stamp")
        session=self.staff.session
        session[mfa.VERIFIED]=mfa.stamp(device)
        session.save()
        self.staff.get("/admin/users")
        self.root=f"/api/publication/spaces/{self.space.pk}"
        self.row=self.entry()
        self.detail=self.root+"/entries/"+self.row["id"]
        self.url="/public/site/press/posts/first"

    def send(self,url,data,method="post",key=None):
        return getattr(self.staff,method)(url,data,content_type="application/json",HTTP_X_CSRFTOKEN=self.staff.cookies["csrftoken"].value,
            HTTP_IDEMPOTENCY_KEY=key or str(uuid4()))

    def entry(self,**data):
        r=self.send(self.root+"/entries",{"working_slug":"first","locale":"en","entry_type":"post","working_data":{"title":"First edition","body_blocks":[{"type":"paragraph","text":"Public copy"}]},**data})
        self.assertEqual(r.status_code,200,r.content)
        return r.json()

    def transition(self,action,key=None):
        r=self.send(self.detail+"/"+action,{"revision":self.row["revision"]},key=key)
        self.assertIn(r.status_code,(200,201),r.content)
        self.row=r.json()

    def edit(self,slug):
        r=self.send(self.detail,{"revision":self.row["revision"],"working_slug":slug,"working_data":{"title":"Changed"}},"patch")
        self.assertEqual(r.status_code,200,r.content)
        self.row=r.json()

    def test_snapshots_and_slug_history(self):
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.transition("publish","same")
        self.assertEqual(self.send(self.detail+"/publish",{"revision":1},key="same").status_code,200)
        self.assertEqual(ContentRevision.objects.count(),1)
        r=self.client.get(self.url)
        self.assertContains(r,"Public copy")
        self.assertEqual(self.client.get(self.url,HTTP_IF_NONE_MATCH=r["ETag"]).status_code,304)
        self.edit("second")
        self.transition("submit-review")
        self.assertContains(self.client.get(self.url),"Public copy")
        self.assertEqual(self.client.get(self.url.replace("first","second")).status_code,404)
        self.assertEqual(self.send(self.detail+"/publish",{"revision":1}).status_code,409)
        self.transition("publish")
        self.edit("third")
        self.transition("publish")
        r=self.client.get(self.url)
        self.assertEqual(r.status_code,301)
        self.assertTrue(r["Location"].endswith("third?lang=en"))
        self.edit("first")
        self.transition("publish")
        r=self.client.get(self.url)
        self.assertContains(r,"Changed")
        self.assertEqual(self.client.get(self.url+"?lang=ar").status_code,404)
        self.assertEqual(self.client.get(self.url.replace("posts","pages")).status_code,404)
        self.transition("unpublish")
        for slug in ("first","second"):
            self.assertEqual(self.client.get(self.url.replace("first",slug),HTTP_IF_NONE_MATCH=r["ETag"]).status_code,404)
        self.transition("archive")
        self.assertEqual(self.client.get(self.url.replace("first","second")).status_code,410)

    def test_seo_translation_and_menus(self):
        self.transition("publish")
        ar=self.entry(locale="ar",translation_of=self.row["id"],working_data={"title":"مرحبا"})
        self.send(self.root+"/entries/"+ar["id"]+"/publish",{"revision":1})
        menu=self.root+"/menus/en/header"
        self.assertEqual(self.send(menu,{"revision":0,"items":[{"label":"Current edition","target_type":"entry","target_entry":self.row["id"]}]},"put").status_code,200)
        self.assertEqual(self.send(menu,{"revision":0,"items":[]},"put").status_code,409)
        page=self.client.get(self.url)
        for term in ('hreflang="ar"','application/ld+json','rel="canonical"','Current edition'):self.assertContains(page,term)
        self.assertContains(self.client.get(self.url+"?lang=ar"),'dir="rtl"')
        for route in ("/robots.txt","/sitemap.xml","/public/site/press/sitemap.xml","/api/public/v1/press/sitemap.xml"):
            self.assertEqual(self.client.get(route).status_code,200)
        PublicationSpace.objects.filter(pk=self.space.pk).update(active=False)
        self.assertEqual(self.client.get(self.url,HTTP_IF_NONE_MATCH=page["ETag"]).status_code,404)

    def customer(self):
        user=ExternalIdentity.objects.create(normalized_email="reader@example.com",display_name="Reader",status="active",verified_at=timezone.now())
        ExternalMembership.objects.create(identity=user,space=self.space)
        row=ExternalSession.objects.create(identity=user,space=self.space,expires_at=timezone.now()+timedelta(days=1),refresh_jti_hash="test")
        return user,{"HTTP_AUTHORIZATION":"Bearer "+issue(row)[0]["access"]}

    def test_comment_boundaries_and_moderation(self):
        self.transition("publish")
        user,auth=self.customer()
        path="/api/public/v1/press/entries/first/comments"
        self.assertIn(self.staff.post(path,{}).status_code,(401,403))
        for url in (self.root+"/entries","/api/workspace"):self.assertIn(self.client.get(url,**auth).status_code,(401,403))
        def submit():return self.client.post(path,{"body_text":"Reader comment"},content_type="application/json",**auth)
        with patch("publication.comments.cache") as cache:
            cache.return_value.exists.return_value=True
            self.assertEqual(submit().status_code,429)
            cache.return_value.exists.side_effect=Offline()
            r=submit()
            self.assertEqual(r.status_code,201,r.content)
            self.assertEqual(submit().status_code,429)
            self.assertEqual(self.client.get(path).json()["results"],[])
            item=self.root+"/submissions/"+r.json()["id"]
            self.assertEqual(self.send(item+"/approve",{"revision":1}).status_code,201)
            page=self.client.get(path)
            self.assertEqual(len(page.json()["results"]),1)
            self.assertEqual(self.send(item+"/hide",{"revision":2}).status_code,201)
            self.assertEqual(self.client.get(path,HTTP_IF_NONE_MATCH=page["ETag"]).json()["results"],[])
            for _ in range(4):
                PublicSubmission.objects.filter(external_identity_id=user.pk).update(created_at=timezone.now()-timedelta(minutes=2))
                self.assertEqual(submit().status_code,201)
            PublicSubmission.objects.all().update(created_at=timezone.now()-timedelta(minutes=3))
            self.assertEqual(submit().status_code,429)

    def test_media_public_access(self):
        user,_=self.customer()
        for mime,staff,external,expected in (("image/png",self.owner,None,201),("application/pdf",self.owner,None,400),("image/webp",None,user,400)):
            row=MediaAsset.objects.create(space=self.space,staff_owner=staff,external_owner=external,original_name="file",mime=mime,size=8,sha256="a"*64,object_key=str(uuid4()))
            data={"revision":self.row["revision"],"working_data":{"title":"Image","cover_media_id":str(row.pk)}}
            self.assertEqual(self.send(self.detail,data,"patch").status_code,400)
            self.assertEqual(self.send("/api/media-service/assets/"+str(row.pk)+"/promote-public",{"revision":1}).status_code,expected)
            if expected==201:
                self.row=self.send(self.detail,data,"patch").json()
                url=f"/api/public/v1/media/{row.pk}/download"
                tag='"'+row.sha256+'-2"'
                self.assertEqual(self.client.get(url,HTTP_IF_NONE_MATCH=tag).status_code,304)
                MediaAsset.objects.filter(pk=row.pk).update(visibility="private")
                self.assertEqual(self.client.get(url,HTTP_IF_NONE_MATCH=tag).status_code,404)

    def test_outbox_retry_and_snapshot_integrity(self):
        from django.db import DatabaseError,transaction
        from kombu.exceptions import OperationalError
        from .tasks import dispatch,warm_sitemap
        self.transition("publish")
        job=SitemapRefresh.objects.get(space=self.space)
        with patch("backbone.celeryapp.app.send_task",side_effect=OperationalError("offline")):dispatch()
        self.assertTrue(SitemapRefresh.objects.filter(pk=job.pk).exists())
        self.transition("unpublish")
        with patch("publication.delivery.cache") as cache:
            warm_sitemap(job.pk,job.epoch)
            self.assertFalse(cache.return_value.setex.called)
            warm_sitemap(job.pk,SitemapRefresh.objects.get(pk=job.pk).epoch)
            self.assertTrue(cache.return_value.setex.called)
            self.assertFalse(SitemapRefresh.objects.exists())
        with self.assertRaises(DatabaseError),transaction.atomic():ContentRevision.objects.all().update(slug="invalid")

    def test_public_home_pages_posts_and_switcher(self):
        self.transition("publish")
        page=self.entry(entry_type="page",working_data={"title":"About the store"})
        self.send(self.root+"/entries/"+page["id"]+"/publish",{"revision":1})
        ar=self.entry(locale="ar",translation_of=self.row["id"],working_data={"title":"مقال منشور"})
        self.send(self.root+"/entries/"+ar["id"]+"/publish",{"revision":1})
        self.entry(working_slug="secret",working_data={"title":"Unpublished title"})
        for path in ("/p/press","/p/press/","/public/site/press"):
            r=self.client.get(path)
            for label in ("<h2>Pages</h2>","<h2>Posts</h2>","About the store","First edition","Arabic content (1)"):self.assertContains(r,label)
            self.assertNotContains(r,"Unpublished title")
        self.assertContains(self.client.get("/p/press/?lang=ar"),"مقال منشور")
        self.assertContains(self.client.get("/p/press/posts/first"),'hreflang="ar"')
