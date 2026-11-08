"""Taryfy firmowe T-Mobile z wzornika ofert zespołu (wersja 11.0): komórkowe S / M100 / M / L / VIP,
światłowód M / L / VIP (blok i dom) i Magenta TV S / M / L.

Używa ich `seed_demo` (lokalnie), przycisk „Wgraj taryfy z wzornika” na stronie „Taryfy i produkty”
i komenda `manage.py load_tariffs` (także na serwerze). Ceny potem zmienia manager na stronie.
"""

from decimal import Decimal

from .models import Product
from .tv_channels import CANAL_PLUS_SUPER_SPORT, CHANNEL_COUNTS, CHANNELS, CHANNELS_AS_OF

def features(*points):
    return chr(10).join(points)   # chr(10) = nowa linia: jeden punkt oferty w linii


def mobile(internet, card):
    """Punkty oferty komórkowej – kolejność i słowa jak we wzorniku ofert (wersja 11.0)."""
    return features(
        "Nielimitowane rozmowy do wszystkich sieci komórkowych i stacjonarnych w Polsce",
        "Roaming w UE: 23 GB",
        "Nielimitowane SMS-y i MMS-y w kraju i w roamingu UE",
        f"Internet na telefonach komórkowych: {internet}",
        "Prędkość internetu: 150 Mb/s",
        f"Internet na karcie internetowej: {card}",
    )


def fiber(speed):
    return features(
        f"Prędkość światłowodu: {speed}",
        "Internet światłowodowy: bez limitu danych",
        "Opłata za przyłączenie do sieci: 25,00 zł netto",
    )


def mobile_info(speed, eu_minutes):
    """Ściąga dla agenta (nie trafia do oferty) – z materiałów szkoleniowych Magenta Biznes."""
    return features(
        f"Prędkość internetu na karcie internetowej (KI): {speed}",
        "Pierwszy numer (Kontrakt Podstawowy): internet 150 Mb/s",
        "Kolejny numer (Kontrakt Dodatkowy) za 25 zł: internet 30 Mb/s; za 35 zł: 150 Mb/s; KD Plus za 50 zł: 300 Mb/s i 27 GB w UE",
        f"Minuty do UE (połączenia z Polski na numery w innych krajach UE): {eu_minutes} na karcie, "
        "na numerach 100 (bez telefonu) albo 200 (z telefonem); kraje spoza UE nie wchodzą w pakiet",
        "Promocja „6 miesięcy gratis”: przez 6 miesięcy 0 zł, potem cena standardowa",
        "Nie łączyć negocjowanych cen KP ze zniżką 6×0 na karcie internetowej",
    )


def fiber_info(home):
    where = "dom (SFH) – zabudowa jednorodzinna, +10 zł" if home else "blok (MFH) – zabudowa wielorodzinna"
    return features(
        f"Cena dla: {where}",
        "Prędkość zależy tylko od taryfy (M 300 / L 600 / VIP 900 Mb/s) – blok i dom różnią się tylko ceną",
        "Umowa na 24 miesiące, router HOME BOX w cenie",
        "Promocja „6 miesięcy za 0 zł”: przez 6 miesięcy 0 zł, potem cena standardowa",
        "Promocja „12 miesięcy za 50 zł” (dom 60 zł) – w zestawie z Kontraktem Podstawowym; od 13. miesiąca 85 / 105 / 140 zł (dom +10 zł)",
        "Promocja „12 miesięcy za 10 zł” (regulamin od 14.03.2026) – tylko w zestawie z Kontraktem Podstawowym, z e-fakturą "
        "i zgodami marketingowymi (bez tych rabatów 35 zł); od 13. miesiąca cena standardowa; przyłączenie 1 zł",
        "Przed sprzedażą sprawdź dostępność światłowodu pod adresem klienta",
    )


def kp_features():
    return features(
        "Nielimitowane rozmowy, SMS-y i MMS-y w kraju i w roamingu UE",
        "Internet w kraju bez limitu danych, prędkość do 150 Mb/s",
        "100 minut na połączenia z Polski do innych krajów UE",
        "Roaming w UE: 23 GB",
        "Każdy kolejny numer (Kontrakt Dodatkowy): 25,00 zł netto, internet do 30 Mb/s",
    )


