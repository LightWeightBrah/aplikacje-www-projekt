"""SMS z przypomnieniem przed umówionym telefonem.

Wysyła go aplikacja dialera na telefonie agenta, z jego karty SIM, tak jakby agent napisał
SMS ręcznie. Nie ma bramki SMS ani dodatkowych opłat, kosztuje tyle co zwykły SMS w abonamencie.
Telefon i tak co 2 sekundy pyta CRM „czy mam coś zrobić?” (/api/dialer/poll/). Gdy nie ma numeru
do wybrania, a zbliża się umówiony termin, CRM odpowiada SMS-em do wysłania. Dlatego SMS wychodzi
tylko wtedy, gdy telefon jest włączony i dialer działa. Gdy telefon włączy się jeszcze przed
terminem, SMS pójdzie wtedy.

Trzy rodzaje (każdy z własną treścią i włącznikiem w Ustawieniach), bo klient jest na innym etapie:
  - „po ofercie” (status oferta): wyliczenie ma już w skrzynce,
  - „po rozmowie” (zainteresowany / oddzwonić, wie, kto dzwoni),
  - „tylko termin” (oddzwonić, ale nie usłyszał jeszcze, w jakiej sprawie) – domyślnie wyłączony.

Zasady (wg RSN, moduł „No Show No Dough”: przypomnienie podnosi obecność o 10–20 pkt proc.):
  - SMS idzie `sms_reminder_minutes` przed terminem (ustawia manager, dowolnie od 10 min do 7 dni),
  - nie idzie, gdy termin umówiono dopiero przed chwilą (już w okienku przypomnienia),
    np. „proszę zadzwonić za pół godziny” przy przypomnieniu godzinę wcześniej,
  - nie idzie w nocy (przed SMS_FROM_HOUR i od SMS_TO_HOUR) ani na ostatnie minuty przed terminem,
  - jeden SMS na jeden termin; po przełożeniu terminu przed nowym idzie nowy.
Próbny SMS (Ustawienia → „Wyślij próbny SMS”) idzie z telefonu managera na wpisany numer.
"""

import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from .models import Activity, DialerDevice, Lead, SmsReminder
from .rules import SMS_MAX_PER_DAY, get_rules
from .services import PROMISED_STATUSES

SMS_FROM_HOUR = 8          # wcześniej SMS czeka (np. termin o 9:00, przypomnienie dzień wcześniej o 21:00 → 8:00)
SMS_TO_HOUR = 20
SMS_MIN_BEFORE = timedelta(minutes=5)   # na 5 minut przed telefonem SMS już nic nie daje
TEST_SMS_VALID = timedelta(minutes=10)  # próbny SMS czeka na telefon najwyżej tyle

WEEKDAYS = {1: "w poniedziałek", 2: "we wtorek", 3: "w środę", 4: "w czwartek",
            5: "w piątek", 6: "w sobotę", 7: "w niedzielę"}


def when_text(appointment, now=None):
    """„dziś o 14:00”, „jutro o 9:30”, „w piątek o 10:00” albo „14.11 o 10:00”."""
    now = timezone.localtime(now or timezone.now())
    local = timezone.localtime(appointment)
    hour = f"{local.hour}:{local.minute:02d}"
    days = (local.date() - now.date()).days
    if days <= 0:
        return f"dziś o {hour}"
    if days == 1:
        return f"jutro o {hour}"
    if days < 7:
        return f"{WEEKDAYS[local.isoweekday()]} o {hour}"
    return f"{local:%d.%m} o {hour}"


def render_sms(template, user, appointment, now=None):
    """Wstawia do treści imię agenta i termin. {Kiedy} = z wielkiej litery (na początku zdania)."""
    local = timezone.localtime(appointment)
    when = when_text(appointment, now)
    values = {
        "imie": user.first_name or user.get_full_name() or user.username,
        "kiedy": when,
        "Kiedy": when[:1].upper() + when[1:],
        "godzina": f"{local.hour}:{local.minute:02d}",
        "data": f"{local:%d.%m}",
    }
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return re.sub(r"\s+", " ", text).strip()


