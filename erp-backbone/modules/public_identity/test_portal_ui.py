"""Presentation regression tests: no database or live broker required."""
import re
from unittest.mock import patch
from django.test import SimpleTestCase,Client
from rest_framework.exceptions import ValidationError

@patch('public_identity.views.enabled_modules',return_value={'publication','public_identity','media_service'})
class PortalPresentationTests(SimpleTestCase):
    def test_account_has_isolated_views_and_nonce_policy(self,_):
        response=self.client.get('/public/account?lang=ar')
        self.assertEqual(response.status_code,200)
        html=response.content.decode()
        self.assertIn('id="auth-view"',html)
        self.assertIn('id="member" hidden',html)
        self.assertIn('id="tab-register"',html)
        self.assertIn('id="name-field" hidden',html)
        self.assertNotIn('PORTAL_THEME',html)
        self.assertNotIn('PORTAL_SCRIPT',html)
        self.assertEqual(response['Cache-Control'],'no-store')
        nonces=re.findall(r'nonce="([^"]+)"',html)
        self.assertEqual(len(nonces),2)
        self.assertEqual(nonces[0],nonces[1])
        self.assertIn("script-src 'nonce-"+nonces[0]+"'",response['Content-Security-Policy'])
        self.assertNotIn('unsafe-inline',response['Content-Security-Policy'])
        self.assertNotIn('localStorage.setItem',html)
    def test_confirm_is_styled_and_does_not_consume_on_get(self,_):
        with patch('public_identity.views.s.consume') as consume:
            response=self.client.get('/public/auth/confirm?lang=ar')
        consume.assert_not_called()
        self.assertContains(response,'confirm-card')
        self.assertContains(response,'name="csrfmiddlewaretoken"')
        self.assertNotContains(response,'PORTAL_CSRF')
        self.assertContains(response,'location.pathname+location.search')
        self.assertContains(response,'location.hash.slice(1)')
    def test_confirm_styled_success_same_service_and_csrf(self,_):
        client=Client(enforce_csrf_checks=True)
        client.get('/public/auth/confirm')
        data={'challenge_id':'abc','token':'one-time-secret','purpose':'verify'}
        self.assertEqual(client.post('/public/auth/confirm',data).status_code,403)
        with patch('public_identity.views.s.consume') as consume:
            response=client.post('/public/auth/confirm?lang=ar',data,HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code,200)
        consume.assert_called_once()
        self.assertEqual(consume.call_args.args[1],'verify')
        self.assertContains(response,'data-result="completed"')
        self.assertNotContains(response,'one-time-secret')
        self.assertEqual(response['Cache-Control'],'no-store')
    def test_confirm_styled_invalid_keeps_400(self,_):
        with patch('public_identity.views.s.consume',side_effect=ValidationError({'token':['Invalid']})):
            response=self.client.post('/public/auth/confirm',{'purpose':'reset','token':'not-returned'})
        self.assertEqual(response.status_code,400)
        self.assertIn('data-result="invalid"',response.content.decode())
        self.assertNotIn('not-returned',response.content.decode())
    def test_disabled_module_and_invalid_purpose(self,enabled):
        self.assertEqual(self.client.post('/public/auth/confirm',{'purpose':'other'}).status_code,400)
        self.assertEqual(self.client.put('/public/auth/confirm').status_code,405)
        enabled.return_value=set()
        self.assertEqual(self.client.get('/public/account').status_code,404)
        self.assertEqual(self.client.get('/public/auth/confirm').status_code,404)
