"""Raporty dla managera: gdzie uciekają klienci, kiedy firmy odbierają, dlaczego odmawiają,
które oferty czekają bez odpowiedzi i kto wróci, gdy skończy mu się umowa.

Wszystko w jednym słowniku (`build_reports`), żeby strona „Raporty” potrzebowała jednego zapytania.
"""

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay
from django.utils import timezone

from .models import NON_CALL_OUTCOMES, Activity, Lead, Offer, porting_from
from .services import ACTIVE_STATUSES, agent_stats, day_bounds, team_users
from .phrases import effective_phrases
from .rules import get_rules

Outcome = Activity.Outcome

# Ile rozmów musi mieć osoba, żeby jej wynik porównywać z zespołem (mniej = przypadek)
MIN_CALLS_TO_COMPARE = 20
# Wynik niższy niż tyle × średnia zespołu = słaby krok
WEAK_FACTOR = 0.8
# Oferta bez odpowiedzi: tyle dni od wysłania bez sprzedaży ani odmowy
OFFER_WAIT_DAYS = 2
OFFERS_LIST_MAX = 50
CONTRACT_MONTHS_AHEAD = 12
HOURS = range(8, 19)   # 8:00–18:59

# Powody odmowy: te same przyciski, które agent klika przy „Odmowa” (queue.js → REFUSE_CHIPS)
REFUSAL_REASONS = (
    "Ma umowę w innej sieci",
    "Za drogo",
    "Nie jest zainteresowany",
    "Nie decyduje o tym",
    "Prosi o brak kontaktu",
)

# Kroki lejka: klucz, nazwa i co poradzić, gdy osoba wypada tu słabiej niż zespół
STEPS = (
    ("answer_rate", "Dodzwonienia",
     "Mało odebranych telefonów. Sprawdź w „Kiedy firmy odbierają”, o której dzwonić, i czy agent robi kolejne próby o innych porach."),
    ("offer_rate", "Rozmowa → oferta",
     "Klienci odbierają, ale rzadko chcą ofertę. Przećwiczcie otwarcie i odpowiedzi na obiekcje, odsłuchajcie kilka nagrań."),
    ("close_rate", "Oferta → sprzedaż",
     "Oferty nie zamieniają się w umowy. Pilnujcie follow-upów (lista „Oferty bez odpowiedzi” niżej) i dzwońcie w umówionym terminie."),
)


def _ratio(part, whole):
    return round(part / whole, 4) if whole else None


def _funnel_row(name, s):
    calls, answered, offers, sold = s["calls"], s["answered"], s["offers"], s["sold"]
    return {
        "agent": s.get("agent"),
        "name": name,
        "calls": calls,
        "answered": answered,
        "offers": offers,
        "sold": sold,
        "commission": s.get("commission") or Decimal("0.00"),
        "answer_rate": _ratio(answered, calls),
        "offer_rate": _ratio(offers, answered),
        "close_rate": _ratio(sold, offers),
        "sale_rate": _ratio(sold, calls),
    }


def funnel(since, until):
    """Lejek każdej osoby i całego zespołu + najsłabszy krok osoby na tle zespołu (z podpowiedzią)."""
    users = team_users(since, until)
    stats = agent_stats(users, since, until)
    names = {u.pk: (u.get_full_name() or u.username) for u in users}
    rows = [_funnel_row(names[s["agent"]], s) for s in stats if s["calls"]]
    total = {k: sum(r[k] for r in rows) for k in ("calls", "answered", "offers", "sold")}
    total["commission"] = sum((r["commission"] for r in rows), Decimal("0.00"))
    team = _funnel_row("Cały zespół", {**total, "agent": None})
    for r in rows:
        r["weak_step"], r["tip"] = None, ""
        if r["calls"] < MIN_CALLS_TO_COMPARE:
            continue
        # pierwszy słaby krok lejka: dalsze kroki i tak zależą od niego (bez ofert nie ma sprzedaży)
        for key, label, tip in STEPS:
            mine, avg = r[key], team[key]
            if mine is not None and avg and mine < avg * WEAK_FACTOR:
                r["weak_step"], r["weak_label"], r["tip"] = key, label, tip
                break
    rows.sort(key=lambda r: (-r["sold"], -(r["sale_rate"] or 0), -r["calls"]))
    return {"rows": rows, "team": team, "min_calls": MIN_CALLS_TO_COMPARE}


def answer_hours(since, until):
    """Ile telefonów odebrano o każdej godzinie i w każdy dzień tygodnia (cały zespół)."""
    calls = Activity.objects.filter(created_at__gte=since, created_at__lt=until).exclude(outcome__in=NON_CALL_OUTCOMES)
    tz = timezone.get_current_timezone()

    def grouped(field, expr):
        rows = (calls.annotate(**{field: expr}).order_by().values(field)
                .annotate(calls=Count("id"), answered=Count("id", filter=~Q(outcome=Outcome.NO_ANSWER))))
        return {r[field]: r for r in rows}

    by_hour = grouped("hour", ExtractHour("created_at", tzinfo=tz))
    by_day = grouped("weekday", ExtractIsoWeekDay("created_at", tzinfo=tz))
    hours = [{"hour": h, "calls": by_hour.get(h, {}).get("calls", 0), "answered": by_hour.get(h, {}).get("answered", 0)}
             for h in HOURS]
    days = [{"weekday": d, "calls": by_day.get(d, {}).get("calls", 0), "answered": by_day.get(d, {}).get("answered", 0)}
            for d in range(1, 8)]
    for row in hours + days:
        row["rate"] = _ratio(row["answered"], row["calls"])
    # najlepsze godziny: tylko te, w których było dość telefonów, żeby wynik coś znaczył
    # (co najmniej 10 telefonów i 4% wszystkich – inaczej jedna szczęśliwa godzina wygrywa przypadkiem)
    floor = max(10, sum(h["calls"] for h in hours) * 0.04)
    for h in hours:
        h["few"] = 0 < h["calls"] < floor
    enough = [h for h in hours if h["calls"] >= floor]
    best = sorted(enough, key=lambda h: -(h["rate"] or 0))[:3]
    return {"hours": hours, "weekdays": days, "best_hours": sorted(h["hour"] for h in best)}


