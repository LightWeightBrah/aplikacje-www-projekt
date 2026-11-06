"""Przykładowy plik Excela do importu (wyłącznie fikcyjne dane)."""

from openpyxl import Workbook
from openpyxl.styles import Font

SAMPLE_HEADERS = [
    "Imię i nazwisko", "Telefon", "E-mail", "Firma", "NIP", "KRS", "REGON", "Miasto",
]

# Pierwsze wiersze są poprawne; ostatnie celowo zawierają błędy i duplikat,
# żeby pokazać raport z importu.
SAMPLE_ROWS = [
    ["Adam Wiśniewski", "555 010 101", "adam.wisniewski@example.com", "Bramka Sp. z o.o.", "5550000001", "0000555001", "555000001", "Olsztyn"],
    ["Barbara Lis", "+48 555 010 102", "barbara.lis@example.com", "Bramka Sp. z o.o.", "5550000001", "0000555001", "555000001", "Olsztyn"],
    ["Cezary Mazur", 555010103, "", "Cegła i Syn", "5550000002", "", "", "Elbląg"],
    ["Dorota Kruk", "555-010-104", "dorota.kruk@example.com", "Delta Logistyka S.A.", "5550000003", "0000555003", "555000003", "Gdańsk"],
    ["Emil Wrona", "555 010 105", "", "Eko-Serwis", "", "", "", "Ostróda"],
    ["Felicja Sowa", "555 010 106", "felicja.sowa@example.com", "", "", "", "", "Iława"],
    ["Grzegorz Jeż", "555 010 107", "", "Gama Transport", "5550000004", "", "", "Mrągowo"],
    ["Halina Żuk", "555 010 108", "halina.zuk@example.com", "Hortus Ogrodnictwo", "5550000005", "", "", "Giżycko"],
    ["Ireneusz Bąk", "555 010 109", "", "Iskra Elektro", "5550000006", "", "", "Ełk"],
    ["Joanna Kos", "555 010 110", "joanna.kos@example.com", "Jantar Hotele", "5550000007", "", "", "Sopot"],
    # duplikat numeru z wiersza 3 (inny zapis telefonu)
    ["Cezary Mazur (dubel)", "+48 555 010 103", "", "Cegła i Syn", "5550000002", "", "", "Elbląg"],
    # błędne wiersze
    ["Karol Puchacz", "123", "", "Kolor Druk", "5550000008", "", "", "Olsztyn"],
    ["", "555 010 111", "", "Lux Meble", "5550000009", "", "", "Olsztyn"],
    ["Lidia Szpak", "555 010 112", "niepoprawny-mail", "", "", "", "", "Olsztyn"],
    ["Marek Gil", "555 010 113", "", "Moto-Max", "12345", "", "", "Olsztyn"],
]


# Baza w układzie z CEIDG (taki plik dostają agenci): nagłówki mają tylko kolumny A, B, C i F,
# reszta jest bez nazw. Wszystkie dane są zmyślone (numery 555…, NIP/REGON fikcyjne).
CEIDG_HEADERS = ["tel", "data kontaktu", "termin oddzwonki-opis klienta(co do kiedy)", None, None, "nip"] + [None] * 12


def _ceidg_row(phone, nip, regon, name, phone_raw, street, building, apartment, city, postal, post=None):
    from datetime import datetime
    return [phone, None, None, 1, datetime(2020, 7, 6, 23, 59), nip, regon, "F", name, phone_raw,
            street, building, apartment, None, city, postal, post or city, None]


