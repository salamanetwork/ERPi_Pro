from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from PIL import Image

from .mfa import VERIFIED, stamp
from .models import Authenticator, CompanyProfile

PATH = "/api/company-profile"


class BrandingTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.admin = users.create_superuser("branding_admin")
        self.user = users.create_user("branding_reader")
        device = Authenticator.objects.create(user=self.admin, encrypted_key="test-session-only")
        self.client.force_login(self.admin)
        session = self.client.session
        session[VERIFIED] = stamp(device)
        session.save()
        self.reader = Client()
        self.reader.force_login(self.user)

    def save(self, revision=0, **fields):
        return self.client.post(PATH, {"revision": revision, "name": "شركة اختبار", **fields})

    def image(self, format, filename, content_type):
        buffer = BytesIO()
        Image.new("RGB", (600, 300), "teal").save(buffer, format)
        return SimpleUploadedFile(filename, buffer.getvalue(), content_type=content_type)

    def test_branding_is_shared_and_only_admin_can_change_it(self):
        self.assertEqual(self.reader.get(PATH).status_code, 403)
        self.assertEqual(self.reader.post(PATH, {"name": "No access", "revision": 0}).status_code, 403)
        saved = self.save(logo=self.image("PNG", "logo.png", "image/png"))
        self.assertEqual(saved.status_code, 200, saved.content)
        record = saved.json()
        self.assertEqual(self.reader.get("/api/workspace").json()["company"], record)
        self.assertEqual(record["name"], "شركة اختبار")
        image = self.reader.get(record["logo_url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image["Content-Type"], "image/png")
        self.assertEqual(Image.open(BytesIO(image.content)).size, (512, 256))
        self.assertEqual(Client().get(record["logo_url"]).status_code, 401)
        self.assertEqual(self.save(0, name="Stale").status_code, 409)
        replaced = self.save(1, logo=self.image("JPEG", "logo.jpg", "image/jpeg")).json()
        self.assertEqual(self.reader.get(replaced["logo_url"])["Content-Type"], "image/jpeg")
        self.assertEqual(self.reader.get(record["logo_url"]).status_code, 404)
        cleared = self.save(2, name="", remove_logo="1").json()
        self.assertEqual(cleared, {"revision": 3, "name": "", "logo_url": None})

    def test_svg_logo_is_parsed_and_untrusted_content_is_rejected(self):
        good = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 16">
          <defs><linearGradient id="a"><stop stop-color="#006e68"/></linearGradient></defs>
          <path d="M0 0h32v16H0z" style="fill:url(#a);stroke:none"/></svg>'''
        saved = self.save(logo=SimpleUploadedFile("logo.svg", good, content_type="image/svg+xml"))
        self.assertEqual(saved.status_code, 200, saved.content)
        response = self.reader.get(saved.json()["logo_url"])
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertIn(b'fill="url(#a)"', response.content)
        for body in (
            b'<svg onload="alert(1)"/>',
            b'<svg><script>alert(1)</script></svg>',
            b'<svg><foreignObject/></svg>',
            b'<svg><image href="https://example.invalid/logo"/></svg>',
            b'<svg><path fill="url(https://example.invalid/fill)"/></svg>',
            b'<!DOCTYPE svg [<!ENTITY injected SYSTEM "file:///etc/passwd">]><svg>&injected;</svg>',
            b'not image content',
            b'\x89PNG\r\n\x1a\ntruncated',
        ):
            with self.subTest(body=body):
                invalid = self.save(1, name="Must not change",
                                    logo=SimpleUploadedFile("tries.png", body, content_type="image/png"))
                self.assertEqual(invalid.status_code, 400, invalid.content)
                self.assertEqual(invalid.json()["error"], "invalid_company_logo")
        too_big = SimpleUploadedFile("big.png", b"x" * (2 * 1024 * 1024 + 1), content_type="image/png")
        self.assertEqual(self.save(1, logo=too_big).status_code, 413)
        self.assertEqual(CompanyProfile.objects.get(pk=1).name, "شركة اختبار")
        self.assertEqual(CompanyProfile.objects.get(pk=1).revision, 1)
