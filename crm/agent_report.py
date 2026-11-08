"""Raport „Wszystko po agencie” (Raporty → na dole strony, tylko manager).

Cała praca jednej osoby w wybranym okresie: dni i czas pracy, minuty rozmów, telefony, wyniki,
oferty, sprzedaż z prowizją, klienci, których teraz prowadzi (do przejęcia, gdy odejdzie)
i dziennik wszystkich rozmów. Działa też dla kont wyłączonych (agent już nie pracuje),
a dane można pobrać do Excela."""

from datetime import datetime
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import NON_CALL_OUTCOMES, Activity, Lead, Offer, SaleItem
from .roles import AGENT, MANAGER
from .services import day_bounds
from .work import segment_of, team_minutes_report

CALL_LOG_LIMIT = 500        # tyle ostatnich wpisów na stronie (w Excelu: wszystkie)
SEGMENT_LABELS = {"offer": "Po ofercie / zainteresowani", "callback": "Umówione oddzwonienia",
                  "retry": "Nie odebrali", "new": "Nowi (jeszcze bez telefonu)"}


def report_people():
    """Osoby do wyboru: agenci i managerowie, także wyłączeni (ci na końcu listy)."""
    users = (get_user_model().objects.filter(Q(groups__name__in=[AGENT, MANAGER]) | Q(activities__isnull=False))
             .distinct().order_by("-is_active", "first_name", "last_name", "username"))
    return [{"id": u.pk, "name": u.get_full_name() or u.username, "username": u.username, "active": u.is_active}
            for u in users]


def _local(t):
    return timezone.localtime(t).isoformat() if t else None


def agent_report(user, date_from, date_to, call_limit=CALL_LOG_LIMIT, client_limit=100):
    since, _ = day_bounds(date_from)
    _, until = day_bounds(date_to)
    acts = Activity.objects.filter(agent=user, created_at__gte=since, created_at__lt=until)
    calls = acts.exclude(outcome__in=NON_CALL_OUTCOMES)
    sales = SaleItem.objects.filter(activity__in=acts.filter(outcome=Activity.Outcome.SOLD))
    minutes = team_minutes_report([user], date_from, date_to)

    # dzień po dniu: czas pracy i minuty (work.py) + telefony, oferty, sprzedaż
    tz = timezone.get_current_timezone()
    per_day = {}
    for a in acts.only("created_at", "outcome"):
        d = per_day.setdefault(timezone.localtime(a.created_at, tz).date().isoformat(),
                               {"calls": 0, "talks": 0, "offers": 0, "sold": 0})
        if a.outcome in NON_CALL_OUTCOMES:
            continue
        d["calls"] += 1
        d["talks"] += a.outcome != Activity.Outcome.NO_ANSWER
        d["offers"] += a.outcome == Activity.Outcome.OFFER
        d["sold"] += a.outcome == Activity.Outcome.SOLD
    days = []
    for r in minutes["days"]:
        extra = per_day.get(r["date"], {"calls": 0, "talks": 0, "offers": 0, "sold": 0})
        if r["worked_minutes"] or r["minutes"] or extra["calls"]:
            days.append({**r, **extra})
    days.reverse()   # najnowszy dzień na górze

    outcome_names = dict(Activity.Outcome.choices)
    outcomes = [{"outcome": r["outcome"], "label": outcome_names.get(r["outcome"], r["outcome"]), "count": r["n"]}
                for r in acts.order_by().values("outcome").annotate(n=Count("id")).order_by("-n")]

    sale_rows = []
    for item in sales.select_related("activity__lead__company").order_by("-activity__created_at"):
        lead = item.activity.lead
        sale_rows.append({"at": _local(item.activity.created_at), "lead": lead.pk, "name": lead.display_name,
                          "product": item.product_name, "quantity": item.quantity, "term_months": item.term_months,
                          "promo": item.promo_label or ("tak" if item.promo else ""), "commission": item.commission})

    status_names = dict(Lead.Status.choices)
    offer_rows = [{"at": _local(o.created_at), "lead": o.lead_id, "name": o.lead.display_name,
                   "product": o.product_name if len(o.lines or []) < 2 else f"Zestaw: {len(o.lines)} usługi",
                   "monthly": o.monthly_total, "status": status_names.get(o.lead.status, o.lead.status),
                   "owner_now": (o.lead.owner.get_full_name() or o.lead.owner.username) if o.lead.owner else "pula"}
                  for o in Offer.objects.filter(agent=user, created_at__gte=since, created_at__lt=until)
                  .select_related("lead__company", "lead__owner").order_by("-created_at")]

    # klienci, których prowadzi TERAZ (niezależnie od okresu) – to ich trzeba przejąć, gdy odejdzie
    owned = list(Lead.objects.filter(owner=user).select_related("company").order_by("next_contact_at", "id"))
    groups = {}
    for lead in owned:
        groups.setdefault(segment_of(lead), []).append(lead)
    clients = [{"segment": key, "label": label, "count": len(groups.get(key, [])),
                "rows": [{"lead": x.pk, "name": x.display_name, "phone": x.phone, "next_contact_at": _local(x.next_contact_at),
                          "status": status_names.get(x.status, x.status)} for x in groups.get(key, [])[:client_limit]]}
               for key, label in SEGMENT_LABELS.items()]

    log = [{"at": _local(a.created_at), "lead": a.lead_id, "name": a.lead.display_name, "phone": a.lead.phone,
            "outcome": outcome_names.get(a.outcome, a.outcome), "seconds": a.call_seconds, "note": a.note}
           for a in acts.select_related("lead__company").order_by("-created_at")[:call_limit]]

    n_calls = calls.count()
    n_sold = calls.filter(outcome=Activity.Outcome.SOLD).count()
    return {
        "person": {"id": user.pk, "name": user.get_full_name() or user.username, "username": user.username,
                   "active": user.is_active, "joined": _local(user.date_joined), "last_login": _local(user.last_login)},
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "totals": {
            "days_worked": sum(1 for d in days if d["worked_minutes"]),
            "worked_minutes": minutes["worked_minutes"], "talk_minutes": minutes["minutes"], "goal": minutes["goal"],
            "calls": n_calls, "talks": calls.exclude(outcome=Activity.Outcome.NO_ANSWER).count(),
            "offers": calls.filter(outcome=Activity.Outcome.OFFER).count(), "sold": n_sold,
            "calls_per_sale": round(n_calls / n_sold) if n_sold else None,
            "commission": sales.aggregate(s=Sum("commission"))["s"] or Decimal("0.00"),
            "skipped": acts.filter(outcome=Activity.Outcome.SKIPPED).count(),
            "clients_now": len(owned),
        },
        "days": days, "outcomes": outcomes, "sales": sale_rows, "offers": offer_rows,
        "clients": clients, "log": log, "log_total": acts.count(),
    }