CEIDG_ROWS = [
    _ceidg_row(555020101, 5550001001, 555001001, "ZAKŁAD STOLARSKI ADAM PRZYKŁADOWY", 555020101, "ul. Leśna", 4, None, "Olsztyn", "10-001"),
    _ceidg_row(555020102, 5550001002, 55500102, "\"KWIACIARNIA POD LIPĄ\" BEATA TESTOWA", "555 02 01 02", "ul. Lipowa", 12, 3, "Ostróda", "14-100"),
    _ceidg_row(555020103, 5550001003, 555001003, "USŁUGI BUDOWLANE CEZARY FIKCYJNY", "48555020103", None, 27, None, "Kolonia Wzorcowa", "-", "Barczewo"),
    # przesunięty wiersz: cudzysłów rozbił nazwę firmy na dwie kolumny (jak w prawdziwych plikach)
    ["555020104", None, None, 1, None, 5550001004, 555001004, "F", "\"HURTOWNIA \\\"ZEFIR\\\"", " DOROTA PRZYKŁAD\"",
     "555-020-104", "ul. Polna", 8, None, None, "Elbląg", "82-300", "Elbląg"],
    # numer budynku zamieniony przez Excela na datę (5/2 -> 5 lutego)
    _ceidg_row(555020105, 5550001005, 555001005, "SKLEP SPOŻYWCZY EMIL WZÓR", 555020105, "ul. Długa", None, None, "Iława", "14-200"),
]


# Większa baza do pokazu (układ CEIDG): kilka plików po kilkadziesiąt firm, każdy z innymi numerami,
# żeby dało się przypisać je różnym agentom. Wszystko zmyślone i zawsze takie samo (stałe ziarno).
DEMO_PARTS = 3
DEMO_ROWS_PER_PART = 80

_DEMO_TRADES = [
    "ZAKŁAD STOLARSKI", "KWIACIARNIA", "USŁUGI BUDOWLANE", "SKLEP SPOŻYWCZY", "SALON FRYZJERSKI",
    "MECHANIKA POJAZDOWA", "BIURO RACHUNKOWE", "PIEKARNIA", "TRANSPORT DROGOWY", "GABINET STOMATOLOGICZNY",
    "SKLEP ODZIEŻOWY", "USŁUGI REMONTOWE", "PRZEDSZKOLE NIEPUBLICZNE", "PIZZERIA", "AGENCJA REKLAMOWA",
    "HURTOWNIA BUDOWLANA", "SALON KOSMETYCZNY", "INSTALACJE ELEKTRYCZNE", "SKLEP ZOOLOGICZNY", "MYJNIA SAMOCHODOWA",
    "KANCELARIA PRAWNA", "SERWIS KOMPUTEROWY", "GOSPODARSTWO AGROTURYSTYCZNE", "APTEKA", "STUDIO FOTOGRAFICZNE",
    "USŁUGI OGRODNICZE", "SZKOŁA JAZDY", "WARSZTAT BLACHARSKI", "KWATERY PRYWATNE", "SKLEP MEBLOWY",
]
_DEMO_FIRST = [
    ("ADAM", "BEATA"), ("CEZARY", "DOROTA"), ("EMIL", "EWA"), ("FILIP", "GRAŻYNA"), ("GRZEGORZ", "HALINA"),
    ("IGOR", "IWONA"), ("JAN", "JOANNA"), ("KAROL", "KATARZYNA"), ("ŁUKASZ", "LIDIA"), ("MAREK", "MAŁGORZATA"),
    ("NORBERT", "NATALIA"), ("OSKAR", "OLGA"), ("PIOTR", "PAULINA"), ("ROBERT", "RENATA"), ("SŁAWOMIR", "SYLWIA"),
    ("TOMASZ", "TERESA"), ("WOJCIECH", "WIOLETTA"), ("ZBIGNIEW", "ZOFIA"),
]
# Nazwiska w formie męskiej i żeńskiej (fikcyjne kombinacje).
_DEMO_LAST = [
    ("NOWAK", "NOWAK"), ("KOWALCZYK", "KOWALCZYK"), ("WIŚNIEWSKI", "WIŚNIEWSKA"), ("LEWANDOWSKI", "LEWANDOWSKA"),
    ("ZIELIŃSKI", "ZIELIŃSKA"), ("SZYMAŃSKI", "SZYMAŃSKA"), ("WOŹNIAK", "WOŹNIAK"), ("DĄBROWSKI", "DĄBROWSKA"),
    ("KOZŁOWSKI", "KOZŁOWSKA"), ("JANKOWSKI", "JANKOWSKA"), ("MAZUR", "MAZUR"), ("KRAWCZYK", "KRAWCZYK"),
    ("PIOTROWSKI", "PIOTROWSKA"), ("GRABOWSKI", "GRABOWSKA"), ("PAWŁOWSKI", "PAWŁOWSKA"), ("MICHALSKI", "MICHALSKA"),
    ("KRÓL", "KRÓL"), ("WIECZOREK", "WIECZOREK"), ("JABŁOŃSKI", "JABŁOŃSKA"), ("WRÓBEL", "WRÓBEL"),
]
_DEMO_COMPANY_WORDS = ["ALFA", "POLMAR", "WARMIA", "MAZURY", "JANTAR", "ZEFIR", "BRAMKA", "KORAL", "OMEGA", "SOLAR"]
_DEMO_CITIES = [
    ("Olsztyn", "10-{:03d}"), ("Elbląg", "82-3{:02d}"), ("Ełk", "19-3{:02d}"), ("Iława", "14-2{:02d}"),
    ("Ostróda", "14-1{:02d}"), ("Giżycko", "11-5{:02d}"), ("Mrągowo", "11-7{:02d}"), ("Kętrzyn", "11-4{:02d}"),
    ("Bartoszyce", "11-2{:02d}"), ("Szczytno", "12-1{:02d}"), ("Działdowo", "13-2{:02d}"), ("Nidzica", "13-1{:02d}"),
]
_DEMO_STREETS = [
    "ul. Leśna", "ul. Lipowa", "ul. Polna", "ul. Długa", "ul. Kościuszki", "ul. Mickiewicza", "ul. Słoneczna",
    "ul. Ogrodowa", "ul. Kolejowa", "ul. Szkolna", "ul. Jagiellońska", "ul. Warszawska", "al. Wojska Polskiego",
    "ul. Grunwaldzka", "ul. Partyzantów", "ul. Kwiatowa",
]


