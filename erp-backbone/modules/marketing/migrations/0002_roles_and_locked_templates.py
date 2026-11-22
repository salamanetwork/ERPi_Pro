from django.conf import settings
from django.db import migrations

FORWARD = """
CREATE FUNCTION marketing_protect_locked_template() RETURNS trigger AS $$
BEGIN
    IF OLD.locked_at IS NOT NULL THEN
        RAISE EXCEPTION 'An approved template version is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;
CREATE TRIGGER marketing_template_lock
BEFORE UPDATE OR DELETE ON marketing_templateversion
FOR EACH ROW EXECUTE FUNCTION marketing_protect_locked_template();
"""
REVERSE = """
DROP TRIGGER marketing_template_lock ON marketing_templateversion;
DROP FUNCTION marketing_protect_locked_template();
"""


def install_roles(apps, editor):
    Role = apps.get_model("backbone", "Role")
    Assignment = apps.get_model("backbone", "Assignment")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    role, _ = Role.objects.get_or_create(name="marketing.admin", defaults={
        "permissions": ["marketing.view", "marketing.edit", "marketing.admin", "marketing.suppressions"]
    })
    Role.objects.get_or_create(name="marketing.editor", defaults={
        "permissions": ["marketing.view", "marketing.edit"]
    })
    for pk in User.objects.filter(is_superuser=True).values_list("pk", flat=True):
        Assignment.objects.get_or_create(user_id=pk, role_id=role.pk, site=None)


class Migration(migrations.Migration):
    dependencies = [("marketing", "0001_initial")]
    operations = [
        migrations.RunPython(install_roles, migrations.RunPython.noop),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
