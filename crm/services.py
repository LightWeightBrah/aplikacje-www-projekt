"""Logika biznesowa CRM (poza widokami): losowanie leadów, terminarz oddzwonień
i cykl życia leada (limit prób, zwrot do puli, nieosiągalni),
statystyki oraz plan dnia."""

import json
import re
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce, TruncDate, TruncHour
from django.utils import timezone
from rest_framework.renderers import BaseRenderer

from .models import Activity, AgentProfile, Lead, LeadAssignment
from .roles import AGENT
from .rules import attempt_times, get_rules, suggest_retry

# Statusy leadów, nad którymi agent jeszcze pracuje
ACTIVE_STATUSES = (
    Lead.Status.NEW,
    Lead.Status.CALLBACK,
    Lead.Status.INTERESTED,
    Lead.Status.OFFER,
)

# Jak wynik rozmowy (Activity.outcome) zmienia status leada.
# Wyniki spoza tej listy (nieodebrany telefon, notatka) nie zmieniają statusu.
OUTCOME_TO_STATUS = {
    Activity.Outcome.CALLBACK: Lead.Status.CALLBACK,
    Activity.Outcome.INTERESTED: Lead.Status.INTERESTED,
    Activity.Outcome.SOLD: Lead.Status.WON,
    Activity.Outcome.REFUSED: Lead.Status.LOST,
    Activity.Outcome.WRONG_NUMBER: Lead.Status.DO_NOT_CALL,
}

# Zamknięte leady nie mają już kolejnego kroku
CLOSED_STATUSES = (Lead.Status.WON, Lead.Status.LOST, Lead.Status.DO_NOT_CALL)

# Nieodebrany telefon w tym oknie przed terminem też liczy się jako nieodebrany umówiony kontakt
MISSED_WINDOW = timedelta(minutes=5)
# Po tych wynikach rozmowa się odbyła, więc licznik nieodebranych umówionych kontaktów wraca do zera
# Statusy leadów z kontaktem obiecanym klientowi (tylko tu nieodebranie w terminie to „nieodebrany umówiony kontakt”)
PROMISED_STATUSES = (Lead.Status.CALLBACK, Lead.Status.INTERESTED, Lead.Status.OFFER)

OUTCOMES_THAT_END_MISSES = (
    Activity.Outcome.CALLBACK,
    Activity.Outcome.INTERESTED,
    Activity.Outcome.SOLD,
    Activity.Outcome.REFUSED,
    Activity.Outcome.WRONG_NUMBER,
)

CLAIM_ATTEMPTS = 5  # tyle razy próbujemy, gdy ktoś inny zdąży przejąć wylosowany lead


class NoLeadsAvailable(Exception):
    """W puli nie ma leadu, który ten agent może dostać."""


class InvalidAssignment(Exception):
    """Przypisanie leada jest niedozwolone (komunikat dla managera)."""


class OverdueCallbacks(Exception):
    """Agent ma zaległe oddzwonienia, więc nie dostanie nowego leada."""

    def __init__(self, count):
        super().__init__(count)
        self.count = count


def with_transfer_info(queryset):
    """Dodaje do leadów `transferred_from_name`: kto prowadził leada przed obecnym opiekunem.

    Bierzemy ostatnie przypisanie do obecnego opiekuna; gdy lead przyszedł z puli,
    wartość jest pusta.
    """
    latest = LeadAssignment.objects.filter(
        lead=OuterRef("pk"), to_agent=OuterRef("owner")
    ).order_by("-assigned_at", "-id")
    return queryset.annotate(
        transferred_from_name=Subquery(latest.values("from_agent__username")[:1])
    )


def with_owner_tries(queryset):
    """Dodaje do leadów `owner_no_answers`: ile razy obecny opiekun nie dodzwonił się do leada."""
    tries = (
        Activity.objects.filter(
            lead=OuterRef("pk"), agent=OuterRef("owner"), outcome=Activity.Outcome.NO_ANSWER
        )
        .values("lead")
        .annotate(n=Count("pk"))
        .values("n")
    )
    # wcześniejsze nieodebrane próby (godziny), żeby ponowna próba wypadła o innej porze dnia
    earlier = Prefetch(
        "activities",
        queryset=Activity.objects.filter(outcome=Activity.Outcome.NO_ANSWER),
        to_attr="no_answer_acts",
    )
    return queryset.annotate(owner_no_answers=Coalesce(Subquery(tries), 0)).prefetch_related(
        earlier
    )


