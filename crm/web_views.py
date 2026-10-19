from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Lead


@login_required
def lead_list(request):
    """Strona HTML z listą leadów (widok MVT, osobny od REST API)."""
    leads = Lead.objects.select_related("product", "company", "owner")
    return render(request, "crm/lead_list.html", {"leads": leads})