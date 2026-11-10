"""Logika biznesowa CRM (poza widokami): losowanie leadów, terminarz oddzwonień
i cykl życia leada (limit prób, zwrot do puli, nieosiągalni),
statystyki oraz plan dnia."""

import json
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Exists, F, Max, OuterRef, Prefetch, Q, Subquery, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncHour
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.renderers import BaseRenderer

from .models import NON_CALL_OUTCOMES, Activity, AgentProfile, Lead, LeadAssignment, Offer, SaleItem
from .roles import AGENT
from .rules import answer_rates_by_slot, attempt_times, callback_retry_at, fit_work_time, get_rules, suggest_retry
from .phrases import skip_never

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
    Activity.Outcome.OFFER: Lead.Status.OFFER,
}

# Pominięcie bez dzwonienia zamyka leada; status zależy od powodu
SKIP_TO_STATUS = {
    Activity.SkipReason.CLOSED: Lead.Status.DO_NOT_CALL,
    Activity.SkipReason.HAS_TMOBILE: Lead.Status.LOST,
    Activity.SkipReason.NO_PHONE: Lead.Status.DO_NOT_CALL,
    Activity.SkipReason.DUPLICATE: Lead.Status.DO_NOT_CALL,
    Activity.SkipReason.OTHER: Lead.Status.LOST,
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
    Activity.Outcome.OFFER,
)
# Po tych wynikach klient wie, z jaką ofertą dzwonimy
OUTCOMES_THAT_INTRODUCE = (
    Activity.Outcome.INTERESTED,
    Activity.Outcome.OFFER,
    Activity.Outcome.SOLD,
    Activity.Outcome.REFUSED,
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
        transferred_from_name=Subquery(latest.values("from_agent__username")[:1]),
        # imię i nazwisko poprzedniego agenta (gdy ma) i kiedy lead przepisano – do kart przypomnień
        transferred_from_first=Subquery(latest.values("from_agent__first_name")[:1]),
        transferred_from_last=Subquery(latest.values("from_agent__last_name")[:1]),
        transferred_at=Subquery(latest.values("assigned_at")[:1]),
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
    # ostatni kontakt z leadem (bez samych notatek): wynik, kiedy i kto, do linii „Ostatnio: …” na listach
    last = (
        Activity.objects.filter(lead=OuterRef("pk")).exclude(outcome__in=NON_CALL_OUTCOMES)
        .order_by("-created_at", "-pk")
    )
    return with_last_offer(queryset.annotate(
        owner_no_answers=Coalesce(Subquery(tries), 0),
        last_contact_outcome=Subquery(last.values("outcome")[:1]),
        last_contact_at=Subquery(last.values("created_at")[:1]),
        last_contact_agent=Subquery(last.values("agent")[:1]),
    ).prefetch_related(earlier))


def with_last_offer(queryset):
    """Dodaje do leadów ostatnią wysłaną ofertę (`last_offer_at`, `last_offer_name`, `last_offer_monthly`):
    lista klientów i przypomnienie pokazują „oferta wysłana”, też gdy klient potem poprosił o oddzwonienie."""
    last = Offer.objects.filter(lead=OuterRef("pk")).order_by("-created_at", "-pk")
    return queryset.annotate(
        last_offer_at=Subquery(last.values("created_at")[:1]),
        last_offer_name=Subquery(last.values("product_name")[:1]),
        last_offer_monthly=Subquery(last.values("monthly_total")[:1]),
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
    queryset = with_last_offer(with_transfer_info(Lead.objects.select_related("product", "company", "owner", "batch", "client_comment_by").filter(
        owner=user,
        status__in=ACTIVE_STATUSES,
        next_contact_at__isnull=False,
        next_contact_at__lte=now + timedelta(minutes=minutes),
    ))).order_by("next_contact_at")   # „przepisany od …” na karcie przypomnienia
    overdue = [lead for lead in queryset if lead.next_contact_at < now]
    upcoming = [lead for lead in queryset if lead.next_contact_at >= now]
    return overdue, upcoming


# Spółki (sp. z o.o., S.A., spółki osobowe) rozpoznajemy po nazwie z bazy: w CEIDG są tylko firmy
# jednoosobowe, spółki przychodzą z KRS. Wielu agentów woli najpierw dzwonić do firm jednoosobowych.
COMPANY_ENTITY_RE = r"(sp\.?\s*z\s*o\.?\s*o|spółka|spolka|s\.\s?a\.|s\.a$|sp\.\s?[jkp]\.)"


def is_company_entity(name):
    """Czy nazwa firmy wygląda na spółkę (sp. z o.o., S.A., sp.j., sp.k., „spółka”)."""
    return bool(re.search(COMPANY_ENTITY_RE, name or "", re.IGNORECASE))


def claim_next_lead(user, batch_id=None, companies_last=None):
    """Losuje lead z puli i przypisuje go agentowi.

    companies_last=True: spółki dostaje dopiero wtedy, gdy skończą się firmy jednoosobowe
    (domyślnie decyduje ustawienie zespołu TeamRules.companies_last, które zmienia manager).

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
        # bazy przypisane innym agentom są tylko dla nich
        .filter(Q(batch__isnull=True) | Q(batch__assigned_to__isnull=True) | Q(batch__assigned_to=user))
    )
    if batch_id is not None:
        pool = pool.filter(batch_id=batch_id)
    # najpierw własna baza agenta (jak „swój Excel”), dopiero gdy się skończy: wspólna pula
    sources = [pool.filter(batch__assigned_to=user), pool]
    if companies_last is None:
        companies_last = get_rules().companies_last
    if companies_last:
        not_company = ~Q(company__name__iregex=COMPANY_ENTITY_RE)
        sources = [src.filter(not_company) for src in sources] + sources

    for _ in range(CLAIM_ATTEMPTS):
        lead_id = None
        for source in sources:
            lead_id = source.order_by("?").values_list("id", flat=True).first()
            if lead_id is not None:
                break
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
                    reason="Losowanie z puli",
                )
                return Lead.objects.get(id=lead_id)
    raise NoLeadsAvailable


def base_progress(user):
    """Postęp w bazach przypisanych agentowi: ile leadów w bazie, ile jeszcze czeka na pierwszy telefon.

    `left` = leady, które „Daj kolejnego” może jeszcze wylosować z tej bazy; `done` = reszta
    (wylosowane, obdzwonione, pominięte albo zamknięte).
    """
    from .models import LeadBatch  # lokalnie: unikamy zmiany kolejności importów na górze pliku

    rows = []
    for batch in LeadBatch.objects.filter(assigned_to=user).order_by("created_at"):
        leads = batch.leads.all()
        total = leads.count()
        left = (
            leads.filter(owner__isnull=True, status=Lead.Status.NEW)
            .exclude(assignments__from_agent=user)
            .count()
        )
        rows.append({"id": batch.pk, "name": batch.name, "total": total, "left": left, "done": total - left})
    return rows


UNDO_WINDOW_SECONDS = 30  # serwer jest łaskawszy niż pasek w kolejce (10 s), żeby opóźnienie sieci nie psuło cofania
UNDO_FIELDS = (
    "owner_id", "status", "claimed_at", "next_contact_at", "next_contact_note",
    "agreed_contact_at", "missed_calls", "attempts_count", "last_activity_at", "knows_caller",
)


class UndoNotPossible(Exception):
    """Wpisu nie da się już cofnąć (za późno, nie jest ostatni albo lead zmienił się w międzyczasie)."""


def _snapshot(lead_pk):
    """Stan leada do przywrócenia przy cofnięciu (daty jako tekst ISO, żeby zmieścić się w JSON)."""
    row = Lead.objects.values(*UNDO_FIELDS).get(pk=lead_pk)
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def _restore_value(field, value):
    if value is not None and field in ("claimed_at", "next_contact_at", "agreed_contact_at", "last_activity_at"):
        return parse_datetime(value)
    return value


def undo_activity(activity, user, is_manager=False):
    """Cofa OSTATNI zapis leada zaraz po pomyłce: przywraca stan sprzed wpisu i usuwa wpis z historii.

    Warunki: wpis ma migawkę, jest najnowszym wpisem leada, powstał nie dawniej niż UNDO_WINDOW_SECONDS
    temu, a cofa go jego autor (albo manager). Jeśli zapis zwrócił leada do puli (wyczerpany limit prób),
    cofnięcie go odzyskuje, o ile nikt go jeszcze nie pobrał.
    """
    with transaction.atomic():
        activity = Activity.objects.select_for_update().get(pk=activity.pk)
        lead = Lead.objects.select_for_update().get(pk=activity.lead_id)
        if activity.undo_state is None:
            raise UndoNotPossible("Tego wpisu nie można cofnąć.")
        if not is_manager and activity.agent_id != user.pk:
            raise UndoNotPossible("Możesz cofnąć tylko własny zapis.")
        if timezone.now() - activity.created_at > timedelta(seconds=UNDO_WINDOW_SECONDS):
            raise UndoNotPossible("Za późno na cofnięcie tego zapisu.")
        newest = Activity.objects.filter(lead_id=lead.pk).order_by("-created_at", "-pk").first()
        if newest.pk != activity.pk:
            raise UndoNotPossible("Po tym zapisie doszły już nowe wpisy, więc nie można go cofnąć.")
        state = activity.undo_state
        if lead.owner_id not in (state["owner_id"], None):
            raise UndoNotPossible("Ten lead został już przejęty przez kogoś innego.")
        # zwrot do puli po wyczerpaniu prób zostawia wpis w historii przypisań: usuwamy go razem z zapisem
        LeadAssignment.objects.filter(
            lead_id=lead.pk, to_agent__isnull=True, assigned_at__gte=activity.created_at
        ).delete()
        restored = {k: _restore_value(k, v) for k, v in state.items()}
        Lead.objects.filter(pk=lead.pk).update(**restored)
        from .models import Offer
        Offer.objects.filter(activity=activity).delete()   # cofnięta „Wysłana oferta” znika razem z ofertą
        activity.delete()
    return Lead.objects.get(pk=lead.pk)


class ReopenNotPossible(Exception):
    """Klienta nie da się przywrócić (nie jest zamknięty, jest sprzedany albo prowadzi go ktoś inny)."""


REOPENABLE_STATUSES = (Lead.Status.LOST, Lead.Status.DO_NOT_CALL, Lead.Status.UNREACHABLE)


def reopen_lead(lead, user, is_manager=False):
    """„Przywróć do dzwonienia”: zamknięty klient (pominięty, odmowa, zły numer) wraca do pracy.

    Po pomyłce, której nie zdążono cofnąć w 10 sekund (np. „Pomiń firmę” zamiast oferty, bo zadzwonił
    inny klient). Klient wraca do stanu sprzed wpisu, który go zamknął (termin, sprawa, status), a gdy
    tego stanu nie ma – jako „oddzwonić teraz”. W historii zostaje notatka, wpis zamykający też zostaje.
    """
    with transaction.atomic():
        lead = Lead.objects.select_for_update().get(pk=lead.pk)
        if lead.status == Lead.Status.WON:
            raise ReopenNotPossible("Sprzedanego klienta nie przywracamy – dopisz nową rozmowę w historii.")
        if lead.status not in REOPENABLE_STATUSES:
            raise ReopenNotPossible("Ten klient nie jest zamknięty.")
        if lead.owner_id not in (None, user.pk) and not is_manager:
            raise ReopenNotPossible("Tego klienta prowadzi inny agent.")
        closing = (Activity.objects.filter(lead_id=lead.pk).exclude(outcome=Activity.Outcome.NOTE)
                   .order_by("-created_at", "-pk").first())
        state = (closing.undo_state if closing else None) or {}
        now = timezone.now()
        changes = {"owner_id": lead.owner_id or user.pk, "last_activity_at": now}
        if lead.owner_id is None:
            changes["claimed_at"] = now
        before = state.get("status")
        if before in ACTIVE_STATUSES:
            changes["status"] = before
            for field in ("next_contact_at", "next_contact_note", "agreed_contact_at", "missed_calls"):
                if field in state:
                    changes[field] = _restore_value(field, state[field])
        else:
            changes.update(status=Lead.Status.CALLBACK, next_contact_at=now, next_contact_note="", missed_calls=0)
        if changes["status"] == Lead.Status.CALLBACK and not changes.get("next_contact_at"):
            changes["next_contact_at"] = now   # „oddzwonić” zawsze ma termin
        Lead.objects.filter(pk=lead.pk).update(**changes)
        what = closing.get_outcome_display().lower() if closing else "zamknięty"
        if closing and closing.outcome == Activity.Outcome.SKIPPED:
            what = "pominięty" + (f" ({closing.skip_reason})" if closing.skip_reason else "")
        Activity.objects.create(lead=lead, agent=user, outcome=Activity.Outcome.NOTE,
                                note=f"Przywrócony do dzwonienia (wcześniej: {what}).")
    return Lead.objects.get(pk=lead.pk)


def add_sale_items(activity, lines):
    """Zapisuje pozycje sprzedaży do rozmowy „Sprzedane”.

    `lines` to lista krotek (produkt, ilość[, okres w miesiącach[, promocja]]). Nazwa, jednostka
    i cena są kopiowane z katalogu w chwili sprzedaży. Wartość pozycji: dla taryf pierwszy numer
    + kolejne po dopłacie (Product.monthly_total), dla zwykłych produktów ilość × cena.
    Prowizja = wartość miesięczna × mnożnik z ustawień ofert (dla umów z okresem).
    """
    from .models import OfferConfig

    config = OfferConfig.get()
    items = []
    for line in lines:
        product, quantity = line[0], line[1]
        term = line[2] if len(line) > 2 else None
        option = int(line[4] or 0) if len(line) > 4 else 0
        choice = product.promo_choice(option, config.promo_label) if len(line) > 3 and line[3] else None
        promo = choice is not None
        total = product.monthly_total(quantity, promo, option)
        commission = (total * config.multiplier(term)).quantize(Decimal("0.01")) if term else Decimal("0.00")
        items.append(SaleItem(
            activity=activity,
            product=product,
            product_name=product.name,
            unit=product.unit,
            unit_price=choice["price"] if choice else product.price,
            quantity=quantity,
            total=total,
            term_months=term,
            promo=promo,
            promo_label=choice["label"] if choice else "",
            commission=commission,
        ))
    return SaleItem.objects.bulk_create(items)


PORTING_CALL_HOUR_OFFSET = 1   # telefon w dniu przeniesienia: godzinę po otwarciu (np. 10:00)


def contract_callback(lead_id, now=None, rules=None):
    """(termin, sprawa) telefonu w dniu, od którego klient może przenieść numery – albo None.

    None, gdy końca umowy nie znamy, umowa jest bez terminu albo przenosić można już teraz.
    """
    now = now or timezone.now()
    lead = Lead.objects.only("contract_end", "contract_open_ended").get(pk=lead_id)
    start = lead.porting_from
    if start is None or start <= timezone.localdate(now):
        return None
    rules = rules or get_rules()
    local = timezone.make_aware(datetime.combine(start, time(rules.work_start_hour + PORTING_CALL_HOUR_OFFSET)))
    end = lead.contract_end
    note = f"Koniec umowy {end.month:02d}.{end.year} – można już przenieść numery"
    return fit_work_time(rules, local), note


def register_activity(
    lead, agent, outcome, note="", next_contact_at=None, next_contact_note=None,
    sale_items=None, skip_reason="", introduced=None,
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
      ustawia ponowną próbę wg `callback_retry_steps` (np. za 15 min, 1 h, 2 h, potem co dzień; w dniach
      i godzinach pracy – ustawienia managera). Dzięki temu lead
      nie zostaje "zaległy" w nieskończoność i nie blokuje pobrania kolejnego,
    - wynik "sprzedane" może mieć `sale_items` (lista par produkt + ilość): z cen katalogu
      powstają pozycje sprzedaży, z których liczy się przychód.
    """
    with transaction.atomic():
        now = timezone.now()
        rules = get_rules()
        undo_state = _snapshot(lead.pk)
        before = Lead.objects.values(
            "next_contact_at", "agreed_contact_at", "status", "owner_id", "missed_calls"
        ).get(pk=lead.pk)
        changes = {"last_activity_at": now}
        if outcome not in NON_CALL_OUTCOMES:
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
                suggested = suggest_retry(rules, tries_before, now, earlier + [now],
                                          slot_rates=answer_rates_by_slot(rules, now))
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
            agreed = outcome in (Activity.Outcome.CALLBACK, Activity.Outcome.INTERESTED, Activity.Outcome.OFFER)
            changes["agreed_contact_at"] = next_contact_at if agreed else None

        if outcome in OUTCOMES_THAT_INTRODUCE or introduced:
            changes["knows_caller"] = True
        elif introduced is False and outcome == Activity.Outcome.CALLBACK:
            changes["knows_caller"] = False

        new_status = OUTCOME_TO_STATUS.get(outcome)
        if outcome == Activity.Outcome.SKIPPED:
            # stare kody (closed, duplicate…) albo powód z list managera: „nie dzwonić więcej” / zamknięty
            new_status = SKIP_TO_STATUS.get(skip_reason) or (
                Lead.Status.DO_NOT_CALL if skip_never(rules, skip_reason) else Lead.Status.LOST)
        # „Odmowa, bo mamy umowę do …”: klient się nie zamyka, tylko wraca w dniu, od którego
        # wolno przenieść numery (12 miesięcy przed końcem umowy)
        porting_call = contract_callback(lead.pk, now, rules) if outcome == Activity.Outcome.REFUSED else None
        if porting_call and "brak kontaktu" not in (note or "").lower():
            new_status = Lead.Status.CALLBACK
            next_contact_at, next_contact_note = porting_call
            changes["agreed_contact_at"] = None
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
            changes["next_contact_at"] = callback_retry_at(rules, before["missed_calls"], now)
        elif auto_retry_at is not None:
            changes["next_contact_at"] = auto_retry_at
        elif new_status and new_status != Lead.Status.CALLBACK:
            changes["next_contact_at"] = None
            changes["next_contact_note"] = ""

        # Do historii: na kiedy był zaplanowany ten kontakt i na kiedy ustawiono następny
        is_call = outcome not in NON_CALL_OUTCOMES
        rescheduled = changes.get("next_contact_at")
        activity = Activity.objects.create(
            lead=lead,
            agent=agent,
            outcome=outcome,
            skip_reason=skip_reason if outcome == Activity.Outcome.SKIPPED else "",
            note=note,
            planned_at=before["next_contact_at"] if is_call else None,
            rescheduled_to=rescheduled if is_call else None,
            undo_state=undo_state,
        )
        if outcome == Activity.Outcome.SOLD and sale_items:
            add_sale_items(activity, sale_items)

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
    return with_owner_tries(Lead.objects.filter(
        owner=user,
        status__in=ACTIVE_STATUSES,
        next_contact_at__isnull=False,
    ).select_related("product", "company", "batch"))


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


def day_summary(user):
    """Liczby do paska dnia nad kolejką: co już zrobiono dziś i co jeszcze czeka.

    `overdue` = zaległe oddzwonienia obiecane klientowi (czerwone), `retries_due` = ponowne próby
    leadów, z którymi jeszcze nikt nie rozmawiał (termin minął, ale to nie jest zaległość wobec klienta).
    """
    start, end = day_bounds(timezone.localdate())
    now = timezone.now()
    mine = Activity.objects.filter(agent=user, created_at__gte=start, created_at__lt=end).exclude(
        outcome__in=NON_CALL_OUTCOMES
    )
    waiting = _scheduled_leads(user)
    past = waiting.filter(next_contact_at__lt=now)
    return {
        "done_today": mine.count(),
        "talked_today": mine.exclude(outcome=Activity.Outcome.NO_ANSWER).count(),
        "overdue": past.filter(status__in=PROMISED_STATUSES).count(),
        "retries_due": past.exclude(status__in=PROMISED_STATUSES).count(),
        "left_today": waiting.filter(next_contact_at__gte=now, next_contact_at__lt=end).count(),
        "active": Lead.objects.filter(owner=user, status__in=ACTIVE_STATUSES).count(),
    }


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


ICS_SUMMARY_PREFIX = {
    Lead.Status.CALLBACK: "Oddzwonić",
    Lead.Status.INTERESTED: "Zainteresowany",
    Lead.Status.OFFER: "Follow-up oferty",
    Lead.Status.NEW: "Ponowna próba",
}


def _ics_fold(line):
    """Zawija linię co 75 bajtów (wymóg formatu iCalendar, RFC 5545); polskie znaki nie są dzielone."""
    out, current = [], ""
    for char in line:
        limit = 75 if not out else 74   # linie kontynuacji zaczynają się od spacji
        if len((current + char).encode("utf-8")) > limit:
            out.append(current)
            current = char
        else:
            current += char
    out.append(current)
    return "\r\n ".join(out)


def calendar_ics(leads):
    """Buduje plik kalendarza (.ics): każdy termin kontaktu to 15-minutowe wydarzenie.

    Plik ma wszystko, czego wymaga import w Google Calendar i Outlooku: METHOD, DTSTAMP,
    unikalny UID (z godziną terminu, więc przełożony kontakt to nowe wydarzenie) i zawijanie linii.
    """
    utc = dt_timezone.utc
    stamp = timezone.now().astimezone(utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Solid CRM//PL", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "X-WR-CALNAME:Solid CRM – kontakty", "X-WR-TIMEZONE:Europe/Warsaw",
    ]
    for lead in leads:
        start = lead.next_contact_at.astimezone(utc).strftime("%Y%m%dT%H%M%SZ")
        prefix = ICS_SUMMARY_PREFIX.get(lead.status, "Kontakt")
        description = f"Tel: {lead.phone}"
        if lead.next_contact_note:
            description += f"\nSprawa: {lead.next_contact_note}"
        if lead.contact_name and lead.contact_name != lead.display_name:
            description += f"\nOsoba: {lead.contact_name}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:lead-{lead.pk}-{start}@solidcrm",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start}",
            "DURATION:PT15M",
            f"SUMMARY:{_ics_escape(prefix + ': ' + lead.display_name)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-PT5M",
            f"DESCRIPTION:{_ics_escape(prefix)}",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


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


def sale_items_in_range(since, until=None, agent_ids=None):
    """Pozycje sprzedaży z rozmów w okresie (opcjonalnie tylko wskazanych agentów)."""
    items = SaleItem.objects.filter(activity__created_at__gte=since)
    if until is not None:
        items = items.filter(activity__created_at__lt=until)
    if agent_ids is not None:
        items = items.filter(activity__agent__in=agent_ids)
    return items


def sales_summary(since, until, agent_ids=None):
    """Przychód w okresie i rozbicie na produkty (najwyższy przychód na górze)."""
    items = sale_items_in_range(since, until, agent_ids)
    revenue = items.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    by_product = list(
        items.order_by()
        .values("product_name")
        .annotate(quantity=Sum("quantity"), revenue=Sum("total"), unit=Max("unit"))
        .order_by("-revenue", "product_name")
    )
    return revenue, by_product


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
            calls=Count("id", filter=~Q(outcome__in=NON_CALL_OUTCOMES)),
            answered=Count(
                "id", filter=~Q(outcome__in=[*NON_CALL_OUTCOMES, Outcome.NO_ANSWER])
            ),
            interested=Count("id", filter=Q(outcome=Outcome.INTERESTED)),
            refused=Count("id", filter=Q(outcome=Outcome.REFUSED)),
            sold=Count("id", filter=Q(outcome=Outcome.SOLD)),
            # jak kafelek „Wysłane oferty”: oferta albo zainteresowany (też dostaje ofertę)
            offers=Count("id", filter=Q(outcome__in=[Outcome.OFFER, Outcome.INTERESTED])),
            no_answer=Count("id", filter=Q(outcome=Outcome.NO_ANSWER)),
            callbacks=Count("id", filter=Q(outcome=Outcome.CALLBACK)),
            wrong_number=Count("id", filter=Q(outcome=Outcome.WRONG_NUMBER)),
        )
    )
    activity = {row["agent"]: row for row in activity_rows}

    # przychód ze sprzedaży w okresie (z pozycji sprzedaży rozmów danego agenta)
    revenue_rows = (
        sale_items_in_range(since, until, ids)
        .order_by()
        .values("activity__agent")
        .annotate(revenue=Sum("total"), commission=Sum("commission"))
    )
    revenue = {row["activity__agent"]: row["revenue"] for row in revenue_rows}
    commission = {row["activity__agent"]: row["commission"] for row in revenue_rows}

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
                "revenue": revenue.get(user.pk) or Decimal("0.00"),
                "commission": commission.get(user.pk) or Decimal("0.00"),
                "offers": a.get("offers", 0),
                # średnio ile rozmów potrzeba na jedną sprzedaż (None = jeszcze bez sprzedaży)
                "calls_per_sale": round(calls / a["sold"], 1) if a.get("sold") else None,
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
        outcome__in=NON_CALL_OUTCOMES
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
        outcome__in=NON_CALL_OUTCOMES
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


