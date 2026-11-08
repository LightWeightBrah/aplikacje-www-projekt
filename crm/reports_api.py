"""API strony „Raporty” (tylko manager): GET /api/reports/?days=30 albo ?date_from=&date_to=."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsManager
from .reports import build_reports
from .views import parse_period


class ReportsView(APIView):
    """Lejek agentów, godziny odbierania, powody odmów, oferty bez odpowiedzi i końce umów."""

    permission_classes = [IsManager]

    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        parameters=[
            OpenApiParameter("days", int, description="Ostatnie N dni (1–365), domyślnie 30."),
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Początek zakresu (razem z date_to)."),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Koniec zakresu (razem z date_from)."),
        ],
    )
    def get(self, request):
        params = request.query_params.copy()
        params.setdefault("days", "30")
        date_from, date_to, error = parse_period(params)
        if error:
            return error
        return Response(build_reports(date_from, date_to))
