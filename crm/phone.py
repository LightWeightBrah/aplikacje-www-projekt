"""Co dzieje się na telefonie agenta (dialer 1.3+), a CRM o tym nie decydował.

1. Kto dzwoni: gdy na telefon dzwoni klient, dialer od razu zgłasza numer (ring). CRM szuka klienta
   i pokazuje agentowi pasek „Dzwoni: Firma X” z przyciskiem „Otwórz kartę” (karta NIE przełącza się
   sama, gdy agent właśnie coś wpisuje u innego klienta).
2. Rejestr połączeń: po rozmowie dialer odsyła połączenia z rejestru telefonu, których CRM nie zlecał
   (klient oddzwonił, agent wybrał numer ręcznie). Zapisujemy TYLKO połączenia z numerami klientów z CRM
   – prywatne rozmowy nie trafiają do CRM. Rozmowy z klientami liczą się do minut rozmów.
3. SMS-y: dialer odsyła przychodzące SMS-y. Zapisujemy tylko te od klientów z CRM i wiadomości od sieci
   o numerze klienta („numer jest już dostępny” – można dzwonić). Reszta (prywatne, reklamy) przepada.
4. „Na mój telefon”: agent wysyła sobie z komputera tekst albo plik (link ważny 24 h) SMS-em na prywatny numer.
"""

import re
import secrets
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import NON_CALL_OUTCOMES, Activity, AgentProfile, CallRequest, IncomingSms, Lead, SharedFile, SmsReminder
from .roles import visible_to
from .services import PROMISED_STATUSES, _local_phone

RING_MINUTES = 60            # tyle najdłużej trwa „dzwoni / rozmowa” bez wpisu z rejestru (potem pasek znika)
SMS_KEEP_DAYS = 30           # SMS-y od klientów kasujemy po miesiącu
FILE_HOURS = 24              # plik „na mój telefon” czeka tyle godzin
FILE_MAX_BYTES = 10 * 1024 * 1024
SELF_SMS_MAX = 600           # długość tekstu „na mój telefon” (kilka zwykłych SMS-ów)
SAME_CALL = timedelta(seconds=120)   # wpis z rejestru ≈ połączenie zlecone z CRM / zgłoszony dzwonek

# Wiadomość od sieci, że numer, pod który dzwoniłeś, już jest dostępny (różne sieci piszą różnie)
AVAILABLE_WORDS = re.compile(r"dost[eę]pn|osi[aą]galn|zasi[eę]g|w[lł][aą]czy[lł]|aktywn|available", re.IGNORECASE)
NUMBER_IN_TEXT = re.compile(r"(?<!\d)(?:\+?48[\s-]?)?(\d{3}[\s-]?\d{3}[\s-]?\d{3})(?!\d)")
EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def match_lead(user, phone):
    """Klient z CRM z tym numerem (główny albo dodatkowy): najpierw własny klient agenta. None = brak."""
    digits = _local_phone(phone)
    if len(digits) < 9:
        return None
    other = None
    rows = visible_to(user, Lead.objects.all(), "owner").values_list("id", "phone", "phone_alt", "owner_id")
    for pk, main, alt, owner in rows.iterator():
        if _local_phone(main) == digits or (alt and _local_phone(alt) == digits):
            if owner == user.pk:
                return Lead.objects.select_related("company").get(pk=pk)
            other = other or pk
    return Lead.objects.select_related("company").get(pk=other) if other else None


def _same_number(a, b):
    return _local_phone(a)[-9:] == _local_phone(b)[-9:]


