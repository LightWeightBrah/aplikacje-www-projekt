"""Praca agenta w Solid CRM: kolejka „Następny klient”, follow-upy, oferty i dialer.

Kolejność pracy (jak „work queue” w dużych CRM-ach: agent nie wybiera, system podaje
najważniejszy kontakt):
  1. oferty i zainteresowani z terminem, który właśnie mija (follow-up po ofercie),
  2. umówione oddzwonienia,
  3. ponowne próby do tych, którzy nie odebrali,
  4. nowe firmy: najpierw z bazy agenta, potem ze wspólnej puli.
"""

import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape
from django.utils.text import slugify
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    NON_CALL_OUTCOMES,
    Activity,
    CallRequest,
    DialerDevice,
    DialerPairing,
    Lead,
    Offer,
    OfferConfig,
    RecordingConfig,
    SaleItem,
)
from .services import (
    ACTIVE_STATUSES,
    NoLeadsAvailable,
    OverdueCallbacks,
    claim_next_lead,
    day_bounds,
    register_activity,
)

DUE_SOON = timedelta(minutes=2)       # termin za chwilę = już do zrobienia
GOAL_MINUTES_PER_HOUR = 15            # cel startowy: 15 minut rozmów na godzinę pracy (manager zmienia go w ustawieniach)


def team_goal_per_hour():
    """Cel całego zespołu (Ustawienia i oferty → „Cel minut rozmów”), domyślnie 15."""
    from .rules import get_rules

    return get_rules().goal_minutes_per_hour or GOAL_MINUTES_PER_HOUR


def own_goal_per_hour(user):
    """Własny cel tej osoby (Zespół → Edytuj) albo None, gdy obowiązuje cel zespołu."""
    from .models import AgentProfile

    return AgentProfile.objects.filter(user=user).values_list("goal_minutes_per_hour", flat=True).first()


def goal_per_hour(user):
    """Cel minut rozmów na godzinę pracy tej osoby: własny, a bez niego cel zespołu."""
    return own_goal_per_hour(user) or team_goal_per_hour()

SEGMENTS = {
    "offer": (Lead.Status.OFFER, Lead.Status.INTERESTED),
    "callback": (Lead.Status.CALLBACK,),
}


def segment_of(lead):
    """offer = po ofercie / zainteresowany, callback = umówione oddzwonienie,
    retry = dzwoniliśmy, nie odebrał, new = jeszcze nikt nie dzwonił, closed = zamknięty."""
    if lead.status in SEGMENTS["offer"]:
        return "offer"
    if lead.status == Lead.Status.CALLBACK:
        return "callback"
    if lead.status == Lead.Status.NEW:
        return "retry" if lead.attempts_count else "new"
    return "closed"


def next_work(user):
    """Najważniejszy kontakt dla agenta teraz: (lead, powód) albo (None, None)."""
    now = timezone.now()
    mine = Lead.objects.filter(owner=user)
    steps = (
        ("offer", mine.filter(status__in=SEGMENTS["offer"], next_contact_at__lte=now + DUE_SOON)),
        ("callback", mine.filter(status=Lead.Status.CALLBACK, next_contact_at__lte=now + DUE_SOON)),
        ("retry", mine.filter(status=Lead.Status.NEW, attempts_count__gt=0, next_contact_at__lte=now)),
        ("new", mine.filter(status=Lead.Status.NEW, attempts_count=0)),
    )
    for reason, queryset in steps:
        lead = queryset.order_by("next_contact_at", "claimed_at", "id").first()
        if lead:
            return lead, reason
    try:
        return claim_next_lead(user), "new"
    except (NoLeadsAvailable, OverdueCallbacks):
        return None, None


class ContactExists(Exception):
    """Numer jest już w CRM: zwracamy istniejącego leada zamiast tworzyć duplikat."""

    def __init__(self, lead):
        super().__init__(lead.pk)
        self.lead = lead


def create_contact(user, phone, company_name="", contact_name="", email="", note="", referred_by=None):
    """Agent dodaje klienta sam, np. z polecenia („kogo Pan zna…”). Klient od razu należy do agenta.

    Numer, który już jest w CRM (także jako dodatkowy), nie tworzy duplikatu: ContactExists.
    """
    from .models import Company
    from .services import _local_phone

    key = _local_phone(phone)
    for lead in Lead.objects.select_related("company").only("id", "phone", "phone_alt", "owner_id", "company__name"):
        if key and key in (_local_phone(lead.phone), _local_phone(lead.phone_alt)):
            raise ContactExists(lead)
    with transaction.atomic():
        company = Company.objects.create(name=company_name.strip()) if company_name.strip() else None
        lead = Lead.objects.create(owner=user, claimed_at=timezone.now(), phone=phone.strip(), company=company,
                                   contact_name=contact_name.strip(), email=email.strip())
        source = f"Polecenie od: {referred_by.display_name} ({referred_by.phone})" if referred_by else "Kontakt dodany ręcznie przez agenta"
        Activity.objects.create(lead=lead, agent=user, outcome=Activity.Outcome.NOTE,
                                note=f"{source}. {note.strip()}".strip().rstrip("."))
    return lead


def shared_pool_left(user):
    """Ile nowych firm we wspólnej puli może jeszcze dostać ten agent (bez baz przypisanych innym)."""
    return (
        Lead.objects.filter(owner__isnull=True, status=Lead.Status.NEW)
        .filter(Q(batch__isnull=True) | Q(batch__assigned_to__isnull=True))
        .exclude(assignments__from_agent=user)
        .count()
    )