def team_users(since, until):
    """Osoby w statystykach zespołu: wszyscy agenci i każdy, kto w okresie dzwonił albo ma klientów."""
    active_ids = set(
        Activity.objects.filter(created_at__gte=since, created_at__lt=until)
        .exclude(agent=None)
        .values_list("agent", flat=True)
    ) | set(
        Lead.objects.exclude(owner=None).values_list("owner", flat=True)
    )
    return list(
        get_user_model().objects.filter(Q(groups__name=AGENT) | Q(pk__in=active_ids))
        .distinct()
        .order_by("username")
    )


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
        users = team_users(since, until)
        pool = Lead.objects.filter(owner__isnull=True, status=Lead.Status.NEW).count()
        agent_ids = None
    else:
        leads = Lead.objects.filter(owner=user)
        users = [user]
        pool = None
        agent_ids = [user.pk]

    revenue, by_product = sales_summary(since, until, agent_ids)
    agents = agent_stats(users, since, until)
    if manager:
        # minuty rozmów, czas pracy i cel każdej osoby – do tabeli „Wyniki agentów”
        from .work import team_minutes_report

        per_user = team_minutes_report(users, date_from, date_to)["per_user"]
        for row in agents:
            row.update(per_user.get(row["agent"], {}))
    return {
        "days": days,
        "date_from": date_from,
        "date_to": date_to,
        "revenue": revenue,
        "commission": sale_items_in_range(since, until, agent_ids).aggregate(c=Sum("commission"))["c"] or Decimal("0.00"),
        "sales_by_product": by_product,
        "total_leads": leads.count(),
        "pool_leads": pool,
        "leads_by_status": lead_funnel(leads),
        "outcomes": outcome_counts(since, until, agent_ids),
        "scheduled": scheduled_counts(leads),
        "agents": agents,
        "daily_calls": daily_calls(date_from, date_to, agent_ids),
        # rozbicie na godziny tylko dla okresu jednodniowego (np. "Dziś")
        "hourly_calls": hourly_calls(date_from, agent_ids) if date_from == date_to else [],
    }