def kp_info():
    return features(
        "Sam numer głosowy, bez karty internetowej – np. do zestawu ze światłowodem "
        "(promocje „12 miesięcy za 50 zł” i „12 miesięcy za 10 zł” wymagają Kontraktu Podstawowego)",
        "W taryfach S / M100 / M / L / VIP Kontrakt Podstawowy jest już w środku (pierwszy numer) – nie dodawaj go osobno",
        "Promocja: 6 miesięcy za 0 zł, potem 40 zł netto",
        "Minuty do UE: 100 bez telefonu, 200 z telefonem; kraje spoza UE nie wchodzą w pakiet",
        "Kontrakt Dodatkowy za 35 zł ma 150 Mb/s, KD Plus za 50 zł – 300 Mb/s",
    )


def jump_features():
    return features(
        "Nielimitowane rozmowy, SMS-y i MMS-y w Polsce i w UE",
        "10 GB internetu bez limitu prędkości",
        "Internet w roamingu UE: 8,95 GB",
        "Umowa na 24 miesiące",
    )


def tv_info(plan):
    """Ściąga z kanałami pakietu (lista T-Mobile) – agent ma ją pod ręką, gdy klient pyta o kanały."""
    head = {"S": [], "M": ["Wszystkie kanały z pakietu S, a do tego:"],
            "L": ["Wszystkie kanały z pakietów S i M, a do tego:"]}[plan]
    own = list(CHANNELS[plan])   # pakiety się sumują, więc tu tylko kanały dochodzące w tym pakiecie
    return features(
        f"Liczba kanałów: {CHANNEL_COUNTS[plan]} (lista T-Mobile, stan na {CHANNELS_AS_OF})",
        "Oglądanie na dekoderze i na 3 urządzeniach naraz (telefon, tablet, komputer), wideo na żądanie (VOD)",
        *head, *own,
        "Za dopłatą: pakiet CANAL+ SUPER SPORT (" + ", ".join(CANAL_PLUS_SUPER_SPORT[:6]) + " i inne)",
    )


def tv(channels, plan_l):
    points = [f"Liczba kanałów: {channels}", "Dostępność na 3 urządzeniach"]
    if plan_l:
        points.append("Pakiet L przez 1 miesiąc")
    return features(*points, "Udostępnienie dekodera", "Wideo na żądanie")


D = Decimal
NONE = None   # brak promocji (telewizja)
# Promocja światłowodu z wzornika: 12 miesięcy po 50 zł (dom +10 zł), od 13. miesiąca 85 / 105 / 140 zł (dom +10 zł)
FIBER_PROMO = "12 miesięcy za 50 zł"


def fiber_extra(month_price, home, standard):
    """Światłowód ma trzy promocje (materiały ze szkolenia): główna 12×50 zł i do wyboru 6×0 zł albo 12×10 zł."""
    return {"promo_label": FIBER_PROMO, "promo_months": 12, "promo_month_price": month_price, "info": fiber_info(home),
            "promos": [
                {"label": "6 miesięcy za 0 zł", "months": 6, "month_price": "0.00", "price": f"{standard}.00"},
                {"label": "12 miesięcy za 10 zł", "months": 12, "month_price": "10.00", "price": f"{standard}.00"},
            ]}

