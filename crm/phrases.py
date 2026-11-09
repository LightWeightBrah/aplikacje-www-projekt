"""Gotowe przyciski przy wyniku rozmowy: wzory komentarzy i powody odmowy.

Manager może je zmienić w „Ustawienia i oferty” → „Wynik rozmowy: powody i wzory komentarzy”
(pole TeamRules.quick_phrases). Gdy dla danego wyniku nic nie zapisano, obowiązuje lista domyślna stąd.
Klik w przycisk dopisuje tekst do komentarza rozmowy, więc w bazie zostaje zwykły tekst.
"""

# klucz = wynik rozmowy (Activity.Outcome); "refused" = powody odmowy („Dlaczego odmówił?”)
DEFAULT_PHRASES = {
    "no_answer": ["Poczta głosowa", "Zajęte", "Odrzucił połączenie", "Sygnał, nikt nie odbiera"],
    "callback": ["Zajęty, prosił później", "Decyduje szef / wspólnik", "Sekretariat – połączyć z decydującym",
                 "W trasie, oddzwonić", "Na urlopie"],
    "interested": ["Poda e-mail później", "Musi porozmawiać ze wspólnikiem", "Sprawdzi obecną umowę",
                   "Umowa kończy się za kilka miesięcy", "Chce ofertę na więcej numerów"],
    "offer": ["Porównuje z obecną umową", "Decyzja po rozmowie ze wspólnikiem", "Interesuje go też internet",
              "Ma ofertę konkurencji"],
    "sold": ["Przeniesienie numerów", "Nowe numery", "Umowa przez kuriera", "Klient poleca znajomych"],
    # „Już jest w T-Mobile” i „Nie prowadzi już firmy” wychodzą w trakcie rozmowy: to odmowa (był telefon),
    # a nie „Pomiń firmę” (to tylko bez dzwonienia)
    "refused": ["Już jest w T-Mobile", "Nie prowadzi już firmy", "Ma umowę w innej sieci", "Za drogo",
                "Nie jest zainteresowany", "Nie decyduje o tym", "Prosi o brak kontaktu"],
    "wrong_number": ["Numer prywatny – nie firma", "Numer nieaktywny", "Inna firma pod tym numerem"],
    # „Pomiń firmę” (bez dzwonienia): powód decyduje, co dalej z firmą
    "skip_never": ["Firma nie istnieje / zawieszona", "Brak numeru / numer stacjonarny", "Duplikat"],   # nie dzwonić więcej
    "skip_lost": ["Już jest w T-Mobile", "Inny powód"],   # zamknięty jak po odmowie
}
SKIP_LISTS = ("skip_never", "skip_lost")
PHRASE_KEYS = tuple(DEFAULT_PHRASES)
PHRASES_MAX = 15        # tyle przycisków najwyżej przy jednym wyniku
PHRASE_MAX_LEN = 60     # długość jednego przycisku


def effective_phrases(rules):
    """Listy, które widzą agenci: zapisane przez managera, a gdzie nic nie zapisano – domyślne."""
    stored = rules.quick_phrases if isinstance(rules.quick_phrases, dict) else {}
    return {key: list(stored[key]) if isinstance(stored.get(key), list) else list(default)
            for key, default in DEFAULT_PHRASES.items()}


def skip_never(rules, reason):
    """Czy powód pominięcia oznacza „nie dzwonić więcej” (inaczej: firma zamknięta jak po odmowie)."""
    key = (reason or "").strip().lower()
    return any(key == r.lower() for r in effective_phrases(rules)["skip_never"])


def clean_phrases(value):
    """Sprawdza i porządkuje listy z formularza. Zwraca (słownik, błąd albo None)."""
    if not isinstance(value, dict):
        return None, "Nieprawidłowe listy przycisków."
    out = {}
    for key, items in value.items():
        if key not in DEFAULT_PHRASES:
            return None, f"Nieznany wynik rozmowy: {key}."
        if not isinstance(items, list) or any(not isinstance(i, str) for i in items):
            return None, "Każda lista to teksty, jeden przycisk w linii."
        seen, clean = set(), []
        for item in items:
            text = " ".join(item.split())
            if not text or text.lower() in seen:
                continue
            if len(text) > PHRASE_MAX_LEN:
                return None, f"„{text[:30]}…” jest za długie (najwyżej {PHRASE_MAX_LEN} znaków)."
            seen.add(text.lower())
            clean.append(text)
        if len(clean) > PHRASES_MAX:
            return None, f"Najwyżej {PHRASES_MAX} przycisków przy jednym wyniku."
        out[key] = clean
    return out, None
