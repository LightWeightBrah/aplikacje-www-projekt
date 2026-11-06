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