# nazwa, cena netto 1. numeru / sztuki, cena po promocji, każdy kolejny numer, punkty oferty, dodatki:
# inna promocja niż „6 miesięcy gratis” (promo_label, promo_months, promo_month_price), inne promocje
# do wyboru (promos) i ściąga dla agenta (info).
# Kolejny numer = 0 → zwykły produkt: cena × ilość (np. 2 łącza, 2 pakiety TV).
STANDARD_TARIFFS = (
    # Komórkowe – przeniesienie numerów
    ("S", D("45"), D("50"), D("25"),
     mobile("50 GB przez pierwsze 3 miesiące, następnie 40 GB (internet współdzielony)", "40 GB"),
     {"info": mobile_info("150 Mb/s", 50)}),
    ("M100", D("55"), D("65"), D("25"), mobile("100 GB (internet współdzielony)", "100 GB"),
     {"info": mobile_info("150 Mb/s", 50)}),
    ("M", D("70"), D("85"), D("25"), mobile("Bez limitu", "Bez limitu"), {"info": mobile_info("150 Mb/s", 100)}),
    ("L", D("85"), D("105"), D("25"), mobile("Bez limitu", "Bez limitu"), {"info": mobile_info("300 Mb/s", 200)}),
    ("VIP", D("105"), D("140"), D("25"), mobile("Bez limitu", "Bez limitu"), {"info": mobile_info("bez limitu", 500)}),
    # Sam numer głosowy (Kontrakt Podstawowy + kolejne Kontrakty Dodatkowe) i najtańszy numer – z maila po szkoleniu
    ("Kontrakt Podstawowy", D("40"), D("40"), D("25"), kp_features(),
     {"promo_label": "6 miesięcy za 0 zł", "promo_months": 6, "promo_month_price": D("0"), "info": kp_info()}),
    ("JUMP proFirma XS", D("15"), NONE, D("15"), jump_features(),
     {"info": features("Najtańszy numer: 15 zł netto, umowa na 24 miesiące",
                       "Po wyczerpaniu limitu w UE: 7,48 zł netto za 1 GB")}),
    # Magenta Światłowód Biznes (umowa na 24 miesiące) – cena zależy od zabudowy: blok (MFH) albo dom (SFH)
    ("Światłowód M – blok", D("45"), D("85"), D("0"), fiber("do 300 Mb/s"), fiber_extra(D("50"), False, 45)),
    ("Światłowód M – dom", D("55"), D("95"), D("0"), fiber("do 300 Mb/s"), fiber_extra(D("60"), True, 55)),
    ("Światłowód L – blok", D("65"), D("105"), D("0"), fiber("do 600 Mb/s"), fiber_extra(D("50"), False, 65)),
    ("Światłowód L – dom", D("75"), D("115"), D("0"), fiber("do 600 Mb/s"), fiber_extra(D("60"), True, 75)),
    ("Światłowód VIP – blok", D("100"), D("140"), D("0"), fiber("do 900 Mb/s"), fiber_extra(D("50"), False, 100)),
    ("Światłowód VIP – dom", D("110"), D("150"), D("0"), fiber("do 900 Mb/s"), fiber_extra(D("60"), True, 110)),
    # Magenta TV – ceny netto z rabatami (brutto z VAT 8%: 20 / 50 / 80 zł)
    # liczby kanałów jak na t-mobile.pl (lista z 1.07.2026); wzornik 11.0 miał jeszcze 85 / 147 / 193
    ("Magenta TV S", D("18.52"), NONE, D("0"), tv(CHANNEL_COUNTS["S"], True), {"info": tv_info("S")}),
    ("Magenta TV M", D("46.30"), NONE, D("0"), tv(CHANNEL_COUNTS["M"], True), {"info": tv_info("M")}),
    ("Magenta TV L", D("74.07"), NONE, D("0"), tv(CHANNEL_COUNTS["L"], False), {"info": tv_info("L")}),
)



def tariff_defaults(order, price, promo, extra, points, more=None):
    more = more or {}
    return {"price": price, "promo_price": promo, "extra_price": extra, "features": points,
            "unit": "mies.", "sort_order": order, "is_active": True,
            "promo_label": more.get("promo_label", ""), "promo_months": more.get("promo_months"),
            "promo_month_price": more.get("promo_month_price", Decimal("0")), "info": more.get("info", ""),
            "promos": more.get("promos", [])}


def clean_promos(value):
    """Sprawdza listę „innych promocji” (z API albo z pliku): [{"label", "months", "month_price", "price"}]."""
    from decimal import InvalidOperation

    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 6:
        raise ValueError("Inne promocje: najwyżej 6 pozycji.")
    out = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("Inne promocje: zły format.")
        label = str(raw.get("label") or "").strip()[:80]
        if not label:
            raise ValueError("Inne promocje: każda promocja musi mieć nazwę.")
        try:
            months = int(str(raw.get("months") or 0).strip())
            month_price = Decimal(str(raw.get("month_price") or 0).replace(",", ".").replace("zł", "").strip()).quantize(Decimal("0.01"))
            price = Decimal(str(raw.get("price")).replace(",", ".").replace("zł", "").strip()).quantize(Decimal("0.01"))
        except (TypeError, ValueError, InvalidOperation):
            raise ValueError(f"Promocja „{label}”: miesiące i ceny muszą być liczbami.")
        if not 0 <= months <= 60 or month_price < 0 or price < 0:
            raise ValueError(f"Promocja „{label}”: miesiące 0–60, ceny nie mogą być ujemne.")
        out.append({"label": label, "months": months, "month_price": str(month_price), "price": str(price)})
    return out


def promos_to_text(promos):
    """Do pliku Excela: jedna promocja w linii „nazwa; miesiące; cena w miesiącach; cena potem”."""
    return chr(10).join(f"{p['label']}; {p['months']}; {p['month_price']}; {p['price']}" for p in clean_promos(promos))


