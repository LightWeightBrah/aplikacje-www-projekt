"""API: telefon agenta (dialer 1.3+) zgłasza dzwonki, rejestr połączeń i SMS-y; CRM pokazuje je agentowi.

Szczegóły w crm/phone.py. Telefon przedstawia się nagłówkiem X-Dialer-Token (jak przy wybieraniu numerów).
"""

from django.http import Http404, HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AgentProfile, IncomingSms, SmsReminder
from .permissions import IsAgentOrManager
from .phone import (
    FILE_HOURS,
    file_for,
    live,
    log_calls,
    private_phone,
    queue_self_sms,
    receive_sms,
    ring,
    sms_row,
    store_file,
)
from .sms import TEST_SMS_VALID, has_phone, phone_for_sms
from .work_api import DialerDeviceAuthMixin


# ------------------------------------------------------------------ telefon → CRM

class DialerRingView(DialerDeviceAuthMixin, APIView):
    """Telefon dzwoni (połączenie przychodzące): {phone}. CRM pokaże agentowi, kto dzwoni."""

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        call = ring(request.auth, request.data.get("phone"))
        return Response({"ok": call is not None, "lead": call.lead_id if call else None})


class DialerCallLogView(DialerDeviceAuthMixin, APIView):
    """Połączenia z rejestru telefonu, których CRM nie zlecał: {calls: [{phone, type: in/out/missed, seconds, at}]}.

    `at` = początek połączenia w milisekundach (jak w rejestrze Androida). Zapisywane są tylko połączenia
    z numerami klientów z CRM."""

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        return Response({"saved": log_calls(request.auth, request.data.get("calls"))})


class DialerIncomingSmsView(DialerDeviceAuthMixin, APIView):
    """SMS przyszedł na telefon: {sender, text, at}. Zapisywany tylko od klienta z CRM / od sieci o kliencie."""

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        sms = receive_sms(request.auth, request.data.get("sender"), request.data.get("text"), request.data.get("at"))
        return Response({"saved": sms is not None})


# ------------------------------------------------------------------ CRM (przeglądarka agenta)

class PhoneLiveView(APIView):
    """Co dzieje się na telefonie: kto dzwoni, „numer klienta już dostępny”, nieodebrane od klientów, nowe SMS-y."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"paired": has_phone(request.user), **live(request.user)})


class PhoneSmsView(APIView):
    """SMS-y od klientów z ostatnich 30 dni (GET). POST {ids: [...]} albo {all: true} = przeczytane."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        rows = IncomingSms.objects.select_related("lead", "lead__company").filter(user=request.user)[:100]
        return Response({"results": [sms_row(s) for s in rows]})

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        mine = IncomingSms.objects.filter(user=request.user, seen=False)
        if request.data.get("all"):
            mine = mine.exclude(kind=IncomingSms.Kind.AVAILABLE)
        else:
            ids = [int(i) for i in request.data.get("ids") or [] if str(i).isdigit()]
            mine = mine.filter(pk__in=ids)
        return Response({"seen": mine.update(seen=True)})


class PhoneSelfView(APIView):
    """„Na mój telefon”: GET/PATCH prywatny numer; POST wysyła tekst i/lub plik (link) SMS-em na ten numer."""

    permission_classes = [IsAgentOrManager]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"private_phone": private_phone(request.user), "paired": has_phone(request.user)})

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def patch(self, request):
        raw = str(request.data.get("private_phone") or "").strip()[:30]
        if raw and not phone_for_sms(raw):
            return Response({"private_phone": ["Wpisz numer telefonu (9 cyfr)."]}, status=status.HTTP_400_BAD_REQUEST)
        AgentProfile.objects.update_or_create(user=request.user, defaults={"private_phone": raw})
        return Response({"private_phone": raw})

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        if not has_phone(request.user):
            return Response({"detail": "Najpierw połącz telefon z Solid Dialer (menu konta → Telefon)."},
                            status=status.HTTP_400_BAD_REQUEST)
        text = str(request.data.get("text") or "").strip()
        upload = request.FILES.get("file")
        try:
            if upload:
                shared = store_file(request.user, upload)
                link = request.build_absolute_uri(f"/crm/f/{shared.token}/")
                text = (text + "\n" if text else "") + f"Plik {shared.name}: {link} (ważny {FILE_HOURS} h)"
            sms = queue_self_sms(request.user, text)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"id": sms.pk, "state": sms.state, "phone": sms.phone}, status=status.HTTP_201_CREATED)


class PhoneSelfStateView(APIView):
    """Czy SMS „na mój telefon” już wyszedł (queued → sending → sent / failed / expired)."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="phone_self_state")
    def get(self, request, pk):
        sms = SmsReminder.objects.filter(pk=pk, user=request.user, kind=SmsReminder.Kind.SELF).first()
        if sms is None:
            return Response({"detail": "Nie ma takiego SMS-a."}, status=status.HTTP_404_NOT_FOUND)
        expired = sms.state == SmsReminder.State.QUEUED and sms.created_at < timezone.now() - TEST_SMS_VALID
        return Response({"id": sms.pk, "state": "expired" if expired else sms.state, "error": sms.error})


def shared_file(request, token):
    """Link z SMS-a „na mój telefon”: plik do pobrania przez 24 godziny (bez logowania – link jest losowy)."""
    shared = file_for(token)
    if shared is None:
        raise Http404("Link wygasł albo jest błędny.")
    type(shared).objects.filter(pk=shared.pk).update(downloads=shared.downloads + 1)
    response = HttpResponse(bytes(shared.data), content_type=shared.content_type or "application/octet-stream")
    from urllib.parse import quote
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(shared.name)}"
    response["X-Robots-Tag"] = "noindex"
    response["Cache-Control"] = "private, no-store"
    return response

