"""API aktywacji urządzeń: wpisanie kodu na nowym urządzeniu i lista urządzeń dla managera."""

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .device_lock import activate, new_code, set_device_cookie
from .models import AccessDevice, ActivationCode
from .permissions import IsManager


class ActivateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=80)


class DeviceActivateView(APIView):
    """Nowe urządzenie wpisuje jednorazowy kod od managera i nazwę (np. „Laptop Jana”)."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "device_activate"   # kodu nie da się zgadywać

    @extend_schema(request=ActivateSerializer, responses={204: None})
    def post(self, request):
        data = ActivateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        result = activate(data.validated_data["code"], data.validated_data["name"])
        if result is None:
            return Response({"detail": "Zły albo zużyty kod. Kod działa raz i tylko 24 godziny – poproś managera o nowy."},
                            status=status.HTTP_400_BAD_REQUEST)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        set_device_cookie(response, result[1])
        return response


class DeviceListView(APIView):
    """Manager: urządzenia z dostępem do CRM i czy blokada jest włączona."""

    permission_classes = [IsManager]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        current = getattr(request, "access_device", None)
        rows = [{
            "id": d.pk, "name": d.name, "created_at": d.created_at, "last_seen": d.last_seen,
            "created_by": (d.created_by.get_full_name() or d.created_by.username) if d.created_by else "",
            "revoked_at": d.revoked_at, "this_device": bool(current and current.pk == d.pk),
        } for d in AccessDevice.objects.select_related("created_by")]
        waiting = ActivationCode.objects.filter(used_at__isnull=True, expires_at__gt=timezone.now()).count()
        return Response({"device_lock": settings.DEVICE_LOCK, "codes_waiting": waiting, "results": rows})


class DeviceCodeView(APIView):
    """Manager: nowy jednorazowy kod aktywacji (ważny 24 h). Kod widać tylko w tej odpowiedzi."""

    permission_classes = [IsManager]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        code, expires = new_code(request.user)
        return Response({"code": code, "expires_at": expires}, status=status.HTTP_201_CREATED)


class DeviceRevokeView(APIView):
    """Manager: blokuje urządzenie (np. po odejściu agenta albo zgubionym laptopie)."""

    permission_classes = [IsManager]

    @extend_schema(request=None, responses={204: None})
    def post(self, request, pk):
        device = get_object_or_404(AccessDevice, pk=pk)
        if getattr(request, "access_device", None) and request.access_device.pk == device.pk:
            return Response({"detail": "Nie możesz zablokować urządzenia, na którym teraz pracujesz."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not device.revoked_at:
            device.revoked_at = timezone.now()
            device.save(update_fields=["revoked_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
