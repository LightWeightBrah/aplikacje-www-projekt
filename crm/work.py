"""Praca agenta w Solid CRM: kolejka „Następny klient”, follow-upy, oferty i dialer.

Kolejność pracy (jak „work queue” w dużych CRM-ach: agent nie wybiera, system podaje
najważniejszy kontakt):
  1. oferty i zainteresowani z terminem, który właśnie mija (follow-up po ofercie),
  2. umówione oddzwonienia,
  3. ponowne próby do tych, którzy nie odebrali,
  4. nowe firmy: najpierw z bazy agenta, potem ze wspólnej puli.
"""

import secrets
from datetime import timedelta
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
GOAL_MINUTES_PER_HOUR = 15            # cel managera: min. 15 minut rozmów na godzinę pracy

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
    return [{"hour": h, "minutes": round(s / 60, 1)} for h, s in enumerate(seconds)]


def day_minutes(user, day):
    """Minuty rozmów jednego dnia i cel dzienny.

    Godziny pracy liczymy od pierwszego zapisu (rozmowa, notatka, telefon z dialera) do ostatniego,
    a dziś do teraz – zaokrąglone w górę do pełnych godzin. Cel = 15 min × godziny pracy, więc
    30 minut w jednej godzinie nadrabia słabszą godzinę obok.
    """
    start, end = day_bounds(day)
    marks = list(Activity.objects.filter(agent=user, created_at__gte=start, created_at__lt=end)
                 .values_list("created_at", flat=True))
    marks += list(CallRequest.objects.filter(user=user, created_at__gte=start, created_at__lt=end)
                  .values_list("created_at", flat=True))
    by_hour = minutes_by_hour(user, day)
    minutes = round(sum(h["minutes"] for h in by_hour), 1)
    hours = 0
    if marks:
        last = timezone.now() if day == timezone.localdate() else max(marks)
        span = (last - min(marks)).total_seconds()
        hours = max(1, -(-int(span) // 3600))   # zaokrąglenie w górę
    return {"date": day.isoformat(), "minutes": minutes, "hours": hours,
            "goal": hours * GOAL_MINUTES_PER_HOUR, "by_hour": by_hour}


def minutes_report(user, date_from, date_to):
    """Minuty rozmów w okresie: dzień po dniu (dla jednego dnia także godzina po godzinie)."""
    days = []
    day = date_from
    while day <= date_to:
        days.append(day_minutes(user, day))
        day += timedelta(days=1)
    total = round(sum(d["minutes"] for d in days), 1)
    goal = sum(d["goal"] for d in days)
    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "minutes": total, "goal": goal, "hours": sum(d["hours"] for d in days),
        "goal_minutes_per_hour": GOAL_MINUTES_PER_HOUR,
        "days": [{k: v for k, v in d.items() if k != "by_hour"} for d in days],
        "by_hour": days[0]["by_hour"] if len(days) == 1 else None,
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
        "minutes_today": round(sum(h["minutes"] for h in by_hour), 1),
        "minutes_this_hour": by_hour[hour_now]["minutes"],
        "minutes_by_hour": by_hour,
        "goal_minutes_per_hour": GOAL_MINUTES_PER_HOUR,
        "dialer_used": dialer_used,
        "day_goal": day_minutes(user, timezone.localdate())["goal"],
    }


# ------------------------------------------------------------------ oferty

def _money(value):
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " zł"


def offer_numbers(product, quantity, term, promo, config=None):
    """(zobowiązanie miesięczne, prowizja) dla wybranej taryfy."""
    config = config or OfferConfig.get()
    monthly = product.monthly_total(quantity, promo)
    return monthly, (monthly * config.multiplier(term)).quantize(Decimal("0.01"))


def build_offer(config, lead, agent, product, quantity, term, promo, client_email):
    """Temat i treść oferty. W trybie „forward” na górze jest notka dla osoby, która ją przekaże."""
    monthly, _ = offer_numbers(product, quantity, term, promo, config)
    company = lead.display_name
    numbers = "1 numer" if quantity == 1 else f"{quantity} numery" if 2 <= quantity % 10 <= 4 and not 12 <= quantity % 100 <= 14 else f"{quantity} numerów"
    lines = [config.greeting.strip(), "", f"OFERTA: {product.name}", f"Liczba numerów: {quantity}",
             f"Okres umowy: {term} miesięcy"]
    if promo and config.promo_label:
        lines.append(f"Promocja: {config.promo_label}")
    lines.append(f"Całkowite zobowiązanie miesięczne za {numbers}: {_money(monthly)} netto")
    if quantity > 1:
        lines.append(f"Cena za jeden numer: {_money(monthly / quantity)} netto")
    features = [f.strip() for f in product.features.splitlines() if f.strip()]
    if features:
        lines += ["", "W ofercie:"] + [f"• {f}" for f in features]
    agent_name = agent.get_full_name() or agent.username
    lines += ["", config.closing.strip(), "", "Z poważaniem,", agent_name]
    body = "\n".join(lines).strip() + "\n"

    if config.mode == OfferConfig.Mode.FORWARD:
        company_line = company + (f", NIP {lead.company.nip}" if lead.company and lead.company.nip else "")
        note = (
            f"Proszę o wysłanie oferty do klienta.\n"
            f"Adres klienta: {client_email or '(brak – trzeba dopytać)'}\n"
            f"Firma: {company_line}\nTelefon: {lead.phone}\nAgent: {agent_name}\n"
            f"{'-' * 40}\n\n"
        )
        return f"Oferta do wysłania: {company} → {client_email or 'brak e-maila'}", note + body
    return f"Oferta T-Mobile dla {company}", body


def _numbers_word(quantity):
    if quantity == 1:
        return "1 numer"
    if 2 <= quantity % 10 <= 4 and not 12 <= quantity % 100 <= 14:
        return f"{quantity} numery"
    return f"{quantity} numerów"


def _term_words(term):
    return "24 miesiące" if int(term) == 24 else f"{term} miesięcy"


OFFER_MAGENTA = "#E20074"


def offer_letter_html(config, product, quantity, term, promo):
    """List do klienta w układzie wzornika ofert (sam fragment, style inline: działa w każdej poczcie)."""
    monthly, _ = offer_numbers(product, quantity, term, promo, config)
    p = 'style="margin:0 0 14px"'
    greeting = [escape(x) for x in config.greeting.strip().split("\n\n")] if config.greeting.strip() else []
    parts = [f"<p {p}>{line.replace(chr(10), '<br>')}</p>" for line in greeting]
    parts.append(
        f'<div style="color:{OFFER_MAGENTA};font-size:20px;font-weight:800;margin:18px 0 12px">'
        f"Oferta: {escape(product.name)}</div>"
    )
    parts.append(f"<p {p}><strong>Okres trwania umowy:</strong> {_term_words(term)}</p>")
    if promo and config.promo_label:
        parts.append(f'<p style="margin:0 0 12px;font-weight:800;color:{OFFER_MAGENTA}">Promocja: {escape(config.promo_label)}</p>')
    parts.append(
        f'<p style="margin:0 0 12px;font-weight:700">Całkowite zobowiązanie miesięczne za {_numbers_word(quantity)}: '
        f"{_money(monthly)} netto</p>"
    )
    if quantity > 1:
        parts.append(f'<p style="margin:0 0 12px;font-weight:700">Cena za jeden numer: {_money(monthly / quantity)} netto</p>')
    items = []
    for feature in (f.strip() for f in product.features.splitlines()):
        if not feature:
            continue
        head, sep, rest = feature.partition(":")
        text = f"<strong>{escape(head)}:</strong>{escape(rest)}" if sep and rest.strip() else escape(feature)
        items.append(f'<li style="margin:5px 0">{text}</li>')
    if items:
        parts.append(f'<ul style="margin:8px 0 18px;padding-left:22px">{"".join(items)}</ul>')
    if config.closing.strip():
        parts.append(f"<p {p}>{escape(config.closing.strip())}</p>")
    if config.contact_phone:
        parts.append(f"<p {p}><strong>Tel.:</strong> {escape(config.contact_phone)}</p>")
    parts.append(f'<p style="margin:22px 0 0">Z poważaniem,<br>{escape(config.signature or "")}</p>')
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#25212a;line-height:1.65;font-size:15px;max-width:720px">'
        + "".join(parts) + "</div>"
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
        "<b>Proszę o wysłanie oferty do klienta</b> (gotowa oferta jest niżej i w załączniku).<br>"
        f"Adres klienta: {email}<br>Firma: {company}{nip}<br>Telefon: {escape(lead.phone)}<br>Agent: {agent_name}</div>"
    )


