from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase,Client,override_settings
from .models import Authenticator,ModuleState
from . import mfa

class SecurityAudit(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('ops-health')
        self.client=Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        dev=Authenticator.objects.create(user=self.user,encrypted_key='fixture')
        session=self.client.session;session[mfa.VERIFIED]=mfa.stamp(dev);session.save()
        self.client.get('/admin/users')
    def test_admin_health_and_csrf(self):
        url='/api/platform/v1/security/health'
        self.assertEqual(Client().get(url).status_code,401)
        self.assertTrue(self.client.get(url).json()['keys']['separate'])
        self.assertEqual(self.client.post('/api/platform/v1/security/revoke-all',{'kind':'staff'},content_type='application/json').status_code,403)
        user=get_user_model().objects.create_user('regular-health')
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code,403)
    def test_sop_enabled_and_scoped(self):
        key='parties'
        ModuleState.objects.update_or_create(module_id=key,defaults={'enabled':True})
        with patch('backbone.training.enabled_modules',return_value={key}):
            d=self.client.get('/api/platform/v1/sop-guides').json()
            self.assertTrue(any(g['module']==key and g['steps'][0]['text_ar'] for g in d['results']))
            self.client.force_login(get_user_model().objects.create_user('no-guides'))
            self.assertFalse(self.client.get('/api/platform/v1/sop-guides').json()['results'])
