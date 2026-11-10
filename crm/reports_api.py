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


class AgentReportView(APIView):
    """„Wszystko po agencie”: cała praca jednej osoby w okresie (też konta wyłączone).

    Bez `user` – lista osób do wyboru. `file=xlsx` – ten sam raport w pliku Excel."""

    permission_classes = [IsManager]

    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        parameters=[
            OpenApiParameter("user", int, description="Id osoby; bez niego lista osób do wyboru."),
            OpenApiParameter("days", int, description="Ostatnie N dni (1–365), domyślnie 30."),
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Początek zakresu (razem z date_to)."),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Koniec zakresu (razem z date_from)."),
            OpenApiParameter("file", str, description="xlsx = pobierz plik Excel."),
        ],
    )
    def get(self, request):
        from django.contrib.auth import get_user_model
        from django.http import HttpResponse

        from .agent_report import agent_report, agent_report_xlsx, report_people

        raw = request.query_params.get("user", "")
        if not raw:
            return Response({"people": report_people()})
        if not raw.isdigit():
            return Response({"detail": "Parametr user musi być liczbą."}, status=400)
        person = get_user_model().objects.filter(pk=int(raw)).first()
        if person is None:
            return Response({"detail": "Nie ma takiej osoby."}, status=404)
        params = request.query_params.copy()
        params.setdefault("days", "30")
        date_from, date_to, error = parse_period(params)
        if error:
            return error
        if request.query_params.get("file") == "xlsx":
            resp = HttpResponse(agent_report_xlsx(person, date_from, date_to),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            resp["Content-Disposition"] = f'attachment; filename="praca-{person.username}-{date_from}-{date_to}.xlsx"'
            return resp
        return Response(agent_report(person, date_from, date_to))
