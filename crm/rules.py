"""Zasady pracy z leadami ustawiane przez managera (limit prób, ponowne próby, zwroty do puli).

`get_rules()` zwraca jedyny wiersz TeamRules; gdy go jeszcze nie ma, powstaje z wartości
domyślnych z config/settings.py (LEAD_*, CALLBACK_RETRY_STEPS, WORK_DAYS).
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import TeamRules

# nazwa pola -> (minimum, maksimum, jednostka, nazwa w settings.py)
FIELD_LIMITS = {
    "max_attempts_per_agent": (1, 10, "prób", "LEAD_MAX_ATTEMPTS_PER_AGENT"),
    "unreachable_after_agents": (1, 20, "agentów", "LEAD_UNREACHABLE_AFTER_AGENTS"),
    "retry_after_first_minutes": (5, 10080, "min", None),
    "retry_after_second_minutes": (5, 20160, "min", None),
    "retry_after_next_minutes": (5, 43200, "min", None),
    "work_start_hour": (0, 23, "godz.", None),
    "work_end_hour": (1, 24, "godz.", None),
    "claim_timeout_hours": (1, 720, "godz.", "LEAD_CLAIM_TIMEOUT_HOURS"),
    "unreachable_cooldown_days": (1, 365, "dni", "LEAD_UNREACHABLE_COOLDOWN_DAYS"),
    "sms_reminder_minutes": (10, 10080, "min", None),   # od 10 minut do 7 dni przed terminem
    "sms_min_gap_minutes": (1, 1440, "min", None),
    "goal_minutes_per_hour": (1, 60, "min", None),
}
SMS_MAX_PER_DAY = 5   # twardy limit na jeden numer w ciągu doby, niezależnie od ustawień
# Rodzaje SMS-ów z przypomnieniem: pola włącz/treść w TeamRules
SMS_KINDS = ("offer", "talked", "stranger")
SMS_TEXT_MAX = 320   # ok. 5 SMS-ów z polskimi znakami (wysyła telefon agenta, w abonamencie)
# Pola, które można wstawić do treści SMS-a (crm.sms.render_sms)
SMS_PLACEHOLDERS = ("imie", "kiedy", "Kiedy", "godzina", "data")

# Ponowne próby po nieodebranym umówionym terminie: ile kroków i jaki zakres jednego kroku (min)
CALLBACK_STEPS_MAX = 8
CALLBACK_STEP_LIMITS = (5, 10080)
DEFAULT_CALLBACK_STEPS = [15, 60, 120, 1440]
DEFAULT_WORK_DAYS = "12345"


def default_values():
    """Wartości domyślne: z settings.py, a tam gdzie ich nie ma, z definicji pól modelu."""
    values = {}
    for name, (_lo, _hi, _unit, setting) in FIELD_LIMITS.items():
        if setting and hasattr(settings, setting):
            values[name] = getattr(settings, setting)
        else:
            values[name] = TeamRules._meta.get_field(name).default
    values["callback_retry_steps"] = list(getattr(settings, "CALLBACK_RETRY_STEPS", DEFAULT_CALLBACK_STEPS))
    values["work_days"] = getattr(settings, "WORK_DAYS", DEFAULT_WORK_DAYS)
    for kind in SMS_KINDS:
        for name in (f"sms_{kind}_enabled", f"sms_{kind}_text"):
            values[name] = TeamRules._meta.get_field(name).default
    values["require_skip_reason"] = False
    values["require_refuse_reason"] = False
    values["quick_phrases"] = {}   # = listy domyślne z crm/phrases.py
    return values


# ---------- dni pracy ----------

def work_days(rules):
    """Dni pracy jako zbiór numerów ISO (1 = poniedziałek … 7 = niedziela); pusty zapis = pon–pt."""
    days = {int(c) for c in (rules.work_days or "") if c in "1234567"}
    return days or {1, 2, 3, 4, 5}


def is_work_day(rules, moment):
    return timezone.localtime(moment).isoweekday() in work_days(rules)


def next_work_day(rules, moment):
    """Ten sam moment przesunięty na najbliższy dzień pracy (ten sam dzień, jeśli jest dniem pracy)."""
    for _ in range(7):
        if is_work_day(rules, moment):
            return moment
        moment = moment + timedelta(days=1)
    return moment


def fit_work_time(rules, moment):
    """Moment w godzinach i dniach pracy: dzień wolny -> ta sama godzina najbliższego dnia pracy,
    przed otwarciem -> otwarcie, po zamknięciu -> otwarcie następnego dnia pracy."""
    local = next_work_day(rules, timezone.localtime(moment))
    opening = local.replace(hour=rules.work_start_hour, minute=0, second=0, microsecond=0)
    if local < opening:
        return opening
    if local.hour + local.minute / 60 < rules.work_end_hour:
        return local
    return next_work_day(rules, opening + timedelta(days=1))


def callback_retry_at(rules, missed_before, now=None):
    """Termin ponownej próby po nieodebranym umówionym kontakcie.

    `missed_before` = ile razy klient już wcześniej nie odebrał w umówionym terminie (0 przy pierwszym).
    Kolejne kroki z ustawień (np. 15 min, 1 h, 2 h, 1 dzień); po ostatnim kolejne co tyle, ile ostatni.
    """
    now = now or timezone.now()
    steps = [int(s) for s in (rules.callback_retry_steps or []) if str(s).isdigit()] or DEFAULT_CALLBACK_STEPS
    delay = steps[min(max(missed_before, 0), len(steps) - 1)]
    return fit_work_time(rules, now + timedelta(minutes=delay))


def get_rules():
    rules, _ = TeamRules.objects.get_or_create(pk=1, defaults=default_values())
    return rules


def retry_delay_minutes(rules, tries_before):
    """Za ile minut ponowić próbę; tries_before = ile razy ten agent już nie dodzwonił się wcześniej."""
    if tries_before <= 0:
        return rules.retry_after_first_minutes
    if tries_before == 1:
        return rules.retry_after_second_minutes
    return rules.retry_after_next_minutes


SLOTS = 3   # dzień pracy dzielimy na tyle pór (np. rano / w środku dnia / po południu)


def _slot_starts(rules):
    """Początki pór dnia w minutach od północy, zaokrąglone do pełnych 30 min."""
    start, total = rules.work_start_hour * 60, (rules.work_end_hour - rules.work_start_hour) * 60
    return [start + round(total * i / SLOTS / 30) * 30 for i in range(SLOTS)]


def _slot_of(rules, moment):
    """Numer pory dnia (0..2) albo None, gdy moment wypada poza godzinami pracy."""
    minutes = moment.hour * 60 + moment.minute
    if not rules.work_start_hour * 60 <= minutes < rules.work_end_hour * 60:
        return None
    starts = _slot_starts(rules)
    return max(i for i, s in enumerate(starts) if minutes >= s or i == 0)


def attempt_times(rules, attempts):
    """Momenty wcześniejszych prób do rotacji godzin; `attempts` = pary (kiedy dzwoniono, na kiedy przełożono).

    Próba wykonana poza godzinami pracy (np. test wieczorem) nie zajmuje żadnej pory sama z siebie,
    więc liczymy ją jako godzinę, na którą system ją przełożył. Dzięki temu rotacja idzie dalej
    (9:30, 12:00, 15:00, potem luki), zamiast zaczynać od nowa przy każdym nieodebraniu.
    """
    times = []
    for created, moved in attempts:
        times.append(created)
        local = timezone.localtime(created)
        if moved is not None and _slot_of(rules, local) is None:
            times.append(moved)
    return times


def _gap_time(rules, day, previous, earliest=None):
    """Godzina (co 30 min, w godzinach pracy) możliwie najdalsza od godzin wcześniejszych prób.

    Używane, gdy trzy podstawowe pory dnia są już zużyte: zamiast wracać do 9:30, wypełniamy luki
    (np. po 9:30, 12:00 i 15:00 dostaniemy 13:30, potem 16:30, potem 10:30...). `earliest` = nie wcześniej
    niż ten moment; gdy dziś nie ma już sensownej godziny, szukamy od rana kolejnego dnia.
    """
    done = [t.hour * 60 + t.minute for t in map(timezone.localtime, previous)
            if rules.work_start_hour * 60 <= t.hour * 60 + t.minute < rules.work_end_hour * 60]
    grid = range(rules.work_start_hour * 60 + 30, rules.work_end_hour * 60, 30)
    for offset in range(0, 15):
        base = day + timedelta(days=offset)
        if not is_work_day(rules, base):   # dni wolne (np. sobota, niedziela) pomijamy
            continue
        cands = [m for m in grid if earliest is None or offset > 0
                 or base.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0) >= earliest]
        if cands:
            best = max(cands, key=lambda m: (min((abs(m - d) for d in done), default=999), -m))
            return base.replace(hour=best // 60, minute=best % 60, second=0, microsecond=0)
    return day


def suggest_retry(rules, tries_before, now=None, previous=()):
    """Proponowany termin ponownej próby po nieodebranym telefonie.

    Termin wypada w godzinach pracy. Gdy wyliczony moment jest poza nimi (np. dzwonisz wieczorem),
    wybieramy początek najbliższego dnia pracy, ale porę dnia (rano / środek dnia / popołudnie)
    rotujemy z numerem próby, żeby nie wychodziło zawsze to samo (9:30, 12:00, 15:00, ...).
    `previous` = momenty wcześniejszych prób (z bieżącą włącznie): pory, w których już dzwoniono
    w godzinach pracy, pomijamy, bo klient, który nie odbiera o 10, może odebrać po południu.
    """
    now = now or timezone.now()
    moment = timezone.localtime(now + timedelta(minutes=retry_delay_minutes(rules, tries_before)))
    start, end = rules.work_start_hour, rules.work_end_hour
    starts = _slot_starts(rules)
    day_start = moment.replace(hour=start, minute=0, second=0, microsecond=0)
    used = {_slot_of(rules, timezone.localtime(p)) for p in previous} - {None}

    def at(base, slot):   # pół godziny po początku pory, żeby nie dzwonić równo z otwarciem
        t = starts[slot] + 30
        return base.replace(hour=t // 60, minute=t % 60, second=0, microsecond=0)

    forced_day = None
    if not is_work_day(rules, moment):   # wypada w dzień wolny: najbliższy dzień pracy
        forced_day = next_work_day(rules, day_start)
    elif moment < day_start:
        forced_day = day_start
    elif moment.hour + moment.minute / 60 >= end:
        forced_day = next_work_day(rules, day_start + timedelta(days=1))
    if forced_day is not None:
        # wybór pory z rotacją: próba 0 -> rano, 1 -> środek dnia, 2 -> popołudnie, ...
        order = [(tries_before + i) % SLOTS for i in range(SLOTS)]
        slot = next((i for i in order if i not in used), None)
        if slot is None:   # wszystkie pory były już próbowane: szukamy godziny najdalszej od dotychczasowych
            return _gap_time(rules, forced_day, previous)
        return at(forced_day, slot)

    here = _slot_of(rules, moment)
    if len(used) >= SLOTS:
        # termin odległy o kilka dni nie wiąże godziny; ograniczenie „nie wcześniej niż” dotyczy tylko dzisiejszego dnia
        return _gap_time(rules, day_start, previous, earliest=moment if moment.date() == timezone.localtime(now).date() else None)
    if here not in used:
        return moment
    for slot in range(here + 1, SLOTS):   # później tego samego dnia
        if slot not in used:
            return at(moment, slot)
    next_day = next_work_day(rules, day_start + timedelta(days=1))   # inaczej następnego dnia pracy, od pierwszej wolnej pory
    for slot in list(range(here + 1, SLOTS)) + list(range(0, here)):
        if slot not in used:
            return at(next_day, slot)
    return moment
