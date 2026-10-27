from django.db import migrations


def create_policy(apps, schema_editor):
    StatusPolicy = apps.get_model("students", "StatusPolicy")
    StatusPolicy.objects.get_or_create(status="DISMISSED", defaults={"can_register": False, "can_view_results": True})


class Migration(migrations.Migration):
    dependencies = [("students", "0003_student_academic_standing_alter_statuspolicy_status_and_more")]
    operations = [migrations.RunPython(create_policy, migrations.RunPython.noop)]
