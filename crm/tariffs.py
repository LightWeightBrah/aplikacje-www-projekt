"""Taryfy firmowe T-Mobile z wzornika ofert zespołu (S / M100 / M / L / VIP).

Używa ich `seed_demo` (lokalnie), przycisk „Wgraj taryfy z wzornika” na stronie „Taryfy i produkty”
i komenda `manage.py load_tariffs` (także na serwerze). Ceny potem zmienia manager na stronie.
"""

from decimal import Decimal

from .models import Product

COMMON = [
    "Nielimitowane rozmowy, SMS-y i MMS-y w kraju",
    "Roaming w UE: 23 GB",
    "Prędkość internetu: 150 Mb/s",
]
UNLIMITED = ["Internet bez limitu", "Karta internetowa bez limitu"]


def features(*extra):
    return chr(10).join(COMMON + list(extra))   # chr(10) = nowa linia: jeden punkt oferty w linii


# nazwa, cena netto 1. numeru, cena z promocją „6 miesięcy gratis”, każdy kolejny numer, punkty oferty
STANDARD_TARIFFS = (
    ("S", Decimal("45"), Decimal("50"), Decimal("25"),
     features("Internet: 50 GB przez 3 miesiące, potem 40 GB (współdzielony)", "Karta internetowa: 40 GB")),
    ("M100", Decimal("55"), Decimal("65"), Decimal("25"),
     features("Internet: 100 GB (współdzielony)", "Karta internetowa: 100 GB")),
    ("M", Decimal("70"), Decimal("85"), Decimal("25"), features(*UNLIMITED)),
    ("L", Decimal("85"), Decimal("105"), Decimal("25"), features(*UNLIMITED)),
    ("VIP", Decimal("105"), Decimal("140"), Decimal("25"), features(*UNLIMITED)),
)


def tariff_defaults(order, price, promo, extra, points):
    return {"price": price, "promo_price": promo, "extra_price": extra, "features": points,
            "unit": "mies.", "sort_order": order, "is_active": True}


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
        ])
    for letter, width in zip("ABCDEFGHI", (14, 14, 14, 14, 10, 60, 30, 12, 10)):
        ws.column_dimensions[letter].width = width
    for row in ws.iter_rows(min_row=2):
        row[5].alignment = Alignment(wrap_text=True, vertical="top")
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
                for field, limit in (("unit", 20), ("features", 2000), ("description", 500)):
                    if field in columns:
                        data[field] = str(get(field) or "").strip()[:limit]
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
    for order, (name, price, promo, extra, points) in enumerate(STANDARD_TARIFFS, start=1):
        defaults = tariff_defaults(order, price, promo, extra, points)
        if overwrite:
            _, was_created = Product.objects.update_or_create(name=name, defaults=defaults)
            (created if was_created else updated).append(name)
        else:
            _, was_created = Product.objects.get_or_create(name=name, defaults=defaults)
            if was_created:
                created.append(name)
    return created, updated
