"""Logika biznesowa CRM (poza widokami): losowanie leadów i terminarz oddzwonień."""

from datetime import timedelta

from django.db import transaction
from django.db.models import F
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

    return activity