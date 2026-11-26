"""Only attach records with a single strong match. Email never grants portal access."""
def match(row, parties):
    kind = row._meta.model_name
    if kind == 'lead' and row.email.strip():
        candidates = parties.filter(active=True, site_id=row.site_id, email__iexact=row.email.strip(), party_type__in=['contact','customer','both'])
    elif kind in ('customer','supplier') and row.tax_id.strip():
        candidates = parties.filter(active=True, identity_number=row.tax_id.strip(), party_type__in=[kind,'both'])
    else:
        return None
    ids = list(candidates.values_list('pk',flat=True)[:2])
    return ids[0] if len(ids)==1 else None
