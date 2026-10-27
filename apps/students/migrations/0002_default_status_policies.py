from django.db import migrations

DEFAULTS = {
    "APPLICANT": (False, False),
    "ACTIVE": (True, True),
    "DEFERRED": (False, True),
    "SUSPENDED": (False, False),
    "WITHDRAWN": (False, True),
    "COMPLETED": (False, True),
    "GRADUATED": (False, True),
}


def create_policies(apps, schema_editor):
    StatusPolicy = apps.get_model("students", "StatusPolicy")
    for status, (can_register, can_view_results) in DEFAULTS.items():
        StatusPolicy.objects.get_or_create(
            status=status, defaults={"can_register": can_register, "can_view_results": can_view_results}
        )


class Migration(migrations.Migration):
    dependencies = [("students", "0001_initial")]
    operations = [migrations.RunPython(create_policies, migrations.RunPython.noop)]