def work_counts(user):
    """Liczniki segmentów agenta (wszystkie aktywne) i ile z nich czeka już teraz."""
    now = timezone.now()
    mine = Lead.objects.filter(owner=user, status__in=ACTIVE_STATUSES)
    counts = {}
    for key, queryset in (
        ("offer", mine.filter(status__in=SEGMENTS["offer"])),
        ("callback", mine.filter(status=Lead.Status.CALLBACK)),
        ("retry", mine.filter(status=Lead.Status.NEW, attempts_count__gt=0)),
        ("new", mine.filter(status=Lead.Status.NEW, attempts_count=0)),
    ):
        due_limit = now if key == "retry" else now + DUE_SOON
        counts[key] = {
            "total": queryset.count(),
            "due": queryset.filter(next_contact_at__lte=due_limit).count() if key != "new" else queryset.count(),
        }
    return counts


# ------------------------------------------------------------------ statystyki dnia

def minutes_by_hour(user, day=None):
    """Minuty rozmów (z dialera) w każdej godzinie dnia: [{hour, minutes}] dla 0–23."""
    start, end = day_bounds(day or timezone.localdate())
    calls = CallRequest.objects.filter(
        user=user, state=CallRequest.State.DONE, answered=True, ended_at__gte=start, ended_at__lt=end
    ).values_list("ended_at", "duration_seconds")
    seconds = [0] * 24
    for ended, duration in calls:
        seconds[timezone.localtime(ended).hour] += duration or 0
    return [{"hour": h, "minutes": round(s / 60, 2)} for h, s in enumerate(seconds)]


# ------------------------------------------------------------------ dzień pracy (przyjście / wyjście)

BREAK_MINUTES = 15  # tyle minut bez pracy = przerwa (krótką tylko pokazujemy, nie odejmujemy)
IDLE_MINUTES = 45   # od tylu minut bez pracy = poza pracą (odejmujemy); też: „do teraz” przestaje rosnąć


LOGOUT_GRACE_MINUTES = 15   # wylogowanie najwyżej tyle po ostatniej pracy = koniec pracy (np. dopisał notatki)


def touch_workday(user, work=False):
    """Zapisuje ruch agenta w CRM.

    work=False – sama obecność (logowanie, otwarcie „Dzwonienia”): nie zaczyna dnia pracy.
    work=True – prawdziwa praca („Następny klient”, telefon, zapis rozmowy): pierwsza = początek pracy,
    każda kolejna = ostatnia praca. Przerwa = co najmniej BREAK_MINUTES między dwiema pracami.
    Samo odświeżanie liczników w tle nie jest żadnym ruchem. Ruch po wylogowaniu znów otwiera dzień.
    """
    from .models import WorkDay

    now = timezone.now()
    day = timezone.localdate()
    wd, _ = WorkDay.objects.get_or_create(user=user, date=day, defaults={"first_seen": now, "last_seen": now})
    fields = ["last_seen", "ended_at"]
    wd.last_seen, wd.ended_at = max(now, wd.last_seen), None
    if work:
        if wd.last_work and now - wd.last_work >= timedelta(minutes=BREAK_MINUTES):
            wd.breaks = list(wd.breaks or []) + [[wd.last_work.isoformat(), now.isoformat()]]
        wd.first_work = wd.first_work or now
        wd.last_work = max(now, wd.last_work or now)
        fields += ["first_work", "last_work", "breaks"]
    wd.save(update_fields=fields)


def end_workday(user):
    """Agent się wylogował: zapisujemy godzinę wyjścia (cel minut przestaje rosnąć)."""
    from .models import WorkDay

    now = timezone.now()
    WorkDay.objects.filter(user=user, date=timezone.localdate()).update(ended_at=now, last_seen=now)


