"""API nagrywania rozmów: ustawienia (manager), łączenie Dysku Google, wysyłka z telefonu i odsłuch."""

from urllib.parse import urlencode

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CallRecording, DialerDevice, RecordingConfig
from .permissions import IsManager
from .recordings import (
    DriveError,
    auth_url,
    connect,
    disconnect,
    download_file,
    oauth_configured,
    save_recording,
    status_for,
    user_from_state,
)
from .roles import is_manager
from .work_api import DialerDeviceAuthMixin


class RecordingSettingsSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    retention_days = serializers.IntegerField(min_value=7, max_value=3650, required=False)


class RecordingSettingsView(APIView):
    """Manager: czy nagrywać rozmowy i po ilu dniach usuwać nagrania; stan połączenia z Dyskiem Google."""

    permission_classes = [IsManager]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(self._payload(RecordingConfig.get()))

    @extend_schema(request=RecordingSettingsSerializer, responses=OpenApiTypes.OBJECT)
    def patch(self, request):
        data = RecordingSettingsSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        config = RecordingConfig.get()
        for key, value in data.validated_data.items():
            setattr(config, key, value)
        config.updated_by = request.user
        config.save()
        return Response(self._payload(config))

    @staticmethod
    def _payload(config):
        phones = [
            {"user": d.user.get_full_name() or d.user.username, "device": d.name, "can_record": d.can_record,
             "app_version": d.app_version, "last_seen": d.last_seen}
            for d in DialerDevice.objects.select_related("user").filter(user__is_active=True).order_by("user__username")
        ]
        return {**status_for(config), "phones": phones}


class GoogleConnectView(APIView):
    """Manager: adres strony Google, na której zgadza się na zapis nagrań na Dysku."""

    permission_classes = [IsManager]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        if not oauth_configured():
            return Response({"detail": "Na serwerze brakuje danych Google (GOOGLE_CLIENT_ID i GOOGLE_CLIENT_SECRET w .env). "
                                       "Instrukcja: docs/NAGRYWANIE.md."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"url": auth_url(request, request.user)})


class GoogleDisconnectView(APIView):
    """Manager: odłącza Dysk Google (nagrania, które już tam są, zostają na Dysku)."""

    permission_classes = [IsManager]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        config = RecordingConfig.get()
        disconnect(config, request.user)
        return Response(RecordingSettingsView._payload(config))


def google_callback(request):
    """Google wraca tu po zgodzie managera (zwykła strona, nie API): zapisujemy dostęp i wracamy do Ustawień."""
    def back(state, message=""):
        query = urlencode({"drive": state, **({"msg": message} if message else {})})
        return redirect(f"/crm/settings/?{query}#rec-card")

    if request.GET.get("error"):
        return back("error", "Nie wyrażono zgody na Dysk Google.")
    user = user_from_state(request.GET.get("state"))
    if user is None or not is_manager(user):
        return back("error", "Link do łączenia wygasł. Kliknij „Połącz z Dyskiem Google” jeszcze raz.")
    code = request.GET.get("code")
    if not code:
        return back("error", "Google nie przysłał kodu. Spróbuj jeszcze raz.")
    try:
        connect(request, user, code)
    except DriveError as e:
        return back("error", str(e))
    return back("ok")


class DialerRecordingView(DialerDeviceAuthMixin, APIView):
    """Telefon wysyła nagranie rozmowy: surowy plik w treści zapytania (Content-Type audio/ogg lub audio/mp4)."""

    @extend_schema(
        request={"audio/ogg": OpenApiTypes.BINARY},
        parameters=[OpenApiParameter("duration", OpenApiTypes.INT, description="czas rozmowy w sekundach")],
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request, pk):
        duration = request.query_params.get("duration")
        try:
            duration = int(duration) if duration not in (None, "") else None
        except ValueError:
            duration = None
        content_type = (request.content_type or "").split(";")[0].strip().lower()
        rec, error = save_recording(request.auth, pk, request.body, content_type, duration)
        if error:
            code, message = error
            return Response({"detail": message}, status=code)
        return Response({"ok": True, "id": rec.pk}, status=status.HTTP_201_CREATED)


def recording_row(rec):
    return {
        "id": rec.pk,
        "lead": rec.lead_id,
        "agent_name": (rec.agent.get_full_name() or rec.agent.username) if rec.agent else "",
        "phone": rec.phone,
        "started_at": rec.started_at,
        "duration_seconds": rec.duration_seconds,
        "size_bytes": rec.size_bytes,
    }


class RecordingListView(APIView):
    """Manager: nagrania rozmów jednego leada (?lead=ID) albo ostatnie nagrania."""

    permission_classes = [IsManager]

    @extend_schema(parameters=[OpenApiParameter("lead", OpenApiTypes.INT)], responses=OpenApiTypes.OBJECT)
    def get(self, request):
        qs = CallRecording.objects.select_related("agent")
        lead = request.query_params.get("lead")
        if lead:
            if not str(lead).isdigit():
                return Response({"detail": "Zły numer leada."}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(lead_id=int(lead))
        return Response({"results": [recording_row(r) for r in qs[:100]]})


class RecordingAudioView(APIView):
    """Manager: plik nagrania (CRM pobiera go z Dysku Google i oddaje przeglądarce)."""

    permission_classes = [IsManager]

    @extend_schema(responses={(200, "audio/ogg"): OpenApiTypes.BINARY})
    def get(self, request, pk):
        rec = get_object_or_404(CallRecording, pk=pk)
        try:
            data = download_file(RecordingConfig.get(), rec.drive_file_id)
        except DriveError as e:
            return Response({"detail": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        response = HttpResponse(data, content_type=rec.content_type)
        response["Cache-Control"] = "private, max-age=3600"
        return response
