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


def queue_page(request):
    """Szkielet kolejki agenta. Dane pobiera JavaScript z API (token JWT)."""
    return render(request, "crm/queue.html")


def dashboard(request):
    """Szkielet dashboardu. Dane pobiera JavaScript z /api/stats/ (token JWT)."""
    return render(request, "crm/dashboard.html")


def manager_page(request):
    """Szkielet panelu managera. Dane pobiera JavaScript z API (token JWT)."""
    return render(request, "crm/manager.html")


def import_page(request):
    """Szkielet strony importu i eksportu Excela (tylko manager). Dane idą przez API."""
    return render(request, "crm/import.html")


def ranking_page(request):
    """Szkielet rankingu zespołu. Dane idą z /api/leaderboard/ (403, gdy manager wyłączył podgląd)."""
    return render(request, "crm/ranking.html")


def users_page(request):
    """Szkielet zarządzania użytkownikami (tylko manager). Dane idą z /api/users/."""
    return render(request, "crm/users.html")


def plan_page(request):
    """Szkielet planu dnia. Dane idą z /api/my-leads/schedule/ (token JWT)."""
    return render(request, "crm/plan.html")