def _from_ms(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return timezone.now()


# ------------------------------------------------------------------ dzwoni klient

def ring(device, phone):
    """Telefon agenta właśnie dzwoni (przychodzące): zapisujemy, żeby CRM mógł pokazać, kto to."""
    phone = str(phone or "").strip()[:30]
    if len(_local_phone(phone)) < 9:
        return None
    user, now = device.user, timezone.now()
    # poprzedni dzwonek bez wpisu z rejestru (np. telefon stracił internet) już nie dzwoni
    CallRequest.objects.filter(user=user, direction=CallRequest.Direction.IN, state=CallRequest.State.DIALING,
                               lead__isnull=True).delete()
    CallRequest.objects.filter(user=user, direction=CallRequest.Direction.IN,
                               state=CallRequest.State.DIALING).update(state=CallRequest.State.CANCELLED)
    return CallRequest.objects.create(
        user=user, lead=match_lead(user, phone), phone=phone, state=CallRequest.State.DIALING,
        direction=CallRequest.Direction.IN, from_phone=True, started_at=now,
    )


def _ring_for(user, phone, at):
    for call in CallRequest.objects.filter(
        user=user, direction=CallRequest.Direction.IN, state__in=(CallRequest.State.DIALING, CallRequest.State.CANCELLED),
        from_phone=True, started_at__gte=at - SAME_CALL, started_at__lte=at + SAME_CALL,
    ):
        if _same_number(call.phone, phone):
            return call
    return None


# ------------------------------------------------------------------ rejestr połączeń telefonu

def _talk(seconds):
    return f"{seconds // 60} min {seconds % 60} s"


def log_calls(device, entries):
    """Połączenia z rejestru telefonu, których CRM nie zlecał: [{phone, type: in/out/missed, seconds, at (ms)}].

    Zwraca, ile zapisano. Połączenia z numerami spoza CRM są pomijane (prywatne)."""
    user, saved = device.user, 0
    for entry in entries[:50] if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        phone = str(entry.get("phone") or "").strip()[:30]
        kind = entry.get("type")
        if kind not in ("in", "out", "missed") or len(_local_phone(phone)) < 9:
            continue
        try:
            seconds = max(0, min(int(entry.get("seconds") or 0), 6 * 3600))
        except (TypeError, ValueError):
            seconds = 0
        at = _from_ms(entry.get("at"))
        direction = CallRequest.Direction.OUT if kind == "out" else CallRequest.Direction.IN
        answered = kind != "missed" and seconds > 0
        with transaction.atomic():
            ringing = _ring_for(user, phone, at) if direction == CallRequest.Direction.IN else None
            lead = ringing.lead if ringing and ringing.lead_id else match_lead(user, phone)
            if lead is None:
                if ringing:
                    ringing.delete()   # nieznany numer: nic nie zostaje w CRM
                continue
            if direction == CallRequest.Direction.OUT:
                # wybrane z CRM (przycisk „Zadzwoń”) – to połączenie ma już wynik od dialera
                ordered = CallRequest.objects.filter(
                    user=user, direction=CallRequest.Direction.OUT, from_phone=False,
                    started_at__gte=at - SAME_CALL, started_at__lte=at + SAME_CALL)
                if any(_same_number(c.phone, phone) for c in ordered):
                    continue
            if CallRequest.objects.filter(user=user, from_phone=True, direction=direction, started_at=at,
                                          state=CallRequest.State.DONE).exists():
                continue   # ten wpis już był (telefon wysłał go drugi raz)
            call = ringing or CallRequest(user=user, phone=phone, direction=direction, from_phone=True)
            call.lead, call.state, call.answered = lead, CallRequest.State.DONE, answered
            call.duration_seconds = seconds if answered else 0
            call.started_at, call.ended_at = at, at + timedelta(seconds=seconds)
            call.save()
            saved += 1
            if answered:
                # wynik rozmowy zapisany w trakcie (agent był szybszy niż rejestr) dostaje czas rozmowy
                act = (Activity.objects.filter(lead=lead, agent=user, call_seconds__isnull=True, created_at__gte=at)
                       .exclude(outcome__in=NON_CALL_OUTCOMES).order_by("created_at").first())
                if act and act.outcome != Activity.Outcome.NO_ANSWER:
                    Activity.objects.filter(pk=act.pk).update(call_seconds=seconds)
            if direction == CallRequest.Direction.IN:
                hour = timezone.localtime(at).strftime("%H:%M")
                note = (f"Klient zadzwonił do Ciebie o {hour}, rozmowa {_talk(seconds)}." if answered
                        else f"Klient dzwonił do Ciebie o {hour} – nieodebrane. Oddzwoń.")
                # nieodebrany telefon od klienta to nie praca agenta (np. wieczorem) – wpis bez autora („telefon”)
                Activity.objects.create(lead=lead, agent=user if answered else None, outcome=Activity.Outcome.NOTE, note=note)
    return saved


# ------------------------------------------------------------------ SMS-y przychodzące

def _leads_in_text(user, text):
    for found in NUMBER_IN_TEXT.findall(text or ""):
        lead = match_lead(user, found)
        if lead:
            yield lead


def receive_sms(device, sender, text, at_ms=None):
    """SMS na telefonie agenta: zapisujemy tylko od klienta z CRM albo od sieci o numerze klienta."""
    user = device.user
    sender, text = str(sender or "").strip()[:40], str(text or "").strip()[:2000]
    if not sender or not text:
        return None
    at = _from_ms(at_ms) if at_ms else timezone.now()
    IncomingSms.objects.filter(created_at__lt=timezone.now() - timedelta(days=SMS_KEEP_DAYS)).delete()
    lead, kind = None, IncomingSms.Kind.CLIENT
    if len(_local_phone(sender)) >= 9:
        lead = match_lead(user, sender)
    if lead is None:
        lead = next(iter(_leads_in_text(user, text)), None)
        kind = IncomingSms.Kind.AVAILABLE if AVAILABLE_WORDS.search(text) else IncomingSms.Kind.INFO
    if lead is None:
        return None
    try:
        with transaction.atomic():
            sms = IncomingSms.objects.create(user=user, lead=lead, kind=kind, sender=sender, text=text, received_at=at)
    except IntegrityError:
        return None   # ten sam SMS drugi raz
    hour = timezone.localtime(at).strftime("%H:%M")
    note = {IncomingSms.Kind.CLIENT: f"SMS od klienta ({hour}): „{text}”",
            IncomingSms.Kind.AVAILABLE: f"Sieć ({hour}): numer klienta jest już dostępny – można dzwonić. „{text}”",
            IncomingSms.Kind.INFO: f"SMS od sieci o numerze klienta ({hour}): „{text}”"}[kind]
    # wpis bez autora: SMS przychodzi także poza pracą, a wpis agenta liczyłby się jako praca
    Activity.objects.create(lead=lead, agent=None, outcome=Activity.Outcome.NOTE, note=note[:2000])
    return sms


def sms_row(sms):
    lead = sms.lead
    emails = EMAIL_IN_TEXT.findall(sms.text)
    return {
        "id": sms.pk, "kind": sms.kind, "sender": sms.sender, "text": sms.text, "seen": sms.seen,
        "received_at": sms.received_at, "lead": lead.pk, "name": lead.display_name, "status": lead.status,
        "phone": lead.phone, "lead_email": lead.email,
        # e-mail w treści, którego klient nie ma jeszcze w karcie („wyślę maila SMS-em”)
        "email": next((e for e in emails if e.lower() != (lead.email or "").lower()), ""),
    }


# ------------------------------------------------------------------ co pokazać agentowi teraz

def live(user):
    """Dla paska „Dzwoni…”, kart „numer już dostępny” / „klient dzwonił” i licznika SMS-ów."""
    now = timezone.now()
    ring_call = (CallRequest.objects.select_related("lead", "lead__company")
                 .filter(user=user, direction=CallRequest.Direction.IN, state=CallRequest.State.DIALING,
                         started_at__gte=now - timedelta(minutes=RING_MINUTES)).order_by("-started_at").first())
    ringing = None
    if ring_call:
        lead = ring_call.lead
        ringing = {"id": ring_call.pk, "phone": ring_call.phone, "since": ring_call.started_at,
                   "lead": lead.pk if lead else None, "name": lead.display_name if lead else "",
                   "status": lead.status if lead else "", "note": lead.next_contact_note if lead else ""}
    since = now - timedelta(hours=8)
    # karta „już dostępny” tylko dla klientów, z którymi coś umówiono (oddzwonienie, zainteresowany, oferta) –
    # przy nowych firmach z bazy to tylko szum; same SMS-y od sieci są dalej w zakładce SMS („Wszystkie”)
    available = []
    for sms in IncomingSms.objects.select_related("lead", "lead__company").filter(
            user=user, kind=IncomingSms.Kind.AVAILABLE, seen=False, received_at__gte=since,
            lead__status__in=PROMISED_STATUSES)[:5]:
        available.append({**sms_row(sms), **why_now(user, sms.lead)})
    missed = []
    for call in (CallRequest.objects.select_related("lead", "lead__company")
                 .filter(user=user, direction=CallRequest.Direction.IN, from_phone=True, state=CallRequest.State.DONE,
                         answered=False, lead__isnull=False, ended_at__gte=since).order_by("-ended_at")[:5]):
        # już oddzwonił / rozmawiał z tym klientem po tym telefonie – karta niepotrzebna
        later = CallRequest.objects.filter(user=user, lead=call.lead, state=CallRequest.State.DONE,
                                           ended_at__gt=call.ended_at).exclude(pk=call.pk).exists()
        if not later:
            missed.append({"id": call.pk, "lead": call.lead_id, "name": call.lead.display_name,
                           "phone": call.lead.phone, "at": call.started_at, "status": call.lead.status})
    return {
        "ringing": ringing, "available": available, "missed": missed,
        "sms_unseen": IncomingSms.objects.filter(user=user, seen=False).exclude(kind=IncomingSms.Kind.AVAILABLE).count(),
    }


def why_now(user, lead):
    """Dlaczego ten klient jest ważny: umówiony termin, sprawa i ostatnia próba dodzwonienia się do niego."""
    call = (CallRequest.objects.filter(user=user, lead=lead, direction=CallRequest.Direction.OUT,
                                       state=CallRequest.State.DONE).order_by("-ended_at").first())
    return {
        "agreed_at": lead.agreed_contact_at or lead.next_contact_at,
        "note": lead.next_contact_note,
        "missed_calls": lead.missed_calls,
        "last_call_at": (call.started_at or call.ended_at) if call else None,
        "last_call_answered": call.answered if call else None,
    }


# ------------------------------------------------------------------ „na mój telefon”

def private_phone(user):
    profile = AgentProfile.objects.filter(user=user).only("private_phone").first()
    return profile.private_phone if profile else ""


def queue_self_sms(user, text):
    """SMS z tekstem na prywatny numer agenta: telefon z dialerem wyśle go przy najbliższym pytaniu."""
    from .sms import phone_for_sms

    phone = phone_for_sms(private_phone(user))
    if not phone:
        raise ValueError("Najpierw wpisz swój prywatny numer.")
    text = str(text or "").strip()
    if not text:
        raise ValueError("Wpisz tekst albo wybierz plik.")
    return SmsReminder.objects.create(user=user, kind=SmsReminder.Kind.SELF, appointment_at=timezone.now(),
                                      phone=phone, text=text[:SELF_SMS_MAX], state=SmsReminder.State.QUEUED)


def store_file(user, upload):
    """Plik do pobrania na telefonie (link w SMS-ie, ważny FILE_HOURS godzin). Stare pliki znikają."""
    SharedFile.objects.filter(created_at__lt=timezone.now() - timedelta(hours=FILE_HOURS)).delete()
    if upload.size > FILE_MAX_BYTES:
        raise ValueError(f"Plik jest za duży (najwyżej {FILE_MAX_BYTES // (1024 * 1024)} MB).")
    data = upload.read()
    name = re.sub(r"[\\/\r\n\"]", "_", upload.name or "plik")[:200]
    return SharedFile.objects.create(user=user, name=name, content_type=(upload.content_type or "")[:100],
                                     data=data, size=len(data), token=secrets.token_urlsafe(18))


def file_for(token):
    return SharedFile.objects.filter(token=token, created_at__gte=timezone.now() - timedelta(hours=FILE_HOURS)).first()
