import hmac,time
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.core.management import call_command
from .models import ApiConnector,Authenticator,OutboxEvent,OutboundApiLog,WebhookEndpoint
from . import mfa
from .services.connector_client import resolve,cipher
from .validation import Problem

@override_settings(ERP_CONNECTOR_KEY="unique-integration-test-key-value-111222333",DEBUG=True)
class GatewayTests(TestCase):
    root="/api/platform/v1/integrations/"
    def setUp(self):
        user=get_user_model().objects.create_superuser("gateway-admin")
        self.client.force_login(user)
        dev=Authenticator.objects.create(user=user,encrypted_key="fixture")
        sess=self.client.session;sess[mfa.VERIFIED]=mfa.stamp(dev);sess.save()
    def post(self,url,data):return self.client.post(url,data,content_type="application/json")
    def test_credentials_probe_retries(self):
        password="synthetic-example"
        r=self.post(self.root+"connectors",dict(slug="fixture",name="Fixture",base_url="http://localhost:8000",auth_type="bearer",credentials=password))
        self.assertEqual(r.status_code,201,r.content)
        self.assertNotIn(password,ApiConnector.objects.get().encrypted_credentials)
        self.assertNotContains(self.client.get(self.root+"connectors"),password)
        with patch("backbone.services.connector_client.send",side_effect=[(429,b""),(200,b"ok")]) as send,patch("backbone.services.connector_client.time.sleep"):
            self.assertEqual(self.post(self.root+"connectors/fixture/test",{}).json()["status"],200)
            self.assertEqual(send.call_count,2)
        self.assertEqual(OutboundApiLog.objects.count(),1)
        self.assertNotContains(self.client.get(self.root+"logs"),password)
    @override_settings(DEBUG=False)
    def test_local_destination(self):
        with self.assertRaises(Problem):resolve("https://127.0.0.1")
    def test_webhook(self):
        r=self.post(self.root+"webhooks",{"slug":"updates","name":"Updates"})
        self.assertEqual(r.status_code,201,r.content)
        secret=r.json()["signing_secret"].encode()
        raw=b'{"value":1}'
        def deliver(ts,key):
            sig=hmac.digest(key,ts.encode()+b"."+raw,"sha256").hex()
            return self.client.post("/api/webhooks/v1/updates/",raw,content_type="application/json",HTTP_X_WEBHOOK_TIMESTAMP=ts,HTTP_X_WEBHOOK_SIGNATURE=sig,HTTP_X_WEBHOOK_ID="example")
        ts=str(int(time.time()))
        self.assertEqual(deliver(ts,b"wrong").status_code,401)
        self.assertEqual(deliver("1",secret).status_code,401)
        self.assertEqual(deliver(ts,secret).json()["status"],"accepted")
        self.assertEqual(deliver(ts,secret).json()["status"],"duplicate")
        self.assertEqual(OutboxEvent.objects.filter(module="webhook",event="updates.received").count(),1)
    def test_rotate(self):
        from django.conf import settings
        r=self.post(self.root+"webhooks",{"slug":"changes","name":"Changes"})
        old=settings.ERP_CONNECTOR_KEY
        with override_settings(ERP_CONNECTOR_KEY="replacement-test-integration-key-444555"):
            call_command("rotate_security_keys",old_connector_key=old)
            self.assertEqual(cipher().decrypt(WebhookEndpoint.objects.get().encrypted_secret.encode()).decode(),r.json()["signing_secret"])
    def test_privacy(self):
        from .models import ModuleState
        for key in ("publication","public_identity"):
            ModuleState.objects.update_or_create(module_id=key,defaults={"enabled":True})
        from public_identity.models import ExternalIdentity
        r=ExternalIdentity.objects.create(normalized_email="demo@example.test",display_name="Demo",password_hash="x")
        base=f"/api/platform/v1/identities/{r.pk}/"
        self.assertEqual(self.client.get(base+"export").json()["profile"]["email"],r.normalized_email)
        self.assertEqual(self.post(base+"anonymize",{"revision":1}).status_code,200)
        r.refresh_from_db()
        self.assertEqual(r.status,"disabled")
        self.assertEqual(self.post(base+"anonymize",{"revision":1}).status_code,409)