def overdue_leads(user):
    """Zaległe oddzwonienia agenta: obiecane klientowi (oddzwonić / zainteresowany / oferta) z minionym terminem.

    Tylko one blokują pobranie nowego leada. Ponowna próba do leada, z którym nikt jeszcze
    nie rozmawiał (status „nowy”), jest zadaniem miękkim i nie blokuje.
    """
    return Lead.objects.filter(
        owner=user,
        status__in=PROMISED_STATUSES,
        next_contact_at__lt=timezone.now(),
    )


def due_leads(user, minutes):
    """Leady agenta do zrobienia: zaległe oraz takie z terminem w ciągu `minutes` minut."""
    now = timezone.now()
    queryset = Lead.objects.filter(
        owner=user,
        status__in=ACTIVE_STATUSES,
        next_contact_at__isnull=False,
        next_contact_at__lte=now + timedelta(minutes=minutes),
    ).order_by("next_contact_at")
    overdue = [lead for lead in queryset if lead.next_contact_at < now]
    upcoming = [lead for lead in queryset if lead.next_contact_at >= now]
    return overdue, upcoming


def claim_next_lead(user, batch_id=None):
    """Losuje lead z puli i przypisuje go agentowi.

    Przypisanie jest atomowe: warunkowy UPDATE zadziała tylko wtedy, gdy lead
    nadal nie ma opiekuna, więc dwóch agentów nie dostanie tego samego leada.
    """
    overdue_count = overdue_leads(user).count()
    if overdue_count:
        raise OverdueCallbacks(overdue_count)

    pool = (
        Lead.objects.filter(owner__isnull=True, status=Lead.Status.NEW)
        # ten sam agent nie dostaje leada, który już wcześniej prowadził
        .exclude(assignments__from_agent=user)
    )
    if batch_id is not None:
        pool = pool.filter(batch_id=batch_id)

    for _ in range(CLAIM_ATTEMPTS):
        lead_id = pool.order_by("?").values_list("id", flat=True).first()
        if lead_id is None:
            break
        with transaction.atomic():
            claimed = Lead.objects.filter(id=lead_id, owner__isnull=True).update(
                owner=user, claimed_at=timezone.now()
            )
            if claimed:
                LeadAssignment.objects.create(
                    lead_id=lead_id,
                    to_agent=user,
                    reason="Losowanie z puli (leads/next)",
                )
                return Lead.objects.get(id=lead_id)
    raise NoLeadsAvailable


