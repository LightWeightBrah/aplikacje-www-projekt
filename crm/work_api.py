"""API pracy agenta (Solid CRM): „Następny klient”, liczniki follow-upów, oferty i dialer."""

from datetime import date

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import DialerDevice, Lead, Offer, OfferConfig, Product
from .permissions import IsAgentOrManager, IsManager
from .roles import is_manager, visible_to
from .serializers import LeadSerializer
from .views import PERIOD_PARAMETERS, parse_period
from .services import base_progress
from .work import (
    PAIRING_MINUTES,
    OfferError,
    ContactExists,
    create_contact,
    attendance,
    create_offer,
    day_minutes,
    end_workday,
    touch_workday,
    minutes_report,
    shared_pool_left,
    offer_document,
    offer_filename,
    create_pairing,
    dialer_status,
    finish_call,
    next_work,
    offer_numbers,
    pair_device,
    poll_call,
    request_call,
    today_stats,
    work_counts,
)


class NextWorkView(APIView):
    """Najważniejszy kontakt teraz: follow-up oferty → oddzwonienie → ponowna próba → nowa firma."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        touch_workday(request.user)
        lead, reason = next_work(request.user)
        if lead is None:
            return Response({"detail": "Na teraz nie ma nic do zrobienia: baza i pula są obdzwonione."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({"reason": reason, "lead": LeadSerializer(lead, context={"request": request}).data})


class WorkSummaryView(APIView):
    """Liczniki segmentów (oferty, oddzwonienia, nie odebrali, nowi), liczby dnia i postęp bazy."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        touch_workday(request.user)   # „Dzwonienie” odświeża to co 20 s: tak wiemy, że agent jest w pracy
        rows = base_progress(request.user)
        today = day_minutes(request.user, timezone.localdate())
        return Response({
            "workday": {"started_at": today["started_at"], "ended_at": today["ended_at"],
                        "worked_minutes": today["worked_minutes"]},
            "counts": work_counts(request.user),
            "today": today_stats(request.user),
            "base": {"total": sum(r["total"] for r in rows), "left": sum(r["left"] for r in rows),
                     "done": sum(r["done"] for r in rows), "pool_left": shared_pool_left(request.user)},
        })


class ContactCreateSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=30)
    company_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    contact_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")
    referred_by = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all(), required=False, allow_null=True, default=None)

    def validate_phone(self, value):
        import re
        if not 9 <= len(re.sub(r"\D", "", value)) <= 15:
            raise serializers.ValidationError("Numer telefonu musi mieć od 9 do 15 cyfr.")
        return value.strip()


class WorkContactView(APIView):
    """Agent dodaje klienta sam (np. z polecenia). Numer już w CRM → 409 z tym klientem (bez duplikatu)."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(request=ContactCreateSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        serializer = ContactCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            lead = create_contact(request.user, **data)
        except ContactExists as exists:
            mine = exists.lead.owner_id == request.user.pk or is_manager(request.user)
            return Response({
                "detail": "Ten numer jest już w CRM." + ("" if mine else " Prowadzi go inny agent albo jest w puli."),
                "lead": exists.lead.pk if mine else None,
            }, status=status.HTTP_409_CONFLICT)
        return Response({"lead": LeadSerializer(lead, context={"request": request}).data}, status=status.HTTP_201_CREATED)


class WorkEndView(APIView):
    """Agent klika „Kończę pracę”: zapisuje godzinę wyjścia (następny ruch w CRM znów otwiera dzień)."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        end_workday(request.user)
        today = day_minutes(request.user, timezone.localdate())
        return Response({"started_at": today["started_at"], "ended_at": today["ended_at"],
                         "worked_minutes": today["worked_minutes"], "minutes": today["minutes"], "goal": today["goal"]})


