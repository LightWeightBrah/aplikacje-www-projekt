"""Aktywacja urządzeń – „certyfikat” bez certyfikatów.

Na serwerze firmy (ustawienie DEVICE_LOCK) CRM otwiera się tylko na komputerach i telefonach,
które manager aktywował jednorazowym kodem. Obcy, który zna adres, widzi wyłącznie ekran
„To urządzenie nie ma dostępu” – bez logowania, bez API, bez panelu admina.

Jak to działa:
  1. manager klika „Nowy kod aktywacji” (albo na serwerze: manage.py device_code),
  2. na nowym urządzeniu wpisuje się kod i nazwę („Laptop Jana”),
  3. przeglądarka dostaje tajny klucz w ciasteczku `crm_device` (HttpOnly, tylko HTTPS),
  4. manager może urządzenie w każdej chwili zablokować.

Aplikacja Solid Dialer na telefonie ma własny token z parowania, więc jej adresy są wolne.
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone

COOKIE = "crm_device"
COOKIE_AGE = 2 * 365 * 24 * 3600          # 2 lata – potem aktywacja jeszcze raz
CODE_TTL = timedelta(hours=24)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # bez 0/O i 1/I – łatwo przepisać
SEEN_EVERY = timedelta(minutes=10)

# Bez aktywacji wolno tylko: ekran aktywacji, aplikacja dialera (ma własny token), plik aplikacji,
# pliki CSS/JS (lokalnie podaje je Django) i service worker.
OPEN_PATHS = ("/crm/activate/", "/api/device/activate/", "/crm/dialer.apk",
              "/api/dialer/pair/", "/api/dialer/poll/", "/api/dialer/calls/", "/api/dialer/sms/",
              "/crm/recordings/google/callback/", "/crm/privacy/",
              "/static/", "/favicon.ico")


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_code(code):
    return "".join(ch for ch in str(code).upper() if ch in CODE_ALPHABET)


def new_code(user=None):
    """Nowy jednorazowy kod, np. „K7Q4-M9XP”. Zwraca (kod, ważny_do). Kod widać tylko teraz."""
    from .models import ActivationCode

    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    expires = timezone.now() + CODE_TTL
    ActivationCode.objects.create(code_hash=_hash(raw), created_by=user, expires_at=expires)
    return f"{raw[:4]}-{raw[4:]}", expires


def activate(code, name):
    """Zużywa kod i zakłada urządzenie. Zwraca (urządzenie, tajny_klucz) albo None, gdy kod zły."""
    from django.db import transaction

    from .models import AccessDevice, ActivationCode

    raw = normalize_code(code)
    if len(raw) != 8:
        return None
    with transaction.atomic():
        found = (ActivationCode.objects.select_for_update()
                 .filter(code_hash=_hash(raw), used_at__isnull=True, expires_at__gt=timezone.now()).first())
        if not found:
            return None
        token = secrets.token_urlsafe(32)
        device = AccessDevice.objects.create(name=(name or "Urządzenie").strip()[:80],
                                             token_hash=_hash(token), created_by=found.created_by)
        found.used_at, found.device = timezone.now(), device
        found.save(update_fields=["used_at", "device"])
    return device, token


def set_device_cookie(response, token):
    response.set_cookie(COOKIE, token, max_age=COOKIE_AGE, httponly=True, samesite="Lax",
                        secure=not settings.DEBUG)


def device_for(request):
    """Aktywne urządzenie z ciasteczka albo None."""
    from .models import AccessDevice

    token = request.COOKIES.get(COOKIE)
    if not token:
        return None
    return AccessDevice.objects.filter(token_hash=_hash(token), revoked_at__isnull=True).first()


class DeviceLockMiddleware:
    """Wpuszcza tylko aktywowane urządzenia (gdy DEVICE_LOCK jest włączony – na serwerze firmy)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "DEVICE_LOCK", False) or request.path.startswith(OPEN_PATHS):
            return self.get_response(request)
        device = device_for(request)
        if device is None:
            if request.path.startswith("/api/"):
                response = JsonResponse({"detail": "To urządzenie nie ma dostępu do CRM. Poproś managera o kod aktywacji."},
                                        status=403)
                response["X-Device-Lock"] = "1"
                return response
            return redirect("/crm/activate/")
        now = timezone.now()
        if not device.last_seen or now - device.last_seen > SEEN_EVERY:
            type(device).objects.filter(pk=device.pk).update(last_seen=now)
        request.access_device = device
        return self.get_response(request)
