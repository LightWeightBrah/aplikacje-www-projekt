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