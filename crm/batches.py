"""Bazy leadów (importy z Excela): podsumowanie dla managera i „Usuń tę bazę”.

Usuwamy tylko firmy, do których nikt jeszcze nie dzwonił (bez rozmów, ofert, telefonów i nagrań).
Firmy z historią pracy zostają w CRM – manager ma widzieć całą pracę agentów – tylko przestają
należeć do usuwanej bazy."""

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q

from .models import Activity, CallRecording, CallRequest, Company, Lead, LeadBatch, Offer


def untouched(qs):
    """Leady, których nikt nie ruszył: status „Nowy”, 0 prób i żadnych śladów pracy."""
    return (qs.filter(status=Lead.Status.NEW, attempts_count=0)
            .exclude(Exists(Activity.objects.filter(lead=OuterRef("pk"))))
            .exclude(Exists(Offer.objects.filter(lead=OuterRef("pk"))))
            .exclude(Exists(CallRequest.objects.filter(lead=OuterRef("pk"))))
            .exclude(Exists(CallRecording.objects.filter(lead=OuterRef("pk")))))


def overview():
    """Lista baz (najnowsze na górze): ile firm, ile jeszcze bez telefonu (te da się usunąć)."""
    batches = (LeadBatch.objects.select_related("assigned_to")
               .annotate(total=Count("leads")).order_by("-created_at"))
    fresh = dict(untouched(Lead.objects.filter(batch__isnull=False))
                 .values_list("batch").annotate(n=Count("id")).values_list("batch", "n"))
    out = []
    for b in batches:
        who = b.assigned_to
        out.append({
            "id": b.id, "name": b.name, "created_at": b.created_at,
            "assigned_to": who.id if who else None,
            "assigned_to_name": (who.get_full_name() or who.username) if who else "",
            "total": b.total, "untouched": fresh.get(b.id, 0),
        })
    return out


@transaction.atomic
def remove_batch(batch):
    """Usuwa bazę: nieruszone leady znikają, leady z historią zostają (bez bazy)."""
    leads = Lead.objects.filter(batch=batch)
    gone = untouched(leads)
    company_ids = set(gone.exclude(company=None).values_list("company_id", flat=True))
    removed = gone.count()
    Lead.objects.filter(pk__in=list(gone.values_list("pk", flat=True))).delete()
    kept = leads.update(batch=None)
    # firmy, które zostały bez żadnego leada (tworzy je import), też sprzątamy
    Company.objects.filter(pk__in=company_ids).filter(~Q(Exists(Lead.objects.filter(company=OuterRef("pk"))))).delete()
    name = batch.name
    batch.delete()
    return {"name": name, "removed": removed, "kept": kept}