def _break_minutes(breaks):
    return sum(int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() // 60) for a, b in breaks)


def work_span(user, day):
    """(początek, koniec, czy zakończył) dnia pracy albo (None, None, False), gdy agent nie pracował.

    Liczy się PRACA, a nie samo zalogowanie: początek = pierwszy „Następny klient”, telefon albo zapis
    rozmowy; koniec = ostatnia taka chwila. Wylogowanie najwyżej LOGOUT_GRACE_MINUTES po ostatniej pracy
    przesuwa koniec na wylogowanie (dopisywał notatki), późniejsze się nie liczy (zostawił otwarty CRM).
    Dziś bez wylogowania: „do teraz”, dopóki ostatnia praca była najwyżej IDLE_MINUTES temu.
    Każdy dzień kończy się najpóźniej o północy: nic nie przechodzi na następny dzień.
    """
    from .models import WorkDay

    start, end = day_bounds(day)
    marks = list(Activity.objects.filter(agent=user, created_at__gte=start, created_at__lt=end)
                 .values_list("created_at", flat=True))
    marks += list(CallRequest.objects.filter(user=user, created_at__gte=start, created_at__lt=end)
                  .values_list("created_at", flat=True))
    wd = WorkDay.objects.filter(user=user, date=day).first()
    if wd:
        marks += [t for t in (wd.first_work, wd.last_work) if t]
    if not marks:
        return None, None, False
    began, last = min(marks), max(marks)
    if wd and wd.ended_at and wd.ended_at >= last:
        quit_at = wd.ended_at if wd.ended_at - last <= timedelta(minutes=LOGOUT_GRACE_MINUTES) else last
        return began, quit_at, True
    now = timezone.now()
    # Dziś i bez wylogowania: liczymy do teraz, chyba że agent od dłuższej chwili nic nie robi
    # (wyłączył laptopa) – wtedy czas pracy kończy się na ostatniej pracy. Gdy wróci, przerwa się doliczy.
    if day == timezone.localdate() and now - last <= timedelta(minutes=IDLE_MINUTES):
        return began, now, False
    return began, last, False


def day_minutes(user, day):
    """Minuty rozmów jednego dnia i ile powinno ich być: cel agenta (domyślnie 15 min) na każdą godzinę pracy.

    Pracuje 4 h → powinien mieć 60 min rozmów (3 h 20 min → 50 min). Liczy się wynik dnia,
    więc godzina z 30 minutami nadrabia słabszą godzinę obok.
    """
    by_hour = minutes_by_hour(user, day)
    minutes = round(sum(h["minutes"] for h in by_hour), 2)
    began, ended, finished = work_span(user, day)
    worked = int((ended - began).total_seconds() // 60) if began else 0
    from .models import WorkDay

    wd = WorkDay.objects.filter(user=user, date=day).first()
    breaks = list(wd.breaks or []) if wd else []
    # Krótka przerwa (15–45 min: kawa, toaleta, dłuższa notatka) liczy się do pracy – tylko ją pokazujemy.
    # Długa (od IDLE_MINUTES, np. 3 h w mieście) to czas POZA pracą: odejmujemy go, więc dzień
    # 3 h pracy + 3 h wyjścia + 3 h pracy = 6 h pracy i cel za 6 h. Działa tak samo przy kilku wyjściach.
    long_breaks = sorted(b for b in breaks if _break_minutes([b]) >= IDLE_MINUTES) if began else []
    away = sum(_break_minutes([b]) for b in long_breaks)
    worked = max(0, worked - away)
    # Bloki pracy (rozdzielone wyjściami od 45 min), np. 9:00–12:00, 15:00–18:00, 21:00–teraz.
    # Koniec None = blok trwa (dziś, bez wylogowania, agent właśnie pracuje).
    blocks, live = [], False
    if began:
        piece = began
        for a, b in long_breaks:
            a, b = datetime.fromisoformat(a), datetime.fromisoformat(b)
            if began <= a and b <= ended:
                blocks.append([piece.isoformat(), a.isoformat()])
                piece = b
        live = not finished and day == timezone.localdate() and ended >= timezone.now() - timedelta(seconds=5)
        blocks.append([piece.isoformat(), None if live else ended.isoformat()])
    now = timezone.now()
    # trwająca przerwa: dziś, bez wylogowania, od co najmniej 15 min nic się nie dzieje
    on_break_since = None
    # ostatnia praca: zapisana przy pracy (touch_workday(work=True)); gdy dzień już „stanął” (ponad 45 min ciszy),
    # to koniec dnia z work_span (obejmuje też rozmowy zapisane bez WorkDay, np. z danych demo)
    last_work = wd.last_work if wd else None
    if began and not live:
        last_work = max(t for t in (last_work, ended) if t)
    if began and not finished and day == timezone.localdate() and last_work and now - last_work >= timedelta(minutes=BREAK_MINUTES):
        on_break_since = last_work.isoformat()
    return {
        "date": day.isoformat(), "minutes": minutes,
        "worked_minutes": worked, "hours": round(worked / 60, 2),
        "goal": round(worked * goal_per_hour(user) / 60), "goal_minutes_per_hour": goal_per_hour(user),
        "started_at": began.isoformat() if began else None,
        "login_at": wd.first_seen.isoformat() if wd else None,   # pierwsze zalogowanie / otwarcie CRM tego dnia
        "ended_at": ended.isoformat() if finished else None,
        "last_seen": ended.isoformat() if began else None,
        "breaks": breaks, "break_minutes": _break_minutes(breaks), "away_minutes": away,
        "blocks": blocks, "on_break_since": on_break_since,
        "by_hour": by_hour,
    }


def attendance(day):
    """Obecność agentów w danym dniu (dla managera): przyjście, wyjście, czas pracy, minuty i cel."""
    from django.contrib.auth import get_user_model

    from .roles import AGENT

    rows = []
    for user in get_user_model().objects.filter(is_active=True, groups__name=AGENT).order_by("username"):
        d = day_minutes(user, day)
        acts = Activity.objects.filter(agent=user, created_at__gte=day_bounds(day)[0], created_at__lt=day_bounds(day)[1])
        rows.append({
            "id": user.pk, "username": user.username, "full_name": user.get_full_name() or user.username,
            "started_at": d["started_at"], "ended_at": d["ended_at"], "worked_minutes": d["worked_minutes"],
            "login_at": d["login_at"],
            "working_now": bool(d["started_at"]) and not d["ended_at"] and not d["on_break_since"]
            and day == timezone.localdate(),
            "last_seen": d["last_seen"],
            "breaks": d["breaks"], "break_minutes": d["break_minutes"], "away_minutes": d["away_minutes"], "blocks": d["blocks"], "on_break_since": d["on_break_since"],
            "minutes": d["minutes"], "goal": d["goal"], "goal_minutes_per_hour": d["goal_minutes_per_hour"],
            "calls": acts.exclude(outcome__in=NON_CALL_OUTCOMES).count(),
        })
    return rows


def minutes_report(user, date_from, date_to):
    """Minuty rozmów w okresie: dzień po dniu (dla jednego dnia także godzina po godzinie)."""
    days = []
    day = date_from
    while day <= date_to:
        days.append(day_minutes(user, day))
        day += timedelta(days=1)
    total = round(sum(d["minutes"] for d in days), 2)
    goal = sum(d["goal"] for d in days)
    worked = sum(d["worked_minutes"] for d in days)
    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "minutes": total, "goal": goal, "worked_minutes": worked, "hours": round(worked / 60, 2),
        "goal_minutes_per_hour": goal_per_hour(user),
        "days": [{k: v for k, v in d.items() if k not in ("by_hour", "goal_minutes_per_hour")} for d in days],
        "by_hour": days[0]["by_hour"] if len(days) == 1 else None,
    }


def team_minutes_report(users, date_from, date_to):
    """Jak `minutes_report`, ale dla kilku osób naraz (manager → Wyniki → „Cały zespół”).

    Minuty, czas pracy i „powinno być” to sumy wszystkich osób; `per_user` ma te liczby
    osobno dla każdej osoby (tabela „Wyniki agentów”). `day_minutes` liczymy tylko dla dni,
    w których ktoś cokolwiek robił – reszta to zera, więc długi okres nie obciąża serwera.
    """
    from .models import WorkDay

    ids = [u.pk for u in users]
    since, _ = day_bounds(date_from)
    _, until = day_bounds(date_to)
    local = lambda t: timezone.localtime(t).date()  # noqa: E731
    pairs = {(uid, d) for uid, d in WorkDay.objects.filter(user__in=ids, date__gte=date_from, date__lte=date_to)
             .values_list("user", "date")}
    pairs |= {(uid, local(t)) for uid, t in Activity.objects.filter(agent__in=ids, created_at__gte=since, created_at__lt=until)
              .values_list("agent", "created_at")}
    pairs |= {(uid, local(t)) for uid, t in CallRequest.objects.filter(user__in=ids, created_at__gte=since, created_at__lt=until)
              .values_list("user", "created_at")}
    by_user = {u.pk: u for u in users}
    days, per_user = {}, {uid: {"minutes": 0.0, "goal": 0, "worked_minutes": 0} for uid in ids}
    single = date_from == date_to
    by_hour = [0.0] * 24
    for uid, d in sorted(pairs, key=lambda p: p[1]):
        if not (date_from <= d <= date_to):
            continue
        dm = day_minutes(by_user[uid], d)
        row = days.setdefault(d, {"minutes": 0.0, "goal": 0, "worked_minutes": 0})
        for key in ("minutes", "goal", "worked_minutes"):
            row[key] += dm[key]
            per_user[uid][key] += dm[key]
        if single:
            for h in dm["by_hour"]:
                by_hour[h["hour"]] += h["minutes"]
    day_rows, day = [], date_from
    while day <= date_to:
        r = days.get(day, {"minutes": 0.0, "goal": 0, "worked_minutes": 0})
        day_rows.append({"date": day.isoformat(), "minutes": round(r["minutes"], 2), "goal": r["goal"],
                         "worked_minutes": r["worked_minutes"], "hours": round(r["worked_minutes"] / 60, 2)})
        day += timedelta(days=1)
    worked = sum(r["worked_minutes"] for r in day_rows)
    for v in per_user.values():
        v["minutes"] = round(v["minutes"], 2)
    return {
        "scope": "team", "people": len(ids),
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "minutes": round(sum(r["minutes"] for r in day_rows), 2), "goal": sum(r["goal"] for r in day_rows),
        "worked_minutes": worked, "hours": round(worked / 60, 2),
        "goal_minutes_per_hour": team_goal_per_hour(),
        "started_at": None, "ended_at": None,
        "days": day_rows,
        "by_hour": [{"hour": h, "minutes": round(m, 2)} for h, m in enumerate(by_hour)] if single else None,
        "per_user": per_user,
    }


def today_stats(user):
    """Liczby do panelu „Dziś”: rozmowy, dodzwonienia, oferty, sprzedaż, prowizja i minuty rozmów."""
    start, end = day_bounds(timezone.localdate())
    acts = Activity.objects.filter(agent=user, created_at__gte=start, created_at__lt=end)
    calls = acts.exclude(outcome__in=NON_CALL_OUTCOMES)
    sales = SaleItem.objects.filter(activity__in=acts.filter(outcome=Activity.Outcome.SOLD))
    by_hour = minutes_by_hour(user)
    hour_now = timezone.localtime().hour
    dialer_used = CallRequest.objects.filter(user=user, created_at__gte=start).exists()
    return {
        "calls": calls.count(),
        "talks": calls.exclude(outcome=Activity.Outcome.NO_ANSWER).count(),
        "offers": acts.filter(outcome=Activity.Outcome.OFFER).count(),
        "sold": acts.filter(outcome=Activity.Outcome.SOLD).count(),
        "commission": sales.aggregate(s=Sum("commission"))["s"] or Decimal("0.00"),
        "minutes_today": round(sum(h["minutes"] for h in by_hour), 2),
        "minutes_this_hour": by_hour[hour_now]["minutes"],
        "minutes_by_hour": by_hour,
        "goal_minutes_per_hour": goal_per_hour(user),
        "dialer_used": dialer_used,
        "day_goal": day_minutes(user, timezone.localdate())["goal"],
    }


# ------------------------------------------------------------------ oferty

def _money(value):
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " zł"


def offer_numbers(product, quantity, term, promo, config=None, option=0):
    """(zobowiązanie miesięczne, prowizja) dla wybranej taryfy (option = która promocja z listy)."""
    config = config or OfferConfig.get()
    promo = bool(promo) and product.promo_choice(option) is not None
    monthly = product.monthly_total(quantity, promo, option)
    return monthly, (monthly * config.multiplier(term)).quantize(Decimal("0.01"))


def promo_terms(config, product, option=0):
    """(nazwa, miesiące, cena za sztukę w tych miesiącach) wybranej promocji; None – taryfa nie ma promocji.

    Puste pola taryfy = ustawienia z „Ustawienia i oferty” („6 miesięcy gratis”: 6 miesięcy po 0 zł).
    Światłowód ma kilka promocji (np. 12 miesięcy po 50 zł albo 6 miesięcy po 0 zł) – `option` wybiera jedną.
    """
    choice = product.promo_choice(option, config.promo_label)
    if choice is None:
        return None
    return choice["label"], choice["months"], choice["month_price"]


def line_numbers(config, line):
    """Wyliczenie jednej pozycji oferty: line = {"product", "quantity", "term", "promo", "option"}."""
    product, quantity, term = line["product"], max(int(line.get("quantity") or 1), 1), int(line["term"])
    option = int(line.get("option") or 0)
    promo = bool(line.get("promo")) and product.promo_choice(option) is not None   # TV nie ma promocji
    monthly, commission = offer_numbers(product, quantity, term, promo, config, option)
    label, months, first, average = "", 0, None, None
    if promo:
        label, months, month_price = promo_terms(config, product, option)
        if 0 < months < term:
            first = (month_price * quantity).quantize(Decimal("0.01"))
            average = ((first * months + monthly * (term - months)) / term).quantize(Decimal("0.01"))
    return {"product": product, "quantity": quantity, "term": term, "promo": promo, "option": option if promo else 0,
            "label": label, "months": months, "first": first, "monthly": monthly, "commission": commission,
            "average": average}


def _per_number(product):
    """Taryfa komórkowa (dopłata za każdy kolejny numer) – w ofercie piszemy o numerach.
    Światłowód i telewizja mają dopłatę 0: tam jest „Ilość” i „Opłata miesięczna”."""
    return bool(product.extra_price)


def _numbers_word(quantity):
    if quantity == 1:
        return "1 numer"
    if 2 <= quantity % 10 <= 4 and not 12 <= quantity % 100 <= 14:
        return f"{quantity} numery"
    return f"{quantity} numerów"


def _term_words(term):
    return "24 miesiące" if int(term) == 24 else f"{term} miesięcy"


def offer_total(numbers):
    """Suma zestawu: (miesięcznie po promocjach, średnio w okresie umowy albo None, prowizja)."""
    monthly = sum((n["monthly"] for n in numbers), Decimal("0"))
    commission = sum((n["commission"] for n in numbers), Decimal("0"))
    average = None
    if any(n["average"] is not None for n in numbers):
        average = sum((n["average"] if n["average"] is not None else n["monthly"] for n in numbers), Decimal("0"))
    return monthly, average, commission


def offer_title(numbers):
    """Krótki opis zestawu do historii: „M ×3 + Światłowód L – blok” (max 120 znaków)."""
    text = " + ".join(n["product"].name + (f" ×{n['quantity']}" if n["quantity"] > 1 else "") for n in numbers)
    return text if len(text) <= 120 else text[:119] + "…"


def _line_text(n):
    product, quantity, term = n["product"], n["quantity"], n["term"]
    per_number = _per_number(product)
    lines = [f"OFERTA: {product.name}",
             f"Liczba numerów: {quantity}" if per_number else f"Ilość: {quantity}",
             f"Okres umowy: {term} miesięcy"]
    if n["promo"] and n["label"]:
        lines.append(f"Promocja: {n['label']}")
    if per_number:
        lines.append(f"Całkowite zobowiązanie miesięczne za {_numbers_word(quantity)}: {_money(n['monthly'])} netto")
        if quantity > 1:
            lines.append(f"Cena za jeden numer: {_money(n['monthly'] / quantity)} netto")
    else:
        lines.append(f"Opłata miesięczna: {_money(n['monthly'])} netto")
    if n["first"]:   # promocja z opłatą (np. światłowód 12 miesięcy po 50 zł)
        lines.append(f"Przez pierwsze {n['months']} miesięcy: {_money(n['first'])} netto miesięcznie, "
                     f"od {n['months'] + 1}. miesiąca: {_money(n['monthly'])} netto")
    if n["average"] is not None:
        lines.append(f"Średni miesięczny koszt w okresie {term} miesięcy: {_money(n['average'])} netto")
    features = [f.strip() for f in product.features.splitlines() if f.strip()]
    if features:
        lines += ["", "W ofercie:"] + [f"• {f}" for f in features]
    return lines


def build_offer(config, lead, agent, lines, client_email):
    """Temat i treść oferty (jedna albo kilka usług). W trybie „forward” na górze jest notka dla osoby, która ją przekaże."""
    numbers = [line_numbers(config, line) for line in lines]
    company = lead.display_name
    text = [config.greeting.strip(), ""]
    for index, n in enumerate(numbers):
        if index:
            text += ["", "-" * 40, ""]
        text += _line_text(n)
    if len(numbers) > 1:
        monthly, average, _ = offer_total(numbers)
        text += ["", "=" * 40, f"RAZEM miesięcznie za wszystkie usługi: {_money(monthly)} netto"]
        if average is not None:
            text.append(f"Średni miesięczny koszt w okresie umowy: {_money(average)} netto")
    agent_name = agent.get_full_name() or agent.username
    text += ["", config.closing.strip(), "", "Z poważaniem,", agent_name]
    body = "\n".join(text).strip() + "\n"

    if config.mode == OfferConfig.Mode.FORWARD:
        company_line = company + (f", NIP {lead.company.nip}" if lead.company and lead.company.nip else "")
        note = (
            # Czarek robi to codziennie – same dane, bez formułek
            f"Adres klienta: {client_email or '(brak – trzeba dopytać)'}\n"
            f"Firma: {company_line}\nTelefon: {lead.phone}\nAgent: {agent_name}\n"
            f"{'-' * 40}\n\n"
        )
        return f"Oferta do wysłania: {company} → {client_email or 'brak e-maila'}", note + body
    return f"Oferta T-Mobile dla {company}", body


OFFER_MAGENTA = "#E20074"


def _line_html(n):
    product, quantity, term = n["product"], n["quantity"], n["term"]
    p = 'style="margin:0 0 14px"'
    bold = 'style="margin:0 0 12px;font-weight:700"'
    parts = [
        f'<div style="color:{OFFER_MAGENTA};font-size:20px;font-weight:800;margin:18px 0 12px">'
        f"Oferta: {escape(product.name)}</div>",
        f"<p {p}><strong>Okres trwania umowy:</strong> {_term_words(term)}</p>",
    ]
    if n["promo"] and n["label"]:
        parts.append(f'<p style="margin:0 0 12px;font-weight:800;color:{OFFER_MAGENTA}">Promocja: {escape(n["label"])}</p>')
    if _per_number(product):
        parts.append(f"<p {bold}>Całkowite zobowiązanie miesięczne za {_numbers_word(quantity)}: {_money(n['monthly'])} netto</p>")
        if quantity > 1:
            parts.append(f"<p {bold}>Cena za jeden numer: {_money(n['monthly'] / quantity)} netto</p>")
    else:
        parts.append(f"<p {bold}>Opłata miesięczna{f' za {quantity} szt.' if quantity > 1 else ''}: {_money(n['monthly'])} netto</p>")
    if n["first"]:
        parts.append(f"<p {bold}>Przez pierwsze {n['months']} miesięcy: {_money(n['first'])} netto miesięcznie, "
                     f"od {n['months'] + 1}. miesiąca: {_money(n['monthly'])} netto</p>")
    if n["average"] is not None:
        parts.append(f"<p {bold}>Średni miesięczny koszt w okresie {term} miesięcy: {_money(n['average'])} netto</p>")
    items = []
    for feature in (f.strip() for f in product.features.splitlines()):
        if not feature:
            continue
        head, sep, rest = feature.partition(":")
        text = f"<strong>{escape(head)}:</strong>{escape(rest)}" if sep and rest.strip() else escape(feature)
        items.append(f'<li style="margin:5px 0">{text}</li>')
    if items:
        parts.append(f'<ul style="margin:8px 0 18px;padding-left:22px">{"".join(items)}</ul>')
    return parts


def offer_letter_html(config, lines):
    """List do klienta w układzie wzornika ofert (sam fragment, style inline: działa w każdej poczcie).
    Kilka usług = osobna sekcja dla każdej i suma na końcu (jak „Oferta łączona” we wzorniku)."""
    numbers = [line_numbers(config, line) for line in lines]
    p = 'style="margin:0 0 14px"'
    greeting = [escape(x) for x in config.greeting.strip().split("\n\n")] if config.greeting.strip() else []
    parts = [f"<p {p}>{line.replace(chr(10), '<br>')}</p>" for line in greeting]
    for n in numbers:
        parts += _line_html(n)
    if len(numbers) > 1:
        monthly, average, _ = offer_total(numbers)
        rows = f"Razem miesięcznie za wszystkie usługi: {_money(monthly)} netto"
        if average is not None:
            rows += f"<br>Średni miesięczny koszt w okresie umowy: {_money(average)} netto"
        parts.append(
            f'<div style="border:2px solid {OFFER_MAGENTA};border-radius:10px;padding:12px 16px;margin:6px 0 18px;'
            f'font-weight:800;font-size:16px">{rows}</div>'
        )
    if config.closing.strip():
        parts.append(f"<p {p}>{escape(config.closing.strip())}</p>")
    if config.contact_phone:
        parts.append(f"<p {p}><strong>Tel.:</strong> {escape(config.contact_phone)}</p>")
    parts.append(f'<p style="margin:22px 0 0">Z poważaniem,<br>{escape(config.signature or "")}</p>')
    # Własne białe tło: w ciemnej poczcie (np. czarny Thunderbird, tryb ciemny u klienta) ciemny tekst
    # bez tła byłby nieczytelny. Tabela z bgcolor to sposób, który zachowuje każda poczta (sam styl
    # na <div> bywa gubiony). Z tłem oferta wygląda wszędzie tak samo – jak biała kartka.
    return (
        '<table role="presentation" data-offer-card="1" width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff"'
        ' style="background:#ffffff;border-collapse:collapse;max-width:760px"><tr>'
        '<td bgcolor="#ffffff" style="background:#ffffff;padding:20px 24px">'
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#25212a;'
        'line-height:1.65;font-size:15px;max-width:720px">'
        + "".join(parts) + "</div></td></tr></table>"
    )


def offer_document(letter_html, title):
    """Pełny plik HTML (załącznik): otwiera się w przeglądarce, da się wydrukować do PDF."""
    return (
        '<!DOCTYPE html><html lang="pl"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f"<title>{escape(title)}</title></head>"
        '<body style="margin:0;padding:28px 16px;background:#ffffff">'
        f"{letter_html}</body></html>"
    )


def forward_note_html(lead, agent, client_email):
    """Ramka na górze maila do osoby wysyłającej: komu przekazać ofertę."""
    company = escape(lead.display_name)
    nip = f", NIP {escape(lead.company.nip)}" if lead.company and lead.company.nip else ""
    email = escape(client_email) if client_email else "<b>brak – trzeba dopytać klienta</b>"
    agent_name = escape(agent.get_full_name() or agent.username)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;border:1px solid #d6dbe6;'
        'border-left:5px solid #2456d6;background:#f3f6fc;border-radius:8px;padding:12px 16px;margin:0 0 22px;max-width:720px">'
        f"<b>Adres klienta:</b> {email}<br>Firma: {company}{nip}<br>Telefon: {escape(lead.phone)}<br>Agent: {agent_name}</div>"
    )