def register_activity(
    lead, agent, outcome, note="", next_contact_at=None, next_contact_note=None
):
    """Zapisuje rozmowę (Activity) i od razu aktualizuje lead.

    - `last_activity_at` ustawiamy zawsze,
    - `attempts_count` rośnie przy każdym wyniku poza zwykłą notatką,
    - status zmienia się wg OUTCOME_TO_STATUS,
    - termin i sprawa kolejnego kontaktu: ustawiane, gdy zostały podane (sprawa jest
      opcjonalna: bez niej zostaje dotychczasowa, pusty tekst ją czyści);
      zamknięty lead (przegrany, nie dzwonić) ich nie ma; po zmianie statusu
      na inny niż "do oddzwonienia" stary termin jest czyszczony,
      żeby nie blokował agenta jako zaległy,
    - "nie odebrał" w umówionym terminie (albo po nim) liczy się jako nieodebrany
      umówiony kontakt: rośnie `missed_calls`, umówiony termin zostaje w
      `agreed_contact_at`, a jeśli agent nie wskazał nowego terminu, system sam
      ustawia ponowną próbę za `callback_retry_minutes` minut (ustawienie managera). Dzięki temu lead
      nie zostaje "zaległy" w nieskończoność i nie blokuje pobrania kolejnego.
    """
    with transaction.atomic():
        now = timezone.now()
        rules = get_rules()
        before = Lead.objects.values(
            "next_contact_at", "agreed_contact_at", "status", "owner_id"
        ).get(pk=lead.pk)
        changes = {"last_activity_at": now}
        if outcome != Activity.Outcome.NOTE:
            changes["attempts_count"] = F("attempts_count") + 1

        # Umówiony kontakt, do którego nie dodzwoniono (termin minął lub właśnie nadszedł)
        # (tylko kontakt obiecany klientowi: oddzwonić / zainteresowany; zwykła ponowna próba
        # po nieodebraniu to po prostu kolejna próba, a nie „nieodebrany umówiony kontakt”)
        term_due = (
            before["next_contact_at"] is not None
            and before["next_contact_at"] <= now + MISSED_WINDOW
        )
        missed = (
            outcome == Activity.Outcome.NO_ANSWER
            and term_due
            and before["status"] in PROMISED_STATUSES
        )
        # Pierwszy kontakt, do którego nikt się jeszcze nie dodzwonił: system sam proponuje
        # ponowną próbę (chyba że to ostatnia próba agenta, wtedy lead wraca do puli)
        auto_retry_at = None
        if (
            outcome == Activity.Outcome.NO_ANSWER
            and not missed
            and before["status"] == Lead.Status.NEW
            and before["owner_id"] is not None
        ):
            tries_before = Activity.objects.filter(
                lead_id=lead.pk, agent_id=before["owner_id"], outcome=outcome
            ).count()
            attempts_raw = list(Activity.objects.filter(
                lead_id=lead.pk, outcome=outcome).values_list("created_at", "rescheduled_to"))
            earlier = attempt_times(rules, attempts_raw)
            if tries_before + 1 < rules.max_attempts_per_agent:
                suggested = suggest_retry(rules, tries_before, now, earlier + [now])
                # Termin ustawiony wcześniej spoza rozmów (np. wpisany ręcznie albo przez managera)
                # i późniejszy niż propozycja zostaje. Termin, który powstał przy poprzednich próbach
                # (automatyczny albo z formularza), zastępuje nowa propozycja: inaczej rotacja godzin
                # „zacinała się”, bo zapis się nie zmieniał, a w historii brakowało nowej godziny.
                earlier_terms = {m for _c, m in attempts_raw if m is not None}
                keep = (before["next_contact_at"] is not None and before["next_contact_at"] >= suggested
                        and before["next_contact_at"] not in earlier_terms)
                if not keep:
                    auto_retry_at = suggested
        if missed:
            changes["missed_calls"] = F("missed_calls") + 1
            if before["agreed_contact_at"] is None:
                changes["agreed_contact_at"] = before["next_contact_at"]
        elif outcome in OUTCOMES_THAT_END_MISSES:
            # Rozmowa się odbyła: licznik zerujemy, a nowy umówiony termin zapamiętujemy
            changes["missed_calls"] = 0
            agreed = outcome in (Activity.Outcome.CALLBACK, Activity.Outcome.INTERESTED)
            changes["agreed_contact_at"] = next_contact_at if agreed else None

        new_status = OUTCOME_TO_STATUS.get(outcome)
        if new_status:
            changes["status"] = new_status

        if new_status in CLOSED_STATUSES:
            changes["next_contact_at"] = None
            changes["next_contact_note"] = ""
        elif next_contact_at is not None:
            changes["next_contact_at"] = next_contact_at
            if next_contact_note is not None:
                changes["next_contact_note"] = next_contact_note
        elif missed:
            # Brak nowego terminu od agenta: ponowna próba za chwilę, sprawa zostaje ta sama
            changes["next_contact_at"] = now + timedelta(minutes=rules.callback_retry_minutes)
        elif auto_retry_at is not None:
            changes["next_contact_at"] = auto_retry_at
        elif new_status and new_status != Lead.Status.CALLBACK:
            changes["next_contact_at"] = None
            changes["next_contact_note"] = ""

        # Do historii: na kiedy był zaplanowany ten kontakt i na kiedy ustawiono następny
        is_call = outcome != Activity.Outcome.NOTE
        rescheduled = changes.get("next_contact_at")
        activity = Activity.objects.create(
            lead=lead,
            agent=agent,
            outcome=outcome,
            note=note,
            planned_at=before["next_contact_at"] if is_call else None,
            rescheduled_to=rescheduled if is_call else None,
        )

        Lead.objects.filter(pk=lead.pk).update(**changes)

        # Kolejny nieodebrany telefon może wyczerpać limit prób agenta
        if outcome == Activity.Outcome.NO_ANSWER:
            release_if_exhausted(lead.pk)

    return activity


def _return_to_pool(lead, new_status, reason):
    """Odbiera leada agentowi i zapisuje to w historii przypisań.

    Warunkowy UPDATE (owner nadal ten sam) chroni przed wyścigiem z innym żądaniem.
    """
    former_owner_id = lead.owner_id
    changed = Lead.objects.filter(pk=lead.pk, owner_id=former_owner_id).update(
        owner=None,
        claimed_at=None,
        status=new_status,
        next_contact_at=None,
        next_contact_note="",
    )
    if changed:
        LeadAssignment.objects.create(
            lead=lead, from_agent_id=former_owner_id, to_agent=None, reason=reason
        )
    return bool(changed)


