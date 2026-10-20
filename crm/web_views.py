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


def login_page(request):
    """Ekran logowania aplikacji. Logowanie robi JavaScript przez /api/token/."""
    return render(request, "crm/login.html")


def dashboard(request):
    """Szkielet dashboardu. Dane pobiera JavaScript z /api/stats/ (token JWT)."""
    return render(request, "crm/dashboard.html")
