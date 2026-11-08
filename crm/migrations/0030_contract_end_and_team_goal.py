import django.core.validators
from django.db import migrations, models


def old_default_to_team(apps, schema_editor):
    """Profile z dawną wartością domyślną 15 przechodzą na cel zespołu (puste pole)."""
    AgentProfile = apps.get_model("crm", "AgentProfile")
    AgentProfile.objects.filter(goal_minutes_per_hour=15).update(goal_minutes_per_hour=None)


def team_to_old_default(apps, schema_editor):
    AgentProfile = apps.get_model("crm", "AgentProfile")
    AgentProfile.objects.filter(goal_minutes_per_hour__isnull=True).update(goal_minutes_per_hour=15)


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0029_call_recordings"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="contract_end",
            field=models.DateField(blank=True, null=True, verbose_name="umowa u obecnego operatora do"),
        ),
        migrations.AddField(
            model_name="lead",
            name="contract_open_ended",
            field=models.BooleanField(default=False, verbose_name="umowa bez terminu"),
        ),
        migrations.AddField(
            model_name="teamrules",
            name="goal_minutes_per_hour",
            field=models.PositiveSmallIntegerField(default=15, verbose_name="cel: minut rozmów na godzinę pracy"),
        ),
        migrations.AlterField(
            model_name="agentprofile",
            name="goal_minutes_per_hour",
            field=models.PositiveSmallIntegerField(
                blank=True,
                default=None,
                help_text="Własny cel tej osoby; puste = cel zespołu ustawiony przez managera (domyślnie 15).",
                null=True,
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(60)],
                verbose_name="minut rozmów na godzinę pracy (cel)",
            ),
        ),
        migrations.RunPython(old_default_to_team, team_to_old_default),
    ]