def promos_from_text(text):
    rows = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split(";")]
        if len(parts) != 4:
            raise ValueError(f"Inne promocje: „{line.strip()}” – wpisz „nazwa; miesiące; cena w miesiącach; cena potem”.")
        rows.append({"label": parts[0], "months": parts[1] or 0, "month_price": parts[2] or 0, "price": parts[3]})
    return clean_promos(rows)


# ---------------------------------------------------------------- plik Excela z taryfami
# Jeden wiersz = jedna taryfa. Ten sam układ ma eksport i import, więc najprościej:
# „Pobierz plik taryf” → zmień ceny w Excelu → „Wgraj plik taryf”.
TARIFF_COLUMNS = (
    ("name", "Nazwa"),
    ("price", "Cena 1. numeru (zł netto)"),
    ("promo_price", "Cena z promocją (zł netto)"),
    ("extra_price", "Każdy kolejny numer (zł)"),
    ("unit", "Jednostka"),
    ("features", "Co zawiera (punkty oferty, każdy w nowej linii)"),
    ("description", "Opis"),
    ("is_active", "Do sprzedaży (tak/nie)"),
    ("sort_order", "Kolejność"),
    ("promo_label", "Nazwa promocji (puste = z ustawień)"),
    ("promo_months", "Miesiące promocji"),
    ("promo_month_price", "Cena w miesiącach promocji (zł netto)"),
    ("info", "Informacje dla agenta (np. lista kanałów)"),
    ("promos", "Inne promocje (nazwa; miesiące; cena w miesiącach; cena potem)"),
)
MAX_TARIFF_FILE_BYTES = 1024 * 1024


class TariffFileError(Exception):
    """Pliku nie da się wczytać (zły format, brak kolumny „Nazwa”)."""


def _plain(text):
    import unicodedata

    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in text if ch.isalnum())


