from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0003_alter_activity_outcome"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lead",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "Nowy"),
                    ("callback", "Do oddzwonienia"),
                    ("interested", "Zainteresowany"),
                    ("offer", "Oferta wysłana"),
                    ("won", "Sprzedany"),
                    ("lost", "Bez sprzedaży"),
                    ("do_not_call", "Nie dzwonić"),
                    ("unreachable", "Nieosiągalny"),
                ],
                db_index=True,
                default="new",
                max_length=20,
            ),
        ),
    ]
