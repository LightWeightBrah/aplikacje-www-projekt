from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Lead
from .roles import visible_to


@login_required
def lead_list(request):
    """Strona HTML z listą leadów (widok MVT, osobny od REST API)."""
    leads = visible_to(
        request.user, Lead.objects.select_related("product", "company", "owner")
    )
    return render(request, "crm/lead_list.html", {"leads": leads})