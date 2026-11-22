import getpass
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from backbone.models import Assignment, ModuleState, Role
from backbone.registry import MANIFESTS


class Command(BaseCommand):
    help = "Initialize a new installation with all business modules disabled."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")

    def handle(self, *args, **options):
        username = options["username"]
        user_model = get_user_model()
        if user_model.objects.filter(is_superuser=True).exists():
            raise CommandError("An administrator already exists.")
        password = getpass.getpass("Administrator password: ")
        if password != getpass.getpass("Confirm password: "):
            raise CommandError("The passwords do not match.")
        user = user_model(username=username)
        try:
            user.full_clean(exclude=["password"])
            validate_password(password, user)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        with transaction.atomic():
            admin = user_model.objects.create_superuser(username=username, password=password)
            if "marketing" in MANIFESTS and (role := Role.objects.filter(name="marketing.admin").first()):
                Assignment.objects.get_or_create(user=admin,role=role,site=None)
            for key, module in MANIFESTS.items():
                if module.get("category") != "platform_core":
                    ModuleState.objects.get_or_create(module_id=key)
            for name, permissions in (
                ("Reader", ["assets.view", "workorders.view"]),
                ("Asset operator", ["assets.view", "assets.create"]),
                ("Work order operator", ["workorders.view", "workorders.create", "workorders.edit", "workorders.submit"]),
            ):
                Role.objects.get_or_create(name=name, defaults={"permissions": permissions})
        self.stdout.write(self.style.SUCCESS("Administrator created. Business modules are disabled."))