def demo_ceidg_rows(part):
    """Wiersze CEIDG dla pliku `part` (1..DEMO_PARTS). Numery 555 3/4/5 xx xxx – inne w każdym pliku
    i inne niż w małych plikach przykładowych (555 01…, 555 02…)."""
    import random
    rng = random.Random(1000 + part)
    rows = []
    for i in range(1, DEMO_ROWS_PER_PART + 1):
        phone = int(f"555{2 + part}{i:05d}")
        nip = int(f"5552{part}{i:05d}")
        regon = int(f"5552{part}{i:04d}")
        male = rng.random() < 0.55
        first = rng.choice(_DEMO_FIRST)[0 if male else 1]
        last = rng.choice(_DEMO_LAST)[0 if male else 1]
        trade = rng.choice(_DEMO_TRADES)
        kind = rng.random()
        if kind < 0.6:
            name = f"{trade} {first} {last}"
        elif kind < 0.85:
            name = f"\"{rng.choice(_DEMO_COMPANY_WORDS)}\" {trade} {first} {last}"
        else:
            name = f"{rng.choice(_DEMO_COMPANY_WORDS)}-{last} SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ"
        city, postal = rng.choice(_DEMO_CITIES)
        street = rng.choice(_DEMO_STREETS) if rng.random() < 0.9 else None   # część to wsie bez ulic
        building = rng.randint(1, 120)
        apartment = rng.randint(1, 40) if rng.random() < 0.3 else None
        phone_raw = rng.choice([phone, f"{str(phone)[:3]} {str(phone)[3:6]} {str(phone)[6:]}", f"48{phone}"])
        rows.append(_ceidg_row(phone, nip, regon, name, phone_raw, street, building, apartment,
                               city, postal.format(rng.randint(1, 99))))
    return rows


def build_demo_ceidg_workbook(part):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Arkusz1"
    sheet.append(CEIDG_HEADERS)
    for row in demo_ceidg_rows(part):
        sheet.append(row)
    return workbook


def build_ceidg_sample_workbook():
    from datetime import datetime
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Arkusz1"
    sheet.append(CEIDG_HEADERS)
    for row in CEIDG_ROWS:
        sheet.append(row)
    sheet.cell(row=6, column=12, value=datetime(2026, 2, 5))  # „5/2” zapisane jako data
    return workbook


def build_sample_workbook():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Baza kontaktów"
    sheet.append(SAMPLE_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in SAMPLE_ROWS:
        sheet.append(row)
    for letter, width in zip("ABCDEFGH", (26, 18, 30, 26, 14, 14, 14, 14)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"
    return workbook