"""Solid info: poprawiony opis promocji „6 miesięcy gratis” i otwarcie rozmowy ze screenplaya.

Zmieniamy tylko teksty, których manager nie poprawiał (treść taka sama jak stary domyślny)."""

from django.db import migrations

CHANGES = (
    ("Promocja: 6 miesięcy gratis",
     "Przy umowie na 36 miesięcy klient może dostać 6 miesięcy gratis (cena pierwszego numeru wyższa – patrz taryfy). Zawsze proponuj najpierw 36 miesięcy.",
     "6 miesięcy gratis możesz dać klientowi zawsze. Wtedy cena miesięczna jest wyższa (patrz taryfy), więc w całej umowie klient płaci więcej, a Twoja prowizja jest wyższa."),
    ("Jak zacząć rozmowę",
     "Dzień dobry, dzwonię w sprawie usług komórkowych dla firmy – czy rozmawiam z osobą, która decyduje o abonamentach?",
     "Dzień dobry… z tej strony [Twoje imię], dzwonię w sprawie telefonów firmowych. Wiem, że dzwonię bez zapowiedzi, więc krótko: w pół minuty powiem, o co chodzi, a pan oceni, czy to w ogóle ma sens. Może tak być?"),
)


def forwards(apps, schema_editor):
    Announcement = apps.get_model("crm", "Announcement")
    for title, old, new in CHANGES:
        Announcement.objects.filter(title=title, body=old).update(body=new)


class Migration(migrations.Migration):
    dependencies = [("crm", "0033_tv_info_all_channels")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
