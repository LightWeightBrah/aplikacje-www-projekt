"""Ściąga Magenta TV M i L: pokazujemy WSZYSTKIE kanały pakietu (też te z S i M), a nie tylko dochodzące.

Zmieniamy tylko ściągi wygenerowane z wzornika (tekst taki sam jak stary wzornik) –
ściąg poprawionych ręcznie przez managera nie ruszamy."""

from django.db import migrations

from crm.tv_channels import CANAL_PLUS_SUPER_SPORT, CHANNEL_COUNTS, CHANNELS, CHANNELS_AS_OF

NL = chr(10)


def old_info(plan):
    head = {"S": [], "M": ["Wszystkie kanały z pakietu S, a do tego:"],
            "L": ["Wszystkie kanały z pakietów S i M, a do tego:"]}[plan]
    return NL.join([
        f"Liczba kanałów: {CHANNEL_COUNTS[plan]} (lista T-Mobile, stan na {CHANNELS_AS_OF})",
        "Oglądanie na dekoderze i na 3 urządzeniach naraz (telefon, tablet, komputer), wideo na żądanie (VOD)",
        *head, *CHANNELS[plan],
        "Za dopłatą: pakiet CANAL+ SUPER SPORT (" + ", ".join(CANAL_PLUS_SUPER_SPORT[:6]) + " i inne)",
    ])


def forwards(apps, schema_editor):
    from crm.tariffs import tv_info
    Product = apps.get_model("crm", "Product")
    for plan in ("M", "L"):
        old = old_info(plan)
        for p in Product.objects.filter(name=f"Magenta TV {plan}"):
            if (p.info or "").replace("\r\n", NL).strip() == old:
                p.info = tv_info(plan)
                p.save(update_fields=["info"])


class Migration(migrations.Migration):
    dependencies = [("crm", "0032_skip_reasons_and_real_work_time")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