def offer_filename(lead):
    base = slugify(lead.display_name)[:50] or f"klient-{lead.pk}"
    return f"oferta-{base}.html"


class OfferError(Exception):
    pass


def create_offer(lead, agent, lines, client_email, next_contact_at, next_contact_note=None, note=""):
    """Zapisuje rozmowę „Wysłana oferta” z terminem follow-upu, ofertę (jedna albo kilka usług)
    i (jeśli się da) wysyła e-mail. lines = [{"product", "quantity", "term", "promo"}, ...].

    Zwraca (offer, mailto) – mailto to gotowa wiadomość do otwarcia w programie pocztowym,
    gdy system nie wysłał jej sam (brak serwera poczty).
    """
    config = OfferConfig.get()
    if not lines:
        raise OfferError("Wybierz co najmniej jedną taryfę.")
    if config.mode == OfferConfig.Mode.DIRECT and not client_email:
        raise OfferError("Podaj e-mail klienta: oferta idzie od razu do niego.")
    numbers = [line_numbers(config, line) for line in lines]
    monthly, _, commission = offer_total(numbers)
    first = numbers[0]
    title = offer_title(numbers)
    subject, body = build_offer(config, lead, agent, lines, client_email)
    recipient = client_email if config.mode == OfferConfig.Mode.DIRECT else config.forward_email
    letter = offer_letter_html(config, lines)
    stored = [{"product": n["product"].pk, "product_name": n["product"].name, "quantity": n["quantity"],
               "term_months": n["term"], "promo": n["promo"], "option": n["option"], "promo_label": n["label"],
               "monthly": str(n["monthly"])} for n in numbers]
    default_note = " + ".join(f"{n['product'].name} ×{n['quantity']}, {n['term']} mies." for n in numbers)
    with transaction.atomic():
        if client_email and client_email != lead.email:
            Lead.objects.filter(pk=lead.pk).update(email=client_email)
        activity = register_activity(
            lead, agent, Activity.Outcome.OFFER, note or f"Oferta {default_note}",
            next_contact_at=next_contact_at, next_contact_note=next_contact_note,
        )
        offer = Offer.objects.create(
            lead=lead, agent=agent, activity=activity, product=first["product"],
            product_name=title if len(numbers) > 1 else first["product"].name,
            quantity=first["quantity"], term_months=first["term"], promo=any(n["promo"] for n in numbers),
            monthly_total=monthly, commission=commission, lines=stored,
            client_email=client_email, recipient=recipient or "", subject=subject, body=body, html=letter,
        )
    if recipient and getattr(settings, "OFFER_EMAIL_ENABLED", False):
        # Mail = sformatowana oferta (w trybie „forward” z ramką „komu przekazać” na górze)
        # + załącznik oferta-….html z samym listem, gotowy do przesłania klientowi.
        top = forward_note_html(lead, agent, client_email) if config.mode == OfferConfig.Mode.FORWARD else ""
        message = EmailMultiAlternatives(subject, body, to=[recipient], reply_to=[agent.email] if agent.email else None)
        message.attach_alternative(offer_document(top + letter, subject), "text/html")
        message.attach(offer_filename(lead), offer_document(letter, f"Oferta: {title}"), "text/html")
        try:
            message.send()
            Offer.objects.filter(pk=offer.pk).update(sent=True)
            offer.sent = True
        except Exception:  # noqa: BLE001 – brak poczty nie może zgubić zapisanej oferty
            pass
    mailto = f"mailto:{quote(recipient or '')}?subject={quote(subject)}&body={quote(body)}"
    return offer, mailto


