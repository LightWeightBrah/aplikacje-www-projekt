import django_filters
from django.utils import timezone

from .models import Lead
from .services import ACTIVE_STATUSES


class LeadFilter(django_filters.FilterSet):
    """Filtry listy leadów. `pool=true` = tylko leady w puli (bez opiekuna)."""

    pool = django_filters.BooleanFilter(
        field_name="owner",
        lookup_expr="isnull",
        help_text="true = tylko leady w puli (bez opiekuna), false = tylko przypisane",
    )

    overdue = django_filters.BooleanFilter(
        method="filter_overdue",
        help_text="true = zaległe: aktywny lead, którego termin kontaktu już minął",
    )

    def filter_overdue(self, queryset, name, value):
        late = {"status__in": ACTIVE_STATUSES, "next_contact_at__lt": timezone.now()}
        if value:
            return queryset.filter(**late)
        return queryset.exclude(**late)

    class Meta:
        model = Lead
        fields = ["status", "owner", "batch", "product", "pool", "overdue"]