def release_if_exhausted(lead_id):
    """Zwraca leada do puli, gdy jego opiekun wyczerpał limit prób.

    Dotyczy tylko leadów o statusie "nowy": lead z obietnicą oddzwonienia
    zostaje przy agencie. Zwraca nowy status leada albo None, gdy nic się nie stało.

    - opiekun ma już LEAD_MAX_ATTEMPTS_PER_AGENT nieodebranych prób -> lead wraca do puli,
    - jeśli tyle prób zrobiło już LEAD_UNREACHABLE_AFTER_AGENTS różnych agentów,
      lead dostaje status "nieosiągalny" i do puli nie wraca.
    """
    lead = Lead.objects.get(pk=lead_id)
    if lead.owner_id is None or lead.status != Lead.Status.NEW:
        return None

    no_answers = Activity.objects.filter(
        lead=lead, outcome=Activity.Outcome.NO_ANSWER
    )
    rules = get_rules()
    owner_tries = no_answers.filter(agent_id=lead.owner_id).count()
    if owner_tries < rules.max_attempts_per_agent:
        return None

    agents_tried = no_answers.values("agent").distinct().count()
    if agents_tried >= rules.unreachable_after_agents:
        new_status = Lead.Status.UNREACHABLE
        reason = f"Nieosiągalny: {agents_tried} agentów bez skutku"
    else:
        new_status = Lead.Status.NEW
        reason = f"Limit prób ({owner_tries}) wyczerpany, zwrot do puli"

    if _return_to_pool(lead, new_status, reason):
        return new_status
    return None


def release_stale_leads(dry_run=False):
    """Zwraca do puli leady, których agent nie ruszył od LEAD_CLAIM_TIMEOUT_HOURS godzin.

    "Nietknięty" = status nowy, przypisany dawno temu i bez żadnej aktywności
    obecnego opiekuna. Zwraca liczbę leadów (przy dry_run tylko je liczy).
    """
    hours = get_rules().claim_timeout_hours
    cutoff = timezone.now() - timedelta(hours=hours)
    touched = Activity.objects.filter(lead=OuterRef("pk"), agent=OuterRef("owner"))
    stale = Lead.objects.filter(
        owner__isnull=False,
        status=Lead.Status.NEW,
        claimed_at__lt=cutoff,
    ).exclude(Exists(touched))

    if dry_run:
        return stale.count()

    released = 0
    for lead in stale:
        reason = f"Brak aktywności przez {hours} h, zwrot do puli"
        if _return_to_pool(lead, Lead.Status.NEW, reason):
            released += 1
    return released


# Leady zamknięte nie są już przepisywane ani zwracane do puli
FINISHED_STATUSES = (Lead.Status.WON, Lead.Status.LOST, Lead.Status.DO_NOT_CALL)


def assign_lead(lead, agent, reason=""):
    """Manager przypisuje leada agentowi albo zwraca go do puli (agent=None).

    - zamknięty lead (wygrany, przegrany, nie dzwonić) nie może być przepisany,
    - do puli wracają tylko leady nowe i nieosiągalne (resztę trzeba przepisać),
    - przepisanie zachowuje status i termin kontaktu, tylko nieosiągalny
      lead dostaje status "nowy",
    - każda zmiana trafia do historii (LeadAssignment).
    """
    with transaction.atomic():
        lead = Lead.objects.get(pk=lead.pk)
        if lead.status in FINISHED_STATUSES:
            raise InvalidAssignment("Zamkniętego leada nie można przepisać.")
        if lead.owner_id == (agent.pk if agent else None):
            raise InvalidAssignment(
                "Ten agent już prowadzi leada."
                if agent
                else "Lead jest już w puli."
            )

        if agent is None:
            if lead.status not in (Lead.Status.NEW, Lead.Status.UNREACHABLE):
                raise InvalidAssignment(
                    "Do puli wracają tylko leady nowe i nieosiągalne. "
                    "Przepisz ten lead na innego agenta."
                )
            changes = {
                "owner": None,
                "claimed_at": None,
                "status": Lead.Status.NEW,
                "next_contact_at": None,
                "next_contact_note": "",
            }
            default_reason = "Zwrot do puli przez managera"
        else:
            changes = {"owner": agent, "claimed_at": timezone.now()}
            if lead.status == Lead.Status.UNREACHABLE:
                changes["status"] = Lead.Status.NEW
            default_reason = "Przypisanie przez managera"

        former_owner_id = lead.owner_id
        Lead.objects.filter(pk=lead.pk).update(**changes)
        LeadAssignment.objects.create(
            lead=lead,
            from_agent_id=former_owner_id,
            to_agent=agent,
            reason=reason or default_reason,
        )
    return Lead.objects.get(pk=lead.pk)


