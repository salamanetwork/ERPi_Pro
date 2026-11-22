from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from backbone.mfa import cipher,session_allowed,stamp
from backbone.models import Authenticator
from backbone.services.connector_client import log_path
from django.test import RequestFactory
class MenuUpgradeTests(TestCase):
    def test_personal_and_conflict(self):
        path='/api/preferences/menu'
        user=get_user_model().objects.create_user('menu-owner')
        self.client.force_login(user)
        layout={'order':['admin','general'],'children':{'general':['suppliers']},'hidden_screens':['workspace']}
        def save():return self.client.patch(path,{'revision':0,'layout':layout},content_type='application/json')
        self.assertEqual(save().json()['revision'],1)
        self.assertEqual(save().status_code,409)
        self.assertEqual(self.client.get(path).json()['layout'],layout)
        self.client.force_login(get_user_model().objects.create_user('other'))
        self.assertEqual(self.client.get(path).json()['revision'],0)
        layout['children']['admin']=['suppliers']
        self.assertEqual(save().status_code,400)
    def test_redaction(self):
        self.assertEqual(log_path('/drug/label.json'),'/drug/label.json')
        self.assertEqual(log_path('/invoices/123'),'/invoices/{invoice_id}')
        self.assertEqual(log_path('/customers/private'),'/[resource]')
        self.assertNotIn('secret',log_path('/drug/label.json?key=secret'))
    @override_settings(ERP_MFA_KEY='new-mfa-only-'+'x'*40)
    def test_upgrade_rewrap(self):
        from django.conf import settings
        import pyotp
        secret=pyotp.random_base32()
        user=get_user_model().objects.create_superuser('enrolled',password='fictional-only')
        row=Authenticator.objects.create(user=user,encrypted_key=cipher(settings.SECRET_KEY).encrypt(secret.encode()).decode())
        call_command('migrate_wsl_mfa')
        row.refresh_from_db()
        self.assertEqual(cipher().decrypt(row.encrypted_key.encode()),secret.encode())
        original=row.encrypted_key
        call_command('migrate_wsl_mfa')
        row.refresh_from_db()
        self.assertEqual(original,row.encrypted_key)

        login=self.client.post('/api/login',{'username':'enrolled','password':'fictional-only'},content_type='application/json')
        self.assertEqual(login.status_code,200)
        self.assertEqual(self.client.post('/api/login/mfa',{'code':pyotp.TOTP(secret).now()},content_type='application/json').status_code,200)
        self.assertEqual(self.client.get('/api/workspace').status_code,200)
    def test_mfa_rollback(self):
        from django.conf import settings
        first=Authenticator.objects.create(user=get_user_model().objects.create_user('first'),encrypted_key=cipher(settings.SECRET_KEY).encrypt(b'example').decode())
        original=first.encrypted_key
        Authenticator.objects.create(user=get_user_model().objects.create_user('second'),encrypted_key='damaged')
        with self.assertRaises(CommandError):call_command('migrate_wsl_mfa')
        first.refresh_from_db()
        self.assertEqual(first.encrypted_key,original)