def phone_for_sms(raw):
    """Numer bez spacji i kresek (zostaje + na początku); pusty, gdy to nie wygląda na numer."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not 9 <= len(digits) <= 15:
        return ""
    return ("+" + digits) if raw.startswith("+") else digits


def kind_for(lead):
    """Który SMS dostaje klient: po ofercie, po rozmowie albo „tylko termin” (nie wie, kto dzwoni)."""
    if lead.status == Lead.Status.OFFER:
        return SmsReminder.Kind.OFFER
    if not lead.knows_caller:
        return SmsReminder.Kind.STRANGER
    return SmsReminder.Kind.TALKED


def kind_on(rules, kind):
    return bool(getattr(rules, f"sms_{kind}_enabled"))


def due_leads(user, now=None, rules=None):
    """Leady agenta, którym właśnie teraz należy się SMS z przypomnieniem (najbliższy termin pierwszy)."""
    now = now or timezone.now()
    rules = rules or get_rules()
    lead_time = timedelta(minutes=rules.sms_reminder_minutes)
    already = SmsReminder.objects.filter(lead=OuterRef("pk"), appointment_at=OuterRef("agreed_contact_at"))
    who = Q(pk__in=[])   # tylko włączone rodzaje SMS-ów
    if kind_on(rules, "offer"):
        who |= Q(status=Lead.Status.OFFER)
    if kind_on(rules, "talked"):
        who |= Q(knows_caller=True) & ~Q(status=Lead.Status.OFFER)
    if kind_on(rules, "stranger"):
        who |= Q(knows_caller=False) & ~Q(status=Lead.Status.OFFER)
    return (
        Lead.objects.filter(
            who,
            owner=user,
            status__in=PROMISED_STATUSES,
            agreed_contact_at__isnull=False,
            next_contact_at=F("agreed_contact_at"),
            agreed_contact_at__gt=now + SMS_MIN_BEFORE,
            agreed_contact_at__lte=now + lead_time,
        )
        .exclude(phone="")
        # termin umówiony wcześniej niż w okienku przypomnienia (a nie „za pół godziny” przed chwilą)
        .filter(Q(last_activity_at__isnull=True) | Q(last_activity_at__lte=F("agreed_contact_at") - lead_time))
        .annotate(sent=Exists(already))
        .filter(sent=False)
        .order_by("agreed_contact_at")
    )


def flood_block(phone, rules=None, now=None):
    """Zabezpieczenie przed serią SMS-ów na ten sam numer: powód blokady albo None.

    Liczą się SMS-y wysłane i właśnie wysyłane (także próbne), numer porównujemy po ostatnich
    9 cyfrach (600100200 i +48600100200 to ten sam numer)."""
    now = now or timezone.now()
    rules = rules or get_rules()
    tail = re.sub(r"\D", "", phone or "")[-9:]
    if not tail:
        return "brak numeru"
    recent = SmsReminder.objects.filter(
        phone__endswith=tail, state__in=(SmsReminder.State.SENDING, SmsReminder.State.SENT),
    )
    last = recent.filter(created_at__gte=now - timedelta(minutes=rules.sms_min_gap_minutes)).order_by("-created_at").first()
    if last:
        wait = rules.sms_min_gap_minutes - int((now - last.created_at).total_seconds() // 60)
        return f"na ten numer poszedł już SMS {timezone.localtime(last.created_at):%H:%M}, następny za {max(wait, 1)} min"
    if recent.filter(created_at__gte=now - timedelta(days=1)).count() >= SMS_MAX_PER_DAY:
        return f"na ten numer poszło już {SMS_MAX_PER_DAY} SMS-ów w ciągu doby"
    return None


def queue_test_sms(user, phone, template, minutes):
    """Próbny SMS z ustawień: telefon managera wyśle go przy najbliższym pytaniu (do 10 minut)."""
    now = timezone.now()
    appointment = now + timedelta(minutes=minutes)
    # poprzedni niewysłany próbny SMS zastępujemy nowym (żeby nie poszły dwa naraz)
    SmsReminder.objects.filter(user=user, kind=SmsReminder.Kind.TEST, state=SmsReminder.State.QUEUED).delete()
    return SmsReminder.objects.create(
        user=user, kind=SmsReminder.Kind.TEST, appointment_at=appointment, phone=phone,
        text=render_sms(template, user, appointment, now), state=SmsReminder.State.QUEUED,
    )


def has_phone(user):
    return DialerDevice.objects.filter(user=user).exists()


def _next_test_sms(device, now):
    with transaction.atomic():
        sms = (SmsReminder.objects.select_for_update()
               .filter(user=device.user, kind=SmsReminder.Kind.TEST, state=SmsReminder.State.QUEUED,
                       created_at__gte=now - TEST_SMS_VALID)
               .order_by("created_at").first())
        if sms:
            blocked = flood_block(sms.phone, now=now)
            sms.state = SmsReminder.State.FAILED if blocked else SmsReminder.State.SENDING
            sms.error = f"zablokowany: {blocked}" if blocked else ""
            sms.save(update_fields=["state", "error"])
            if blocked:
                return None
    return sms


def next_sms(device, now=None):
    """Telefon pyta o pracę: SMS do wysłania (SmsReminder w stanie „wysyła”) albo None."""
    now = now or timezone.now()
    test = _next_test_sms(device, now)
    if test:
        return test
    if not SMS_FROM_HOUR <= timezone.localtime(now).hour < SMS_TO_HOUR:
        return None
    rules = get_rules()
    for lead in due_leads(device.user, now, rules)[:3]:
        phone = phone_for_sms(lead.phone)
        if not phone or flood_block(phone, rules, now):
            continue   # zły numer albo na ten numer niedawno poszedł SMS (spróbujemy przy kolejnym pytaniu)
        kind = kind_for(lead)
        try:
            with transaction.atomic():
                return SmsReminder.objects.create(
                    user=device.user, lead=lead, kind=kind, appointment_at=lead.agreed_contact_at, phone=phone,
                    text=render_sms(getattr(rules, f"sms_{kind}_text"), device.user, lead.agreed_contact_at, now),
                )
        except IntegrityError:
            continue   # ten sam termin właśnie wziął drugi telefon agenta
    return None


def finish_sms(device, sms_id, ok, error=""):
    """Telefon odsyła, czy SMS wyszedł. W historii klienta zostaje wpis (widać treść i kto wysłał)."""
    sms = SmsReminder.objects.filter(pk=sms_id, user=device.user).select_related("lead").first()
    if sms is None:
        return None
    if sms.state != SmsReminder.State.SENDING:
        return sms
    sms.state = SmsReminder.State.SENT if ok else SmsReminder.State.FAILED
    sms.error = "" if ok else str(error or "telefon nie wysłał SMS-a")[:200]
    sms.sent_at = timezone.now()
    sms.save(update_fields=["state", "error", "sent_at"])
    if sms.lead_id:
        when = timezone.localtime(sms.appointment_at).strftime("%d.%m, %H:%M")
        note = (f"SMS z przypomnieniem o telefonie {when} wysłany z telefonu agenta: „{sms.text}”" if ok
                else f"Nie udało się wysłać SMS-a z przypomnieniem o telefonie {when} ({sms.error}).")
        Activity.objects.create(lead=sms.lead, agent=device.user, outcome=Activity.Outcome.NOTE, note=note)
    return sms
