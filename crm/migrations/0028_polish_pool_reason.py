from django.db import migrations


def fix_reason(apps, schema_editor):
    """Stare wpisy historii opiekunów miały techniczny dopisek „(leads/next)”."""
    LeadAssignment = apps.get_model("crm", "LeadAssignment")
    LeadAssignment.objects.filter(reason="Losowanie z puli (leads/next)").update(
        reason="Losowanie z puli"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0027_sms_reminders"),
    ]

    operations = [
        migrations.RunPython(fix_reason, migrations.RunPython.noop),
    ]
