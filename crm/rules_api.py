"""Ustawienia zespołu: GET dla agentów i managerów, PATCH / reset tylko manager."""

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsManager, IsManagerOrReadOnly
from .rules import default_values, get_rules
from .serializers import TeamRulesSerializer


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
