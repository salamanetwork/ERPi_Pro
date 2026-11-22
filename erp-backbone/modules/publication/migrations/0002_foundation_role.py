from django.db import migrations

def roles(apps,editor):
    Role=apps.get_model("backbone","Role")
    Role.objects.get_or_create(name="Platform_Services_Admin",defaults={"permissions":["publication.view","publication.manage","public_identity.view","public_identity.manage","media_service.view","media_service.manage"]})

class Migration(migrations.Migration):
    dependencies=[("publication","0001_initial")]
    operations=[migrations.RunPython(roles,migrations.RunPython.noop)]