# ------------------------------------------------------------------ dialer (aplikacja na telefonie)

PAIRING_MINUTES = 10
DEVICE_ONLINE_SECONDS = 40
CALL_PICKUP_SECONDS = 120


def create_pairing(user):
    """Nowy jednorazowy kod parowania telefonu (6 cyfr, ważny PAIRING_MINUTES minut)."""
    DialerPairing.objects.filter(user=user, used=False).delete()
    while True:
        code = f"{secrets.randbelow(10 ** 6):06d}"
        if not DialerPairing.objects.filter(code=code).exists():
            return DialerPairing.objects.create(user=user, code=code)


def pair_device(code, name=""):
    """Telefon wpisuje (albo skanuje) kod: dostaje własny token do API dialera."""
    limit = timezone.now() - timedelta(minutes=PAIRING_MINUTES)
    with transaction.atomic():
        pairing = DialerPairing.objects.select_for_update().filter(code=code, used=False, created_at__gte=limit).first()
        if pairing is None:
            return None
        pairing.used = True
        pairing.save(update_fields=["used"])
        return DialerDevice.objects.create(
            user=pairing.user, name=name[:80], token=secrets.token_hex(32), last_seen=timezone.now()
        )


def request_call(user, phone, lead=None):
    """CRM prosi telefon o wybranie numeru (poprzednie niewybrane prośby są anulowane)."""
    CallRequest.objects.filter(user=user, state=CallRequest.State.PENDING).update(state=CallRequest.State.CANCELLED)
    return CallRequest.objects.create(user=user, lead=lead, phone=phone)


