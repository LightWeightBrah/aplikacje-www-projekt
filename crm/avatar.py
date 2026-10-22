"""Zdjęcie profilowe: walidacja małego obrazka przesłanego jako data URL.

Przeglądarka zmniejsza zdjęcie do ok. 160 px (canvas), więc w bazie ląduje kilka KB
i nie potrzebujemy ani Pillow, ani katalogu MEDIA. Serwer i tak sprawdza typ, rozmiar
i „magiczne bajty” pliku, żeby nie dało się wrzucić czegoś innego niż obrazek.
"""

import base64
import binascii
import re

MAX_AVATAR_BYTES = 150 * 1024
_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$")


def _looks_like(mime, raw):
    if mime == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    return raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"


def validate_avatar(value):
    """Zwraca komunikat błędu albo None, gdy data URL jest poprawnym małym obrazkiem."""
    if not isinstance(value, str) or len(value) > MAX_AVATAR_BYTES * 2:
        return "Zdjęcie jest zbyt duże."
    match = _DATA_URL.match(value)
    if not match:
        return "Dozwolone formaty zdjęcia: PNG, JPEG, WebP."
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        return "Nieprawidłowe dane zdjęcia."
    if len(raw) > MAX_AVATAR_BYTES:
        return "Zdjęcie jest zbyt duże (maks. 150 KB po zmniejszeniu)."
    if not _looks_like(match.group(1), raw):
        return "Plik nie wygląda na obrazek w podanym formacie."
    return None
