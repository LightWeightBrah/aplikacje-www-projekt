"""Światłowód: jedyna promocja to „12 miesięcy za 10 zł” (potwierdzone 09.11.2026).

„6 miesięcy za 0 zł” jest tylko na numery, „12 miesięcy za 50 zł” na światłowód już nie ma.
Zmieniamy tylko taryfy światłowodu z wzornika (główna promocja dalej „12 miesięcy za 50 zł”) –
taryf poprawionych ręcznie przez managera nie ruszamy. Ściąga Kontraktu Podstawowego też tylko,
gdy jest taka jak w starym wzorniku."""

from django.db import migrations

OLD_PROMO = "12 miesięcy za 50 zł"
OLD_KP = "(promocje „12 miesięcy za 50 zł” i „12 miesięcy za 10 zł” wymagają Kontraktu Podstawowego)"
NEW_KP = "(promocja światłowodu „12 miesięcy za 10 zł” wymaga Kontraktu Podstawowego)"


def forwards(apps, schema_editor):
    from decimal import Decimal

    from crm.tariffs import FIBER_PROMO, fiber_info

    Product = apps.get_model("crm", "Product")
    for p in Product.objects.filter(name__startswith="Światłowód", promo_label=OLD_PROMO):
        p.promo_label = FIBER_PROMO
        p.promo_months = 12
        p.promo_month_price = Decimal("10")
        p.promo_price = p.price          # od 13. miesiąca cena standardowa
        p.promos = []
        if "12 miesięcy za 50 zł" in (p.info or ""):
            p.info = fiber_info(" dom" in p.name)
        p.save(update_fields=["promo_label", "promo_months", "promo_month_price", "promo_price", "promos", "info"])
    for p in Product.objects.filter(name="Kontrakt Podstawowy", info__contains=OLD_KP):
        p.info = p.info.replace(OLD_KP, NEW_KP)
        p.save(update_fields=["info"])


class Migration(migrations.Migration):
    dependencies = [("crm", "0035_forward_name_no_default")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