def reassign_agent_leads(from_agent, to_agent, reason=""):
    """Przepisuje wszystkie aktywne leady odchodzącego agenta na innego agenta.

    Zwraca liczbę przepisanych leadów. Leady zamknięte zostają jak były.
    """
    reason = reason or "Przepisanie leadów odchodzącego agenta"
    with transaction.atomic():
        leads = list(Lead.objects.filter(owner=from_agent, status__in=ACTIVE_STATUSES))
        for lead in leads:
            assign_lead(lead, to_agent, reason)
    return len(leads)


def unreachable_leads(ready_only=False):
    """Pula nieosiągalnych. ready_only: tylko te, które przeleżały cooldown."""
    queryset = Lead.objects.filter(status=Lead.Status.UNREACHABLE)
    if ready_only:
        cutoff = timezone.now() - timedelta(days=get_rules().unreachable_cooldown_days)
        queryset = queryset.filter(owner__isnull=True, last_activity_at__lte=cutoff)
    return queryset.order_by("last_activity_at")


def recycle_unreachable():
    """Zwraca do obiegu nieosiągalne leady po `unreachable_cooldown_days` dniach (ustawienie managera).

    Lead dostaje status "nowy" i wraca do puli. Agenci, którzy już go prowadzili,
    nadal go nie dostaną (historia przypisań). Zwraca liczbę leadów.
    """
    recycled = 0
    days = get_rules().unreachable_cooldown_days
    for lead in unreachable_leads(ready_only=True):
        with transaction.atomic():
            changed = Lead.objects.filter(
                pk=lead.pk, status=Lead.Status.UNREACHABLE
            ).update(status=Lead.Status.NEW)
            if changed:
                LeadAssignment.objects.create(
                    lead=lead,
                    reason=(
                        f"Koniec okresu karencji "
                        f"({days} dni), zwrot do obiegu"
                    ),
                )
                recycled += 1
    return recycled



# ---------------------------------------------------------------- plan dnia

