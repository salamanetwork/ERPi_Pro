from django.db import migrations
from django.db.models import F
"""Only attach records with a single strong match. Email never grants portal access."""
def match(row, parties):
    kind = row._meta.model_name
    if kind == 'lead' and row.email.strip():
        candidates = parties.filter(site_id=row.site_id, email__iexact=row.email.strip(), party_type__in=['contact','customer','both'])
    elif kind in ('customer','supplier') and row.tax_id.strip():
        candidates = parties.filter(identity_number=row.tax_id.strip(), party_type__in=[kind,'both'])
    else:
        return None
    ids = list(candidates.values_list('pk',flat=True)[:2])
    return ids[0] if len(ids)==1 else None

def forwards(apps,schema_editor):
    parties=apps.get_model('parties','Party').objects
    for app,model in [('sales','Customer'),('purchasing','Supplier'),('crm','Lead')]:
        table=apps.get_model(app,model).objects
        for row in table.filter(party=None).iterator():
            pk=match(row,parties)
            if pk: table.filter(pk=row.pk,party=None).update(party_id=pk,revision=F('revision')+1)
    identities=apps.get_model('public_identity','ExternalIdentity').objects
    memberships=apps.get_model('public_identity','ExternalMembership').objects
    for user in identities.filter(party=None).iterator():
        links=list(parties.filter(account_id=user.pk).values_list('pk','space_id')[:2])
        if len(links)==1 and memberships.filter(identity_id=user.pk,space_id=links[0][1],active=True).exists():
            identities.filter(pk=user.pk).update(party_id=links[0][0],revision=F('revision')+1)

class Migration(migrations.Migration):
    dependencies=[('parties','0002_alter_party_account'),('public_identity','0003_externalidentity_party'),('sales','0004_customer_party'),('purchasing','0005_supplier_party'),('crm','0004_lead_party')]
    operations=[migrations.RunPython(forwards,migrations.RunPython.noop)]
