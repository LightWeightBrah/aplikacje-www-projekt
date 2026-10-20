"""Logika biznesowa CRM (poza widokami): losowanie leadów, terminarz oddzwonień
i cykl życia leada (limit prób, zwrot do puli, nieosiągalni)."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from .models import Activity, Lead, LeadAssignment

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
    Activity.Outcome.REFUSED: Lead.Status.LOST,
    Activity.Outcome.WRONG_NUMBER: Lead.Status.DO_NOT_CALL,
}

# Zamknięte leady nie mają już kolejnego kroku
CLOSED_STATUSES = (Lead.Status.LOST, Lead.Status.DO_NOT_CALL)

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


def overdue_leads(user):
    """Leady agenta z terminem kontaktu, który już minął."""
    return Lead.objects.filter(
        owner=user,
        status__in=ACTIVE_STATUSES,
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
    lead, agent, outcome, note="", next_contact_at=None, next_contact_note=""
):
    """Zapisuje rozmowę (Activity) i od razu aktualizuje lead.

    - `last_activity_at` ustawiamy zawsze,
    - `attempts_count` rośnie przy każdym wyniku poza zwykłą notatką,
    - status zmienia się wg OUTCOME_TO_STATUS,
    - termin i notatka kolejnego kontaktu: ustawiane, gdy zostały podane;
      zamknięty lead (przegrany, nie dzwonić) ich nie ma; po zmianie statusu
      na inny niż "do oddzwonienia" stary termin jest czyszczony,
      żeby nie blokował agenta jako zaległy.
    """
    with transaction.atomic():
        activity = Activity.objects.create(
            lead=lead, agent=agent, outcome=outcome, note=note
        )

        changes = {"last_activity_at": timezone.now()}
        if outcome != Activity.Outcome.NOTE:
            changes["attempts_count"] = F("attempts_count") + 1

        new_status = OUTCOME_TO_STATUS.get(outcome)
        if new_status:
            changes["status"] = new_status

        if new_status in CLOSED_STATUSES:
            changes["next_contact_at"] = None
            changes["next_contact_note"] = ""
        elif next_contact_at is not None:
            changes["next_contact_at"] = next_contact_at
            changes["next_contact_note"] = next_contact_note
        elif new_status and new_status != Lead.Status.CALLBACK:
            changes["next_contact_at"] = None
            changes["next_contact_note"] = ""

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
    owner_tries = no_answers.filter(agent_id=lead.owner_id).count()
    if owner_tries < settings.LEAD_MAX_ATTEMPTS_PER_AGENT:
        return None

    agents_tried = no_answers.values("agent").distinct().count()
    if agents_tried >= settings.LEAD_UNREACHABLE_AFTER_AGENTS:
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
    cutoff = timezone.now() - timedelta(hours=settings.LEAD_CLAIM_TIMEOUT_HOURS)
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
        reason = f"Brak aktywności przez {settings.LEAD_CLAIM_TIMEOUT_HOURS} h, zwrot do puli"
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
        cutoff = timezone.now() - timedelta(days=settings.LEAD_UNREACHABLE_COOLDOWN_DAYS)
        queryset = queryset.filter(owner__isnull=True, last_activity_at__lte=cutoff)
    return queryset.order_by("last_activity_at")


def recycle_unreachable():
    """Zwraca do obiegu nieosiągalne leady po LEAD_UNREACHABLE_COOLDOWN_DAYS dniach.

    Lead dostaje status "nowy" i wraca do puli. Agenci, którzy już go prowadzili,
    nadal go nie dostaną (historia przypisań). Zwraca liczbę leadów.
    """
    recycled = 0
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
                        f"({settings.LEAD_UNREACHABLE_COOLDOWN_DAYS} dni), zwrot do obiegu"
                    ),
                )
                recycled += 1
    return recycled