LEADERBOARD_SORT_KEYS = ("sold", "calls", "interested", "revenue", "minutes", "commission")


def leaderboard(date_from, date_to, sort="sold", me_id=None):
    """Ranking agentów w okresie: tylko liczby wyników, bez żadnych danych klientów.

    Sortowanie malejąco wg `sort` (sold / calls / interested / revenue), remis rozstrzyga liczba
    rozmów. Agenci z takim samym wynikiem i taką samą liczbą rozmów mają to samo miejsce.
    """
    since, _ = day_bounds(date_from)
    _, until = day_bounds(date_to)
    from .models import CallRequest
    from .roles import MANAGER
    # agenci zawsze; managerowie też (wielu z nich dzwoni), chyba że manager wyłączył
    # „pokazuj w rankingu” (karta „Kto może widzieć wyniki innych” na stronie Ranking)
    hidden = AgentProfile.objects.filter(show_in_ranking=False).values_list("user_id", flat=True)
    users = list(
        get_user_model()
        .objects.filter(Q(groups__name=AGENT) | (Q(groups__name=MANAGER) & ~Q(pk__in=hidden)), is_active=True)
        .distinct()
        .order_by("username")
    )
    avatars = dict(
        AgentProfile.objects.filter(user__in=users).exclude(avatar="").values_list("user_id", "avatar")
    )
    from .models import CallRequest
    seconds = dict(
        CallRequest.objects.filter(user__in=users, state=CallRequest.State.DONE, answered=True,
                                   ended_at__gte=since, ended_at__lt=until)
        .order_by().values("user").annotate(s=Sum("duration_seconds")).values_list("user", "s")
    )
    commission = dict(
        sale_items_in_range(since, until, [u.pk for u in users]).order_by()
        .values("activity__agent").annotate(c=Sum("commission")).values_list("activity__agent", "c")
    )
    rows = [
        {
            "agent": row["agent"],
            "username": row["username"],
            "full_name": user.get_full_name() or user.username,
            "avatar": avatars.get(user.pk, ""),
            "sold": row["sold"],
            "revenue": row["revenue"],
            "calls": row["calls"],
            "answered": row["answered"],
            "interested": row["interested"],
            "answer_rate": row["answer_rate"],
            "minutes": round((seconds.get(user.pk) or 0) / 60, 2),
            "commission": commission.get(user.pk) or Decimal("0.00"),
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
        for pk, phone, alt, nip, krs in queryset.values_list("id", "phone", "phone_alt", "company__nip", "company__krs"):
            local = _local_phone(phone)
            if digits == local or (alt and digits == _local_phone(alt)):   # oddzwania z drugiego numeru
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
        .order_by("company__name", "contact_name")[:limit]
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