def agent_report_xlsx(user, date_from, date_to):
    """Ten sam raport w pliku Excel: osobny arkusz na podsumowanie, dni, sprzedaż, oferty, klientów i rozmowy."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    d = agent_report(user, date_from, date_to, call_limit=None, client_limit=None)
    wb = Workbook()
    bold = Font(bold=True)
    when = lambda iso: datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M") if iso else ""  # noqa: E731

    def sheet(title, header, rows, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(header)
        for c in ws[1]:
            c.font = bold
        for r in rows:
            ws.append(list(r))
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(60, max(10, *(len(str(c.value or "")) + 2 for c in col)))

    t, p = d["totals"], d["person"]
    sheet("Podsumowanie", ["", ""], [
        ("Osoba", f"{p['name']} ({p['username']})" + ("" if p["active"] else " – konto wyłączone")),
        ("Okres", f"{d['date_from']} – {d['date_to']}"),
        ("Dni pracy", t["days_worked"]), ("Czas pracy (min)", t["worked_minutes"]),
        ("Minuty rozmów", t["talk_minutes"]), ("Powinno być (min)", t["goal"]),
        ("Telefony", t["calls"]), ("Rozmowy (odebrane)", t["talks"]), ("Wysłane oferty", t["offers"]),
        ("Sprzedane", t["sold"]), ("Rozmów na 1 sprzedaż", t["calls_per_sale"] or ""),
        ("Prowizja (zł)", float(t["commission"])), ("Pominięte firmy", t["skipped"]),
        ("Klienci prowadzeni teraz", t["clients_now"]),
    ], first=True)
    sheet("Dni", ["Data", "Czas pracy (min)", "Minuty rozmów", "Powinno być", "Telefony", "Odebrane", "Oferty", "Sprzedane"],
          [(x["date"], x["worked_minutes"], x["minutes"], x["goal"], x["calls"], x["talks"], x["offers"], x["sold"]) for x in d["days"]])
    sheet("Sprzedaż", ["Kiedy", "Firma", "Usługa", "Ilość", "Umowa (mies.)", "Promocja", "Prowizja (zł)"],
          [(when(x["at"]), x["name"], x["product"], x["quantity"], x["term_months"] or "", x["promo"], float(x["commission"])) for x in d["sales"]])
    sheet("Oferty", ["Kiedy", "Firma", "Oferta", "Miesięcznie (zł)", "Status teraz", "Prowadzi teraz"],
          [(when(x["at"]), x["name"], x["product"], float(x["monthly"]), x["status"], x["owner_now"]) for x in d["offers"]])
    sheet("Klienci teraz", ["Grupa", "Firma", "Telefon", "Następny kontakt", "Status"],
          [(g["label"], x["name"], x["phone"], when(x["next_contact_at"]), x["status"]) for g in d["clients"] for x in g["rows"]])
    sheet("Rozmowy", ["Kiedy", "Firma", "Telefon", "Wynik", "Czas rozmowy (s)", "Komentarz"],
          [(when(x["at"]), x["name"], x["phone"], x["outcome"], x["seconds"] or "", x["note"]) for x in d["log"]])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