def tariffs_to_xlsx(products):
    """Plik .xlsx z taryfami (bytes) – do zapisania u siebie i wgrania na innym serwerze."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Taryfy"
    ws.append([label for _, label in TARIFF_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for p in products:
        ws.append([
            p.name, float(p.price), float(p.promo_price) if p.promo_price is not None else None,
            float(p.extra_price), p.unit, p.features, p.description, "tak" if p.is_active else "nie", p.sort_order,
            p.promo_label, p.promo_months, float(p.promo_month_price), p.info, promos_to_text(p.promos),
        ])
    for letter, width in zip("ABCDEFGHIJKLMN", (14, 14, 14, 14, 10, 60, 30, 12, 10, 22, 12, 14, 60, 50)):
        ws.column_dimensions[letter].width = width
    for row in ws.iter_rows(min_row=2):
        for index in (5, 12, 13):   # punkty oferty i ściąga dla agenta: tekst w kilku liniach
            row[index].alignment = Alignment(wrap_text=True, vertical="top")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _money(value, label, row_no, required=False):
    from decimal import Decimal, InvalidOperation

    if value is None or str(value).strip() == "":
        if required:
            raise ValueError(f"wiersz {row_no}: brak „{label}”")
        return None
    try:
        amount = Decimal(str(value).replace("zł", "").replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(f"wiersz {row_no}: „{label}” to nie kwota ({value})")
    if amount < 0:
        raise ValueError(f"wiersz {row_no}: „{label}” nie może być ujemna")
    return amount


def import_tariffs(uploaded_file):
    """Wgrywa taryfy z pliku .xlsx: taryfa o tej samej nazwie jest aktualizowana, nowa – dodawana.

    Taryf, których nie ma w pliku, nie usuwamy (historia sprzedaży zostaje). Zwraca
    {"created": [...], "updated": [...], "errors": [...]}. Błędny wiersz jest pomijany.
    """
    from zipfile import BadZipFile

    from django.db import transaction
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    if uploaded_file.size > MAX_TARIFF_FILE_BYTES:
        raise TariffFileError("Plik jest za duży (max 1 MB).")
    try:
        rows = load_workbook(uploaded_file, read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True)
    except (BadZipFile, InvalidFileException, KeyError, IndexError, OSError, ValueError):
        raise TariffFileError("Nie udało się odczytać pliku. Wgraj plik .xlsx (np. pobrany przyciskiem „Pobierz plik taryf”).")
    header = next(rows, None) or ()
    keys = {_plain(label): field for field, label in TARIFF_COLUMNS}
    # krótsze nagłówki też pasują: „Cena”, „Promocja”, „Kolejny numer”, „Punkty”
    keys.update({"cena": "price", "promocja": "promo_price", "cenazpromocja": "promo_price",
                 "kolejnynumer": "extra_price", "punkty": "features", "cozawiera": "features",
                 "dosprzedazy": "is_active", "aktywna": "is_active", "kolejnosc": "sort_order"})
    columns = {}
    for index, title in enumerate(header):
        field = keys.get(_plain(title))
        if field and field not in columns:
            columns[field] = index
    if "name" not in columns or "price" not in columns:
        raise TariffFileError("Pierwszy wiersz pliku musi mieć nagłówki – co najmniej „Nazwa” i „Cena 1. numeru (zł netto)”.")

    result = {"created": [], "updated": [], "errors": []}
    with transaction.atomic():
        for row_no, row in enumerate(rows, start=2):
            get = lambda f: row[columns[f]] if f in columns and columns[f] < len(row) else None  # noqa: E731
            name = str(get("name") or "").strip()
            if not name:
                continue
            try:
                if len(name) > 120:
                    raise ValueError(f"wiersz {row_no}: nazwa jest za długa (max 120 znaków)")
                data = {"price": _money(get("price"), "Cena 1. numeru", row_no, required=True)}
                if "promo_price" in columns:
                    data["promo_price"] = _money(get("promo_price"), "Cena z promocją", row_no)
                if "extra_price" in columns:
                    data["extra_price"] = _money(get("extra_price"), "Każdy kolejny numer", row_no) or 0
                if "promo_month_price" in columns:
                    data["promo_month_price"] = _money(get("promo_month_price"), "Cena w miesiącach promocji", row_no) or 0
                if "promo_months" in columns:
                    raw = get("promo_months")
                    try:
                        data["promo_months"] = None if raw in (None, "") else max(0, min(60, int(float(raw))))
                    except ValueError:
                        raise ValueError(f"wiersz {row_no}: „Miesiące promocji” to nie liczba")
                for field, limit in (("unit", 20), ("features", 2000), ("description", 500),
                                     ("promo_label", 80), ("info", 4000)):
                    if field in columns:
                        data[field] = str(get(field) or "").strip()[:limit]
                if "promos" in columns:
                    data["promos"] = promos_from_text(get("promos"))
                if data.get("unit") == "":
                    data["unit"] = "mies."
                if "is_active" in columns and get("is_active") is not None:
                    data["is_active"] = _plain(get("is_active")) not in ("nie", "no", "0", "false", "n")
                if "sort_order" in columns and get("sort_order") not in (None, ""):
                    try:
                        data["sort_order"] = max(0, min(32767, int(float(get("sort_order")))))
                    except ValueError:
                        raise ValueError(f"wiersz {row_no}: „Kolejność” to nie liczba")
            except ValueError as exc:
                result["errors"].append(str(exc))
                continue
            _, created = Product.objects.update_or_create(name=name, defaults=data)
            result["created" if created else "updated"].append(name)
    return result


def load_standard_tariffs(overwrite=False):
    """Dodaje brakujące taryfy z wzornika. Zwraca (dodane, zaktualizowane) – listy nazw.

    Bez `overwrite` istniejąca taryfa o tej samej nazwie zostaje bez zmian (np. z cenami
    poprawionymi przez managera); z `overwrite` dostaje z powrotem ceny z wzornika.
    """
    created, updated = [], []
    for order, (name, price, promo, extra, points, more) in enumerate(STANDARD_TARIFFS, start=1):
        defaults = tariff_defaults(order, price, promo, extra, points, more)
        if overwrite:
            _, was_created = Product.objects.update_or_create(name=name, defaults=defaults)
            (created if was_created else updated).append(name)
        else:
            product, was_created = Product.objects.get_or_create(name=name, defaults=defaults)
            if was_created:
                created.append(name)
            elif not product.is_active:
                # wyłączona (np. po „Wyczyść wszystkie”) wraca do sprzedaży z cenami z wzornika
                Product.objects.filter(pk=product.pk).update(**defaults)
                created.append(name)
            else:
                # taryfa z wcześniejszej wersji: uzupełniamy tylko puste pola (ceny managera zostają)
                fill = {f: defaults[f] for f in ("info", "promos") if defaults[f] and not getattr(product, f)}
                if product.promo_price is None and defaults["promo_price"] is not None:   # brak promocji = dodajemy z wzornika
                    fill.update({f: defaults[f] for f in ("promo_price", "promo_label", "promo_months", "promo_month_price")})
                if fill:
                    Product.objects.filter(pk=product.pk).update(**fill)
                    updated.append(name)
    return created, updated
