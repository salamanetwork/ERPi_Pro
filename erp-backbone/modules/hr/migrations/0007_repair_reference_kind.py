from django.db import migrations


def restore_reference_kinds(apps, schema_editor):
    references = apps.get_model("hr", "HRReference").objects
    employees = apps.get_model("hr", "Employee").objects
    events = apps.get_model("backbone", "AuditEvent").objects
    untyped = references.filter(kind="")
    if not untyped.exists():
        return

    kinds = {"departments": "department_ref_id", "job-titles": "title_ref_id", "grades": "grade_ref_id"}
    original_kind = {}
    for target in events.filter(action="hr.reference.create").order_by("pk").values_list("target", flat=True).iterator():
        kind, separator, code = target.partition("/")
        if separator and kind in kinds:
            original_kind.setdefault(code, kind)

    for row in untyped.order_by("pk"):
        linked_kinds = {kind for kind, field in kinds.items() if employees.filter(**{field: row.pk}).exists()}
        if len(linked_kinds) > 1:
            continue
        kind = next(iter(linked_kinds), original_kind.get(row.code))
        if not kind:
            continue
        code = row.code
        if references.filter(kind=kind, code=code).exists():
            attempt = 0
            while True:
                suffix = f"-LEGACY-{row.pk}" + (f"-{attempt}" if attempt else "")
                code = row.code[:40 - len(suffix)] + suffix
                if not references.filter(kind=kind, code=code).exists():
                    break
                attempt += 1
        references.filter(pk=row.pk).update(kind=kind, code=code)


class Migration(migrations.Migration):
    dependencies = [
        ("hr", "0006_employee_directory"),
        ("backbone", "0011_procurement_sales_modules"),
    ]
    operations = [migrations.RunPython(restore_reference_kinds, migrations.RunPython.noop)]