def refusals(since, until):
    """Dlaczego klienci odmawiają: powody z przycisków przy „Odmowa” (komentarz rozmowy)."""
    notes = Activity.objects.filter(created_at__gte=since, created_at__lt=until, outcome=Outcome.REFUSED).values_list("note", flat=True)
    counts, total = Counter(), 0
    # powody z ustawień managera (dopisane przez niego) + domyślne, żeby stare rozmowy też się liczyły
    reasons = list(dict.fromkeys(effective_phrases(get_rules())["refused"] + list(REFUSAL_REASONS)))
    for note in notes:
        total += 1
        text = (note or "").lower()
        found = [r for r in reasons if r.lower() in text]
        if found:
            counts.update(found)
        else:
            counts["Inny powód (opisany w komentarzu)" if text.strip() else "Bez podanego powodu"] += 1
    rows = [{"reason": k, "count": v} for k, v in counts.most_common()]
    return {"total": total, "rows": rows}


def offers_waiting(now=None):
    """Klienci z wysłaną ofertą, którzy od OFFER_WAIT_DAYS dni nie kupili ani nie odmówili."""
    now = now or timezone.now()
    last_offer = Offer.objects.filter(lead=OuterRef("pk")).order_by("-created_at")
    leads = (
        Lead.objects.filter(status=Lead.Status.OFFER)
        .annotate(offer_at=Max("offers__created_at"),
                  offer_monthly=Subquery(last_offer.values("monthly_total")[:1]),
                  offer_product=Subquery(last_offer.values("product_name")[:1]))
        .filter(offer_at__lte=now - timedelta(days=OFFER_WAIT_DAYS))
        .select_related("company", "owner")
        .order_by("offer_at")
    )
    rows, monthly = [], Decimal("0.00")
    for lead in leads:
        monthly += lead.offer_monthly or Decimal("0.00")
        if len(rows) >= OFFERS_LIST_MAX:
            continue
        owner = lead.owner
        rows.append({
            "lead": lead.pk,
            "name": lead.display_name,
            "phone": lead.phone,
            "agent": (owner.get_full_name() or owner.username) if owner else "Pula",
            "offer_at": lead.offer_at,
            "days": (now - lead.offer_at).days,
            "product": lead.offer_product or "",
            "monthly": lead.offer_monthly or Decimal("0.00"),
            "next_contact_at": lead.next_contact_at,
            "overdue": bool(lead.next_contact_at and lead.next_contact_at < now),
        })
    return {"count": leads.count(), "monthly": monthly, "rows": rows, "wait_days": OFFER_WAIT_DAYS}


def _month_key(d):
    return f"{d.year}-{d.month:02d}"


def contracts(today=None):
    """Klienci z zapisanym końcem umowy: kto i kiedy będzie mógł przenieść numery."""
    today = today or timezone.localdate()
    open_leads = Lead.objects.exclude(status__in=(Lead.Status.WON, Lead.Status.DO_NOT_CALL))
    with_end = list(open_leads.filter(contract_end__isnull=False, contract_open_ended=False)
                    .select_related("company", "owner").order_by("contract_end"))
    now_ready, months, upcoming = [], Counter(), []
    limit = date(today.year + 1, today.month, 1)
    for lead in with_end:
        start = porting_from(lead.contract_end)
        if start <= today:
            now_ready.append(lead)
        elif start <= limit:
            months[_month_key(start)] += 1
            if len(upcoming) < 20:
                owner = lead.owner
                upcoming.append({"lead": lead.pk, "name": lead.display_name,
                                 "agent": (owner.get_full_name() or owner.username) if owner else "Pula",
                                 "contract_end": lead.contract_end, "porting_from": start,
                                 "next_contact_at": lead.next_contact_at})
    month_rows, y, m = [], today.year, today.month
    for _ in range(CONTRACT_MONTHS_AHEAD):
        m += 1
        if m > 12:
            y, m = y + 1, 1
        month_rows.append({"month": f"{y}-{m:02d}", "count": months.get(f"{y}-{m:02d}", 0)})
    # „można już przenosić”, a klient nie jest u nikogo w toku (nikt go nie prowadzi albo jest zamknięty)
    ready_idle = [l for l in now_ready if l.status not in ACTIVE_STATUSES or l.owner_id is None]
    return {
        "known": len(with_end) + open_leads.filter(contract_open_ended=True).count(),
        "open_ended": open_leads.filter(contract_open_ended=True).count(),
        "ready_now": len(now_ready),
        "ready_idle": [{"lead": l.pk, "name": l.display_name, "status": l.status,
                        "contract_end": l.contract_end} for l in ready_idle[:20]],
        "ready_idle_count": len(ready_idle),
        "months": month_rows,
        "upcoming": upcoming,
    }


def build_reports(date_from, date_to):
    since, _ = day_bounds(date_from)
    _, until = day_bounds(date_to)
    return {
        "date_from": date_from,
        "date_to": date_to,
        "funnel": funnel(since, until),
        "hours": answer_hours(since, until),
        "refusals": refusals(since, until),
        "offers": offers_waiting(),
        "contracts": contracts(),
    }
