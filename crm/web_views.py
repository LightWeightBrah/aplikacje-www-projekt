from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
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
    from django.conf import settings
    # podpowiedź z kontami demo tylko lokalnie – na serwerze firmy jej nie ma
    return render(request, "crm/login.html", {"show_demo": not settings.IS_SERVER})


def privacy_page(request):
    """Polityka prywatności (publiczna, bez logowania) – Google wymaga jej adresu do połączenia Dysku."""
    return render(request, "crm/privacy.html")


def activate_page(request):
    """Ekran „To urządzenie nie ma dostępu” z polem na kod aktywacji (crm/device_lock.py)."""
    from django.conf import settings
    from django.shortcuts import redirect

    from .device_lock import device_for

    if not settings.DEVICE_LOCK or device_for(request):
        return redirect("/crm/login/")   # blokada wyłączona albo urządzenie już aktywne
    return render(request, "crm/activate.html")


def dialer_apk(request):
    """Aplikacja Solid Dialer do pobrania prosto na telefon: /crm/dialer.apk.

    Plik nie jest w repo (to zbudowany program): wrzuca się go na serwer do folderu
    `downloads/solid-dialer.apk` (instrukcja: docs/HOSTING.md).
    """
    from django.conf import settings
    from django.http import FileResponse, HttpResponse

    apk = settings.BASE_DIR / "downloads" / "solid-dialer.apk"
    if not apk.exists():
        return HttpResponse("Aplikacji Solid Dialer jeszcze tu nie ma – poproś managera.", status=404,
                            content_type="text/plain; charset=utf-8")
    return FileResponse(apk.open("rb"), as_attachment=True, filename="solid-dialer.apk",
                        content_type="application/vnd.android.package-archive")


def queue_page(request):
    """Szkielet kolejki agenta. Dane pobiera JavaScript z API (token JWT).

    Lista kanałów Magenta TV (publiczna lista T-Mobile) idzie razem ze stroną – wyszukiwarka
    „czy jest kanał…?” działa nawet wtedy, gdy w taryfach TV nie ma wpisanej ściągi."""
    from .tv_channels import CANAL_PLUS_SUPER_SPORT, CHANNEL_COUNTS, CHANNELS, CHANNELS_AS_OF

    tv = {"as_of": CHANNELS_AS_OF, "counts": CHANNEL_COUNTS, "extra": list(CANAL_PLUS_SUPER_SPORT),
          "packages": {plan: [{"group": line.split(":", 1)[0].strip(),
                               "channels": [c.strip() for c in line.split(":", 1)[1].split(",") if c.strip()]}
                              for line in lines]
                       for plan, lines in CHANNELS.items()}}
    return render(request, "crm/queue.html", {"tv_channels": tv})


def dashboard(request):
    """Szkielet dashboardu. Dane pobiera JavaScript z /api/stats/ (token JWT)."""
    return render(request, "crm/dashboard.html")


def manager_page(request):
    """Szkielet panelu managera. Dane pobiera JavaScript z API (token JWT)."""
    return render(request, "crm/manager.html")


def import_page(request):
    """Szkielet strony importu i eksportu Excela (tylko manager). Dane idą przez API."""
    return render(request, "crm/import.html")


def products_page(request):
    """Katalog produktów z cenami (tylko manager). Dane idą z /api/products/."""
    return render(request, "crm/products.html")


def settings_page(request):
    """Ustawienia zespołu (tylko manager): limity prób, ponowne próby, zwroty do puli. Dane z /api/settings/."""
    return render(request, "crm/settings.html")


def ranking_page(request):
    """Szkielet rankingu zespołu. Dane idą z /api/leaderboard/ (403, gdy manager wyłączył podgląd)."""
    return render(request, "crm/ranking.html")


def users_page(request):
    """Szkielet zarządzania użytkownikami (tylko manager). Dane idą z /api/users/."""
    return render(request, "crm/users.html")


def calendar_page(request):
    """Kalendarz miesięczny agenta. Dane z /api/my-leads/month/ (token JWT)."""
    return render(request, "crm/calendar.html")


def plan_page(request):
    """Szkielet planu dnia. Dane idą z /api/my-leads/schedule/ (token JWT)."""
    return render(request, "crm/plan.html")


def service_worker(request):
    """Service worker obsługujący kliknięcia powiadomień systemowych.

    Serwowany spod /crm/sw.js (a nie ze /static/), żeby miał zakres całej aplikacji /crm/.
    """
    code = (Path(__file__).parent / "static" / "crm" / "js" / "sw.js").read_text(
        encoding="utf-8"
    )
    response = HttpResponse(code, content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/crm/"
    response["Cache-Control"] = "no-cache"
    return response