def poll_call(device):
    """Telefon pyta, czy ma coś wybrać; prośba przechodzi w stan „dzwoni”."""
    now = timezone.now()
    DialerDevice.objects.filter(pk=device.pk).update(last_seen=now)
    with transaction.atomic():
        call = (
            CallRequest.objects.select_for_update()
            .filter(user=device.user, state=CallRequest.State.PENDING, created_at__gte=now - timedelta(seconds=CALL_PICKUP_SECONDS))
            .order_by("created_at").first()
        )
        if call:
            call.state, call.started_at = CallRequest.State.DIALING, now
            call.save(update_fields=["state", "started_at"])
    return call


def finish_call(device, call_id, answered, duration_seconds):
    """Telefon odsyła wynik z rejestru połączeń: czy klient odebrał i ile trwała rozmowa."""
    call = CallRequest.objects.filter(pk=call_id, user=device.user).first()
    if call is None:
        return None
    call.state = CallRequest.State.DONE
    call.answered = bool(answered)
    call.duration_seconds = max(int(duration_seconds or 0), 0) if answered else 0
    call.ended_at = timezone.now()
    call.save(update_fields=["state", "answered", "duration_seconds", "ended_at"])
    DialerDevice.objects.filter(pk=device.pk).update(last_seen=call.ended_at)
    # wynik rozmowy zapisany już wcześniej (agent był szybszy niż telefon) dostaje czas od razu
    act = Activity.objects.filter(
        lead_id=call.lead_id, agent=device.user, call_seconds__isnull=True, created_at__gte=call.created_at
    ).exclude(outcome__in=NON_CALL_OUTCOMES).order_by("created_at").first()
    if act and call.lead_id:
        Activity.objects.filter(pk=act.pk).update(call_seconds=call.duration_seconds)
    return call