def offer_filename(lead):
    base = slugify(lead.display_name)[:50] or f"klient-{lead.pk}"
    return f"oferta-{base}.html"


class OfferError(Exception):
    pass


def create_offer(lead, agent, product, quantity, term, promo, client_email, next_contact_at,
                 next_contact_note=None, note=""):
    """Zapisuje rozmowę „Wysłana oferta” z terminem follow-upu, ofertę i (jeśli się da) wysyła e-mail.

    Zwraca (offer, mailto) – mailto to gotowa wiadomość do otwarcia w programie pocztowym,
    gdy system nie wysłał jej sam (brak serwera poczty).
    """
    config = OfferConfig.get()
    if config.mode == OfferConfig.Mode.DIRECT and not client_email:
        raise OfferError("Podaj e-mail klienta: oferta idzie od razu do niego.")
    monthly, commission = offer_numbers(product, quantity, term, promo, config)
    subject, body = build_offer(config, lead, agent, product, quantity, term, promo, client_email)
    recipient = client_email if config.mode == OfferConfig.Mode.DIRECT else config.forward_email
    letter = offer_letter_html(config, product, quantity, term, promo)
    with transaction.atomic():
        if client_email and client_email != lead.email:
            Lead.objects.filter(pk=lead.pk).update(email=client_email)
        activity = register_activity(
            lead, agent, Activity.Outcome.OFFER, note or f"Oferta {product.name} ×{quantity}, {term} mies.",
            next_contact_at=next_contact_at, next_contact_note=next_contact_note,
        )
        offer = Offer.objects.create(
            lead=lead, agent=agent, activity=activity, product=product, product_name=product.name,
            quantity=quantity, term_months=term, promo=promo, monthly_total=monthly, commission=commission,
            client_email=client_email, recipient=recipient or "", subject=subject, body=body, html=letter,
        )
    if recipient and getattr(settings, "OFFER_EMAIL_ENABLED", False):
        # Mail = sformatowana oferta (w trybie „forward” z ramką „komu przekazać” na górze)
        # + załącznik oferta-….html z samym listem, gotowy do przesłania klientowi.
        top = forward_note_html(lead, agent, client_email) if config.mode == OfferConfig.Mode.FORWARD else ""
        message = EmailMultiAlternatives(subject, body, to=[recipient], reply_to=[agent.email] if agent.email else None)
        message.attach_alternative(offer_document(top + letter, subject), "text/html")
        message.attach(offer_filename(lead), offer_document(letter, f"Oferta: {product.name}"), "text/html")
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
    return {r["user"]: round((r["s"] or 0) / 60, 1) for r in rows}
