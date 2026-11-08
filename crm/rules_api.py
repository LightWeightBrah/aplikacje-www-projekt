"""Ustawienia zespołu: GET dla agentów i managerów, PATCH / reset tylko manager."""

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsManager, IsManagerOrReadOnly
from .rules import default_values, get_rules
from .models import SmsReminder
from .serializers import TeamRulesSerializer, sms_text_error
from .sms import TEST_SMS_VALID, flood_block, has_phone, phone_for_sms, queue_test_sms
from .work import dialer_status


class TeamRulesView(generics.RetrieveUpdateAPIView):
    """Odczyt ustawień (agent i manager) oraz ich zmiana (tylko manager, PATCH)."""

    serializer_class = TeamRulesSerializer
    permission_classes = [IsManagerOrReadOnly]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return get_rules()

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class TeamRulesResetView(APIView):
    """Przywraca ustawienia domyślne (z config/settings.py)."""

    permission_classes = [IsManager]
    serializer_class = TeamRulesSerializer

    @extend_schema(request=None, responses=TeamRulesSerializer)
    def post(self, request):
        rules = get_rules()
        for name, value in default_values().items():
            setattr(rules, name, value)
        rules.updated_by = request.user
        rules.save()
        return Response(TeamRulesSerializer(rules).data, status=status.HTTP_200_OK)


class TestSmsSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=30)
    text = serializers.CharField(max_length=2000)
    minutes = serializers.IntegerField(min_value=1, max_value=10080, required=False)


class TestSmsView(APIView):
    """Manager: próbny SMS z przypomnieniem na wpisany numer, wysyłany z JEGO sparowanego telefonu.

    Treść to szablon z formularza (jeszcze niezapisany), termin w przykładzie = teraz + „ile wcześniej”.
    Obowiązuje to samo zabezpieczenie co przy prawdziwych SMS-ach (odstęp na ten sam numer, limit na dobę)."""

    permission_classes = [IsManager]

    @extend_schema(request=TestSmsSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        data = TestSmsSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        d = data.validated_data
        phone = phone_for_sms(d["phone"])
        if not phone:
            return Response({"phone": "Wpisz cały numer telefonu (9 cyfr albo z +48)."}, status=status.HTTP_400_BAD_REQUEST)
        error = sms_text_error(d["text"])
        if error:
            return Response({"text": error}, status=status.HTTP_400_BAD_REQUEST)
        if not has_phone(request.user):
            return Response({"detail": "Najpierw sparuj swój telefon z dialerem (menu konta → Telefon (dialer)). "
                                       "Próbny SMS idzie z Twojego telefonu."}, status=status.HTTP_400_BAD_REQUEST)
        rules = get_rules()
        blocked = flood_block(phone, rules)
        if blocked:
            return Response({"detail": f"Zabezpieczenie: {blocked}."}, status=status.HTTP_400_BAD_REQUEST)
        sms = queue_test_sms(request.user, phone, d["text"].strip(), d.get("minutes") or rules.sms_reminder_minutes)
        return Response({"id": sms.pk, "state": sms.state, "text": sms.text, "phone": sms.phone,
                         "online": dialer_status(request.user)["online"]}, status=status.HTTP_201_CREATED)


class TestSmsStateView(APIView):
    """Manager: czy próbny SMS już wyszedł (queued → sending → sent / failed)."""

    permission_classes = [IsManager]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        sms = SmsReminder.objects.filter(pk=pk, user=request.user, kind=SmsReminder.Kind.TEST).first()
        if sms is None:
            return Response({"detail": "Nie ma takiego SMS-a."}, status=status.HTTP_404_NOT_FOUND)
        expired = sms.state == SmsReminder.State.QUEUED and sms.created_at < timezone.now() - TEST_SMS_VALID
        return Response({"id": sms.pk, "state": "expired" if expired else sms.state, "error": sms.error})