class AttendanceView(APIView):
    """Manager: kto kiedy przyszedł i wyszedł, czas pracy, minuty rozmów i cel (15 min na godzinę). ?date=RRRR-MM-DD"""

    permission_classes = [IsManager]

    @extend_schema(parameters=[OpenApiParameter("date", OpenApiTypes.DATE, description="Dzień (domyślnie dziś)")],
                   responses=OpenApiTypes.OBJECT)
    def get(self, request):
        raw = request.query_params.get("date")
        try:
            day = date.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            return Response({"detail": "Podaj datę w formacie RRRR-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"date": day.isoformat(), "goal_minutes_per_hour": 15, "results": attendance(day)})


class WorkMinutesView(APIView):
    """Minuty rozmów agenta w okresie (`days` albo `date_from` + `date_to`): dzień po dniu i cel dzienny."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(parameters=PERIOD_PARAMETERS, responses=OpenApiTypes.OBJECT)
    def get(self, request):
        date_from, date_to, error = parse_period(request.query_params)
        if error:
            return error
        return Response(minutes_report(request.user, date_from, date_to))


# ------------------------------------------------------------------ oferty

class OfferConfigSerializer(serializers.ModelSerializer):
    email_enabled = serializers.SerializerMethodField()

    class Meta:
        model = OfferConfig
        exclude = ("id",)
        read_only_fields = ("updated_at",)

    def get_email_enabled(self, obj) -> bool:
        from django.conf import settings
        return bool(getattr(settings, "OFFER_EMAIL_ENABLED", False))

    def validate(self, attrs):
        for name in ("commission_24", "commission_36"):
            if name in attrs and not 0 <= attrs[name] <= 100:
                raise serializers.ValidationError({name: "Mnożnik musi być od 0 do 100."})
        if attrs.get("default_term") not in (None, 24, 36):
            raise serializers.ValidationError({"default_term": "Okres umowy: 24 albo 36 miesięcy."})
        return attrs


class OfferConfigView(APIView):
    """Ustawienia ofert: czytają wszyscy zalogowani (formularz oferty), zmienia manager."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses=OfferConfigSerializer)
    def get(self, request):
        return Response(OfferConfigSerializer(OfferConfig.get()).data)

    @extend_schema(request=OfferConfigSerializer, responses=OfferConfigSerializer)
    def patch(self, request):
        if not is_manager(request.user):
            return Response({"detail": "Ustawienia ofert zmienia manager."}, status=status.HTTP_403_FORBIDDEN)
        serializer = OfferConfigSerializer(OfferConfig.get(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class OfferSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.username", read_only=True, allow_null=True)

    class Meta:
        model = Offer
        exclude = ("activity", "html")


class OfferCreateSerializer(serializers.Serializer):
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all())
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1, max_value=500, default=1)
    term_months = serializers.ChoiceField(choices=(24, 36), default=36)
    promo = serializers.BooleanField(default=False)
    client_email = serializers.EmailField(required=False, allow_blank=True, default="")
    next_contact_at = serializers.DateTimeField()
    next_contact_note = serializers.CharField(required=False, allow_blank=True, max_length=255)
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")

    def validate_next_contact_at(self, value):
        if value < timezone.now():
            raise serializers.ValidationError("Termin follow-upu nie może być w przeszłości.")
        return value


class OfferPreviewSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, max_value=500, default=1)
    term_months = serializers.ChoiceField(choices=(24, 36), default=36)
    promo = serializers.BooleanField(default=False)


class OffersView(APIView):
    """GET ?lead=ID: oferty leada. POST: zapis rozmowy „Wysłana oferta” + oferta + wysyłka."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses=OfferSerializer(many=True))
    def get(self, request):
        offers = visible_to(request.user, Offer.objects.select_related("agent"), "lead__owner")
        if request.query_params.get("lead", "").isdigit():
            offers = offers.filter(lead_id=int(request.query_params["lead"]))
        return Response(OfferSerializer(offers[:100], many=True).data)

    @extend_schema(request=OfferCreateSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        serializer = OfferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lead = data["lead"]
        if not is_manager(request.user) and lead.owner_id != request.user.pk:
            return Response({"detail": "Ofertę możesz wysłać tylko swojemu klientowi."}, status=status.HTTP_403_FORBIDDEN)
        try:
            offer, mailto = create_offer(
                lead, request.user, data["product"], data["quantity"], data["term_months"], data["promo"],
                data["client_email"], data["next_contact_at"], data.get("next_contact_note"), data["note"],
            )
        except OfferError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"offer": OfferSerializer(offer).data, "mailto": mailto,
                         "activity": offer.activity_id}, status=status.HTTP_201_CREATED)


class OfferHtmlView(APIView):
    """Gotowa oferta jako plik HTML (jak we wzorniku ofert): do pobrania albo skopiowania z formatowaniem."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses={(200, "text/html"): OpenApiTypes.STR})
    def get(self, request, pk):
        offer = get_object_or_404(visible_to(request.user, Offer.objects.select_related("lead", "lead__company"), "lead__owner"), pk=pk)
        response = HttpResponse(offer_document(offer.html, f"Oferta: {offer.product_name}"), content_type="text/html; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{offer_filename(offer.lead)}"'
        return response


class OfferPreviewView(APIView):
    """Wylicza zobowiązanie miesięczne i prowizję (bez zapisywania)."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(request=OfferPreviewSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        serializer = OfferPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        monthly, commission = offer_numbers(d["product"], d["quantity"], d["term_months"], d["promo"])
        return Response({"monthly_total": str(monthly), "commission": str(commission)})


# ------------------------------------------------------------------ dialer

class DialerTokenAuthentication(BaseAuthentication):
    """Aplikacja na telefonie przedstawia się nagłówkiem `X-Dialer-Token`."""

    def authenticate(self, request):
        token = request.headers.get("X-Dialer-Token")
        if not token:
            return None
        device = DialerDevice.objects.select_related("user").filter(token=token, user__is_active=True).first()
        if device is None:
            raise AuthenticationFailed("Telefon nie jest sparowany z CRM. Sparuj go ponownie.")
        return device.user, device

    def authenticate_header(self, request):
        return "X-Dialer-Token"


class DialerTokenScheme(OpenApiAuthenticationExtension):
    """Opis nagłówka X-Dialer-Token w Swaggerze."""

    target_class = "crm.work_api.DialerTokenAuthentication"
    name = "DialerToken"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "X-Dialer-Token"}


class DialerPairingView(APIView):
    """Agent w CRM prosi o kod parowania telefonu (pokazywany jako cyfry i kod QR)."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        pairing = create_pairing(request.user)
        return Response({"code": pairing.code, "valid_minutes": PAIRING_MINUTES})


class DialerPairView(APIView):
    """Telefon wymienia kod parowania na własny token."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dialer_pair"

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        code = str(request.data.get("code", "")).strip()
        device = pair_device(code, str(request.data.get("name", "")))
        if device is None:
            return Response({"detail": "Kod jest błędny albo wygasł. Wygeneruj nowy w CRM."},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"token": device.token, "user": device.user.get_full_name() or device.user.username})


class DialerDeviceAuthMixin:
    authentication_classes = [DialerTokenAuthentication]
    permission_classes = [IsAuthenticated]


class DialerPollView(DialerDeviceAuthMixin, APIView):
    """Telefon co chwilę pyta: „czy mam coś wybrać?”. 204 = nic."""

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        call = poll_call(request.auth)
        if call is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        lead = call.lead
        return Response({"id": call.pk, "phone": call.phone, "name": lead.display_name if lead else ""})


class DialerFinishView(DialerDeviceAuthMixin, APIView):
    """Telefon odsyła wynik połączenia z rejestru: answered (bool) i duration_seconds."""

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, pk):
        call = finish_call(request.auth, pk, request.data.get("answered"), request.data.get("duration_seconds"))
        if call is None:
            return Response({"detail": "Nie ma takiego połączenia."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"ok": True})


class DialerCallView(APIView):
    """CRM: „Zadzwoń” – telefon agenta wybierze numer leada (albo podany numer)."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        lead = None
        if request.data.get("lead"):
            lead = get_object_or_404(visible_to(request.user, Lead.objects.all(), "owner"), pk=request.data["lead"])
        phone = str(request.data.get("phone") or (lead.phone if lead else "")).strip()
        if not phone:
            return Response({"detail": "Brak numeru do wybrania."}, status=status.HTTP_400_BAD_REQUEST)
        touch_workday(request.user)
        call = request_call(request.user, phone, lead)
        return Response({"id": call.pk, "state": call.state, **dialer_status(request.user)}, status=status.HTTP_201_CREATED)


class DialerStatusView(APIView):
    """Stan telefonu (sparowany / połączony) i ostatniego połączenia; DELETE = odłącz telefony."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(dialer_status(request.user))

    @extend_schema(responses=None)
    def delete(self, request):
        DialerDevice.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ informacje dla zespołu

class AnnouncementSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.username", read_only=True, allow_null=True)

    class Meta:
        from .models import Announcement
        model = Announcement
        fields = ("id", "title", "body", "pinned", "is_active", "author_name", "created_at")
        read_only_fields = ("author_name", "created_at")


class AnnouncementsView(APIView):
    """GET: aktualne informacje dla zespołu (manager widzi też ukryte). POST: manager dodaje."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(responses=AnnouncementSerializer(many=True))
    def get(self, request):
        from .models import Announcement
        items = Announcement.objects.all() if is_manager(request.user) else Announcement.objects.filter(is_active=True)
        return Response(AnnouncementSerializer(items[:50], many=True).data)

    @extend_schema(request=AnnouncementSerializer, responses=AnnouncementSerializer)
    def post(self, request):
        if not is_manager(request.user):
            return Response({"detail": "Informacje dla zespołu dodaje manager."}, status=status.HTTP_403_FORBIDDEN)
        serializer = AnnouncementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(author=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AnnouncementDetailView(APIView):
    """PATCH / DELETE jednej informacji (tylko manager)."""

    permission_classes = [IsAgentOrManager]

    def _get(self, request, pk):
        from .models import Announcement
        if not is_manager(request.user):
            return None, Response({"detail": "Tylko manager."}, status=status.HTTP_403_FORBIDDEN)
        return get_object_or_404(Announcement, pk=pk), None

    @extend_schema(request=AnnouncementSerializer, responses=AnnouncementSerializer)
    def patch(self, request, pk):
        item, error = self._get(request, pk)
        if error:
            return error
        serializer = AnnouncementSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(responses=None)
    def delete(self, request, pk):
        item, error = self._get(request, pk)
        if error:
            return error
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
