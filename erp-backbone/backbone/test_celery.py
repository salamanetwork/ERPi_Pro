from celery.app.utils import find_app
from django.test import SimpleTestCase

from .celeryapp import app


class CeleryEntryPointTests(SimpleTestCase):
    def test_package_alias_and_explicit_target_use_the_same_notification_queue(self):
        self.assertIs(find_app("backbone"), app)
        self.assertIs(find_app("backbone.celeryapp:app"), app)
        self.assertEqual(app.amqp.default_queue.name, "notifications")
        self.assertEqual(app.amqp.router.route({}, "backbone.tasks.deliver_notification")["queue"].name, "notifications")
