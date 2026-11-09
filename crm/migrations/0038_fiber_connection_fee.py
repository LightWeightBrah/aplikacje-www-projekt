"""Światłowód „12 miesięcy za 10 zł”: przyłączenie 1 zł (regulamin z 14.03.2026), nie 25 zł; ściąga z rabatami.
Kontrakt Podstawowy: cena GB po wyczerpaniu limitu w UE (4,73 zł netto, jak w ofertach T-Mobile).

Zmieniamy tylko teksty takie jak we wzorniku – poprawionych ręcznie przez managera nie ruszamy."""

from django.db import migrations

OLD_FEE = "Opłata za przyłączenie do sieci: 25,00 zł netto"
NEW_FEE = "Opłata za przyłączenie do sieci: 1,00 zł netto"
OLD_INFO = "Wg regulaminu z 14.03.2026: w zestawie z Kontraktem Podstawowym, z e-fakturą i zgodami marketingowymi; przyłączenie 1 zł"
OLD_EU = "Roaming w UE: 23 GB\n"
NEW_EU = "Roaming w UE: 23 GB (po wyczerpaniu 4,73 zł netto za 1 GB)\n"


def forwards(apps, schema_editor):
    from crm.tariffs import fiber_info

    Product = apps.get_model("crm", "Product")
    for p in Product.objects.filter(name__startswith="Światłowód"):
        fields = []
        if OLD_FEE in (p.features or ""):
            p.features = p.features.replace(OLD_FEE, NEW_FEE)
            fields.append("features")
        if OLD_INFO in (p.info or ""):
            p.info = fiber_info(" dom" in p.name)
            fields.append("info")
        if fields:
            p.save(update_fields=fields)
    for p in Product.objects.filter(name="Kontrakt Podstawowy"):
        text = (p.features or "").replace("\r\n", "\n")
        if OLD_EU in text:
            p.features = text.replace(OLD_EU, NEW_EU, 1)
            p.save(update_fields=["features"])


class Migration(migrations.Migration):
    dependencies = [("crm", "0037_phone_calls_sms_files")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
