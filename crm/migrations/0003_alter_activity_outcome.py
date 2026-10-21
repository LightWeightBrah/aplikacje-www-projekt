from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0002_company_krs_company_regon_alter_company_nip'),
    ]

    operations = [
        migrations.AlterField(
            model_name='activity',
            name='outcome',
            field=models.CharField(choices=[('no_answer', 'Nie odebrał'), ('callback', 'Prosił o oddzwonienie'), ('interested', 'Zainteresowany'), ('sold', 'Sprzedane'), ('refused', 'Odmowa'), ('wrong_number', 'Zły numer'), ('note', 'Notatka')], max_length=20),
        ),
    ]