def attach_call_time(activity):
    """Nowy wpis rozmowy dostaje czas ostatniego zakończonego połączenia z dialera do tego leada."""
    if activity.outcome in NON_CALL_OUTCOMES:
        return
    call = CallRequest.objects.filter(
        user=activity.agent, lead=activity.lead, state=CallRequest.State.DONE,
        ended_at__gte=timezone.now() - timedelta(minutes=30),
    ).order_by("-ended_at").first()
    if call and not Activity.objects.filter(lead=activity.lead, call_seconds__isnull=False, created_at__gte=call.ended_at).exists():
        Activity.objects.filter(pk=activity.pk).update(call_seconds=call.duration_seconds)


def dialer_status(user):
    """Czy telefon agenta jest połączony i co się dzieje z ostatnim połączeniem."""
    now = timezone.now()
    device = DialerDevice.objects.filter(user=user).order_by("-last_seen").first()
    online = bool(device and device.last_seen and (now - device.last_seen).total_seconds() <= DEVICE_ONLINE_SECONDS)
    call = CallRequest.objects.filter(user=user, created_at__gte=now - timedelta(minutes=15)).order_by("-created_at").first()
    return {
        "paired": device is not None,
        "online": online,
        "device_name": device.name if device else "",
        # nagrywanie rozmów: włączone przez managera i czy telefon agenta umie nagrywać (None = stara aplikacja)
        "recording": RecordingConfig.get().active,
        "can_record": device.can_record if device else None,
        "call": None if call is None else {
            "id": call.pk, "lead": call.lead_id, "phone": call.phone, "state": call.state,
            "answered": call.answered, "duration_seconds": call.duration_seconds,
        },
    }


def team_minutes_today(users):
    """Minuty rozmów dziś dla każdego agenta (ranking „Minuty”)."""
    start, end = day_bounds(timezone.localdate())
    rows = (
        CallRequest.objects.filter(user__in=users, state=CallRequest.State.DONE, answered=True, ended_at__gte=start, ended_at__lt=end)
        .values("user").annotate(s=Sum("duration_seconds"))
    )
    return {r["user"]: round((r["s"] or 0) / 60, 2) for r in rows}