def day_bounds(day):
    """Początek i koniec doby (w lokalnej strefie czasowej) dla podanej daty."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), tz)
    return start, end


def _scheduled_leads(user):
    """Aktywne leady agenta, które mają wyznaczony termin kontaktu."""
    return Lead.objects.filter(
        owner=user,
        status__in=ACTIVE_STATUSES,
        next_contact_at__isnull=False,
    ).select_related("product", "company", "batch")


def day_schedule(user, day):
    """Plan dnia agenta: (zaległe, zaplanowane na ten dzień).

    Zaległe (z terminem przed dzisiejszą dobą) pokazujemy tylko w planie na dziś.
    """
    start, end = day_bounds(day)
    base = _scheduled_leads(user)
    scheduled = base.filter(
        next_contact_at__gte=start, next_contact_at__lt=end
    ).order_by("next_contact_at")
    if day == timezone.localdate():
        overdue = base.filter(next_contact_at__lt=start).order_by("next_contact_at")
    else:
        overdue = base.none()
    return list(overdue), list(scheduled)


def calendar_leads(user):
    """Leady do kalendarza: terminy od początku dzisiejszej doby."""
    start, _ = day_bounds(timezone.localdate())
    return _scheduled_leads(user).filter(next_contact_at__gte=start).order_by(
        "next_contact_at"
    )


def _ics_escape(text):
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def calendar_ics(leads):
    """Buduje plik kalendarza (.ics): każdy termin kontaktu to 15-minutowe wydarzenie."""
    utc = dt_timezone.utc
    stamp = timezone.now().astimezone(utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LeadCRM//PL", "CALSCALE:GREGORIAN"]
    for lead in leads:
        start = lead.next_contact_at.astimezone(utc).strftime("%Y%m%dT%H%M%SZ")
        description = f"Tel: {lead.phone}. {lead.next_contact_note}".strip()
        lines += [
            "BEGIN:VEVENT",
            f"UID:lead-{lead.pk}@leadcrm",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start}",
            "DURATION:PT15M",
            f"SUMMARY:{_ics_escape('Oddzwonić: ' + lead.contact_name)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


class CalendarRenderer(BaseRenderer):
    """Odpowiedź w formacie kalendarza (text/calendar); błędy zwracamy jako JSON."""

    media_type = "text/calendar"
    format = "ics"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, (str, bytes)):
            return data
        return json.dumps(data)


# ------------------------------------------------------------- statystyki

def _ratio(part, whole):
    return round(part / whole, 2) if whole else None


def lead_funnel(leads):
    """Lejek: liczba leadów w każdym statusie (także tych z zerem)."""
    counts = dict(leads.order_by().values_list("status").annotate(n=Count("id")))
    return {code: counts.get(code, 0) for code, _ in Lead.Status.choices}


def agent_stats(users, since, until=None):
    """Skuteczność agentów: rozmowy od `since` (do `until`, bez niego) oraz bieżący stan ich leadów."""
    ids = [user.pk for user in users]
    Outcome = Activity.Outcome

    in_range = Activity.objects.filter(agent__in=ids, created_at__gte=since)
    if until is not None:
        in_range = in_range.filter(created_at__lt=until)
    activity_rows = (
        in_range
        .order_by()
        .values("agent")
        .annotate(
            calls=Count("id", filter=~Q(outcome=Outcome.NOTE)),
            answered=Count(
                "id", filter=~Q(outcome__in=[Outcome.NOTE, Outcome.NO_ANSWER])
            ),
            interested=Count("id", filter=Q(outcome=Outcome.INTERESTED)),
            refused=Count("id", filter=Q(outcome=Outcome.REFUSED)),
            sold=Count("id", filter=Q(outcome=Outcome.SOLD)),
            no_answer=Count("id", filter=Q(outcome=Outcome.NO_ANSWER)),
            callbacks=Count("id", filter=Q(outcome=Outcome.CALLBACK)),
            wrong_number=Count("id", filter=Q(outcome=Outcome.WRONG_NUMBER)),
        )
    )
    activity = {row["agent"]: row for row in activity_rows}

    lead_rows = (
        Lead.objects.filter(owner__in=ids)
        .order_by()
        .values("owner")
        .annotate(
            leads_total=Count("id"),
            leads_active=Count("id", filter=Q(status__in=ACTIVE_STATUSES)),
            leads_won=Count("id", filter=Q(status=Lead.Status.WON)),
        )
    )
    leads = {row["owner"]: row for row in lead_rows}

    result = []
    for user in users:
        a = activity.get(user.pk, {})
        l = leads.get(user.pk, {})
        calls, answered, interested = (
            a.get("calls", 0),
            a.get("answered", 0),
            a.get("interested", 0),
        )
        result.append(
            {
                "agent": user.pk,
                "username": user.username,
                "leads_total": l.get("leads_total", 0),
                "leads_active": l.get("leads_active", 0),
                "leads_won": l.get("leads_won", 0),
                "calls": calls,
                "answered": answered,
                "interested": interested,
                "refused": a.get("refused", 0),
                "sold": a.get("sold", 0),
                "no_answer": a.get("no_answer", 0),
                "callbacks": a.get("callbacks", 0),
                "wrong_number": a.get("wrong_number", 0),
                "answer_rate": _ratio(answered, calls),
                "interest_rate": _ratio(interested, answered),
            }
        )
    return result


def daily_calls(first, last, agent_ids=None):
    """Liczba rozmów (bez notatek) w każdym dniu od `first` do `last` włącznie, także z zerami."""
    since, _ = day_bounds(first)
    _, until = day_bounds(last)

    queryset = Activity.objects.filter(created_at__gte=since, created_at__lt=until).exclude(
        outcome=Activity.Outcome.NOTE
    )
    if agent_ids is not None:
        queryset = queryset.filter(agent__in=agent_ids)
    rows = (
        queryset.order_by()
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(n=Count("id"))
    )
    counts = {row["day"]: row["n"] for row in rows}
    total_days = (last - first).days + 1
    return [
        {"date": first + timedelta(days=i), "calls": counts.get(first + timedelta(days=i), 0)}
        for i in range(total_days)
    ]


def hourly_calls(day, agent_ids=None):
    """Liczba rozmów (bez notatek) w każdej godzinie (0-23) wskazanego dnia, także z zerami."""
    since, until = day_bounds(day)
    queryset = Activity.objects.filter(created_at__gte=since, created_at__lt=until).exclude(
        outcome=Activity.Outcome.NOTE
    )
    if agent_ids is not None:
        queryset = queryset.filter(agent__in=agent_ids)
    rows = (
        queryset.order_by()
        .annotate(hour=TruncHour("created_at"))
        .values("hour")
        .annotate(n=Count("id"))
    )
    counts = {}
    for row in rows:
        local_hour = timezone.localtime(row["hour"]).hour
        counts[local_hour] = counts.get(local_hour, 0) + row["n"]
    return [{"hour": h, "calls": counts.get(h, 0)} for h in range(24)]


def outcome_counts(since, until, agent_ids=None):
    """Ile razy wystąpił każdy wynik rozmowy w okresie (także zera i notatki)."""
    queryset = Activity.objects.filter(created_at__gte=since, created_at__lt=until)
    if agent_ids is not None:
        queryset = queryset.filter(agent__in=agent_ids)
    counts = dict(queryset.order_by().values_list("outcome").annotate(n=Count("id")))
    return {code: counts.get(code, 0) for code, _ in Activity.Outcome.choices}


def scheduled_counts(leads):
    """Aktywne leady z wyznaczonym terminem kontaktu: zaległe, na dziś, jutro i później.

    To stan na teraz (nie zależy od wybranego okresu).
    """
    now = timezone.now()
    today = timezone.localdate()
    _, end_today = day_bounds(today)
    _, end_tomorrow = day_bounds(today + timedelta(days=1))
    planned = leads.filter(status__in=ACTIVE_STATUSES, next_contact_at__isnull=False)
    return {
        "overdue": planned.filter(next_contact_at__lt=now).count(),
        "today": planned.filter(next_contact_at__gte=now, next_contact_at__lt=end_today).count(),
        "tomorrow": planned.filter(
            next_contact_at__gte=end_today, next_contact_at__lt=end_tomorrow
        ).count(),
        "later": planned.filter(next_contact_at__gte=end_tomorrow).count(),
    }


def build_stats(user, days, manager, date_from=None, date_to=None):
    """Statystyki: manager widzi cały zespół, agent tylko siebie.

    Okres to albo ostatnie `days` dni (łącznie z dzisiaj), albo zakres
    `date_from`-`date_to` (obie daty włącznie).
    """
    if date_from is None:
        date_to = timezone.localdate()
        date_from = date_to - timedelta(days=days - 1)
    days = (date_to - date_from).days + 1
    since, _ = day_bounds(date_from)
    _, until = day_bounds(date_to)
    User = get_user_model()

    if manager:
        leads = Lead.objects.all()
        active_ids = set(
            Activity.objects.filter(created_at__gte=since, created_at__lt=until)
            .exclude(agent=None)
            .values_list("agent", flat=True)
        ) | set(
            Lead.objects.exclude(owner=None).values_list("owner", flat=True)
        )
        users = list(
            User.objects.filter(Q(groups__name=AGENT) | Q(pk__in=active_ids))
            .distinct()
            .order_by("username")
        )
        pool = Lead.objects.filter(owner__isnull=True, status=Lead.Status.NEW).count()
        agent_ids = None
    else:
        leads = Lead.objects.filter(owner=user)
        users = [user]
        pool = None
        agent_ids = [user.pk]

    return {
        "days": days,
        "date_from": date_from,
        "date_to": date_to,
        "total_leads": leads.count(),
        "pool_leads": pool,
        "leads_by_status": lead_funnel(leads),
        "outcomes": outcome_counts(since, until, agent_ids),
        "scheduled": scheduled_counts(leads),
        "agents": agent_stats(users, since, until),
        "daily_calls": daily_calls(date_from, date_to, agent_ids),
        # rozbicie na godziny tylko dla okresu jednodniowego (np. "Dziś")
        "hourly_calls": hourly_calls(date_from, agent_ids) if date_from == date_to else [],
    }


LEADERBOARD_SORT_KEYS = ("sold", "calls", "interested")


def leaderboard(date_from, date_to, sort="sold", me_id=None):
    """Ranking agentów w okresie: tylko liczby wyników, bez żadnych danych klientów.

    Sortowanie malejąco wg `sort` (sold / calls / interested), remis rozstrzyga liczba
    rozmów. Agenci z takim samym wynikiem i taką samą liczbą rozmów mają to samo miejsce.
    """
    since, _ = day_bounds(date_from)
    _, until = day_bounds(date_to)
    users = list(
        get_user_model()
        .objects.filter(is_active=True, groups__name=AGENT)
        .distinct()
        .order_by("username")
    )
    avatars = dict(
        AgentProfile.objects.filter(user__in=users).exclude(avatar="").values_list("user_id", "avatar")
    )
    rows = [
        {
            "agent": row["agent"],
            "username": row["username"],
            "full_name": user.get_full_name() or user.username,
            "avatar": avatars.get(user.pk, ""),
            "sold": row["sold"],
            "calls": row["calls"],
            "answered": row["answered"],
            "interested": row["interested"],
            "answer_rate": row["answer_rate"],
            "is_me": row["agent"] == me_id,
        }
        for user, row in zip(users, agent_stats(users, since, until))
    ]
    rows.sort(key=lambda r: (-r[sort], -r["calls"], r["username"]))
    previous, rank = None, 0
    for position, row in enumerate(rows, start=1):
        score = (row[sort], row["calls"])
        if score != previous:
            rank, previous = position, score
        row["rank"] = rank
    return rows


def release_agent_leads(agent, reason=""):
    """Zwraca do puli wszystkie aktywne leady agenta (np. przy dezaktywacji konta).

    W przeciwieństwie do `assign_lead(lead, None)` zwraca także leady z terminem
    kontaktu (oddzwonienia, zainteresowani): wracają do puli jako nowe, a historia
    kontaktów zostaje. Zwraca liczbę zwróconych leadów.
    """
    reason = reason or "Zwrot do puli: konto agenta zostało zdezaktywowane"
    count = 0
    with transaction.atomic():
        for lead in Lead.objects.filter(owner=agent, status__in=ACTIVE_STATUSES):
            if _return_to_pool(lead, Lead.Status.NEW, reason):
                count += 1
    return count


# ---------------------------------------------------------------------------
# Wyszukiwarka leadów (np. gdy ktoś dzwoni: wpisujesz numer albo NIP i od razu masz jego kartę)
# ---------------------------------------------------------------------------
def _local_phone(value):
    """Same cyfry numeru, bez prefiksu kraju (+48 / 0048), żeby „+48 500 100 200” == „500100200”."""
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("48"):
        digits = digits[2:]
    return digits


def lookup_leads(queryset, query, limit=8):
    """Zwraca listę (lead, dopasowanie, pole) z podanego querysetu.

    dopasowanie: „exact” (cały numer / NIP / KRS) albo „partial” (fragment);
    pole: po czym znaleziono („phone”, „nip”, „krs”, „name”, „company”, „email”).

    Zapytanie złożone z cyfr (spacje, +, -, nawiasy są pomijane) szuka po numerze telefonu,
    NIP i KRS (najpierw dokładne trafienia, potem fragmenty, np. ostatnie cyfry numeru).
    Inne zapytanie szuka po nazwisku, nazwie firmy i e-mailu."""
    query = (query or "").strip()
    if len(query) < 2:
        return []

    if re.fullmatch(r"[\d\s+\-().]+", query):
        digits = _local_phone(query)
        if len(digits) < 3:
            return []
        exact, partial = [], []
        for pk, phone, nip, krs in queryset.values_list("id", "phone", "company__nip", "company__krs"):
            local = _local_phone(phone)
            if digits == local:
                exact.append((pk, "exact", "phone"))
            elif digits == nip:
                exact.append((pk, "exact", "nip"))
            elif digits == krs:
                exact.append((pk, "exact", "krs"))
            elif digits in local:
                partial.append((pk, "partial", "phone"))
            elif nip and digits in nip:
                partial.append((pk, "partial", "nip"))
            elif krs and digits in krs:
                partial.append((pk, "partial", "krs"))
        ordered = (exact + partial)[:limit]
        leads = {lead.pk: lead for lead in queryset.filter(pk__in=[pk for pk, _, _ in ordered])}
        return [(leads[pk], match, field) for pk, match, field in ordered if pk in leads]

    needle = query.lower()
    found = (
        queryset.filter(
            Q(contact_name__icontains=query)
            | Q(company__name__icontains=query)
            | Q(email__icontains=query)
        )
        .order_by("contact_name")[:limit]
    )
    result = []
    for lead in found:
        if needle in lead.contact_name.lower():
            field = "name"
        elif lead.company and needle in lead.company.name.lower():
            field = "company"
        else:
            field = "email"
        result.append((lead, "partial", field))
    return result
