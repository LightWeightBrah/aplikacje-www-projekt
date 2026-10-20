from datetime import date

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .excel import (
    XLSX_CONTENT_TYPE,
    ImportFileError,
    XlsxRenderer,
    import_leads as run_import,
    leads_to_xlsx,
)
from .models import Activity, Company, Lead, LeadAssignment, LeadBatch, Product
from .permissions import (
    IsAgentOrManager,
    IsLeadOwnerOrManager,
    IsManager,
    IsManagerOrReadOnly,
)
from .roles import is_manager, visible_to
from .serializers import (
    ActivitySerializer,
    AssignLeadSerializer,
    ImportLeadsSerializer,
    ImportResultSerializer,
    CompanySerializer,
    DueLeadsSerializer,
    LeadAssignmentSerializer,
    LeadBatchSerializer,
    LeadDetailSerializer,
    LeadSerializer,
    ProductSerializer,
    ReassignLeadsSerializer,
    ScheduleSerializer,
    StatsSerializer,
)
from .services import (
    CalendarRenderer,
    InvalidAssignment,
    NoLeadsAvailable,
    OverdueCallbacks,
    claim_next_lead,
    assign_lead,
    build_stats,
    calendar_ics,
    calendar_leads,
    day_schedule,
    due_leads,
    reassign_agent_leads,
    recycle_unreachable,
    register_activity,
    unreachable_leads,
)


class OwnerScopedMixin:
    """Agent widzi tylko swoje leady (i ich historię), manager wszystko."""

    owner_lookup = "owner"

    def get_queryset(self):
        queryset = super().get_queryset()
        return visible_to(self.request.user, queryset, self.owner_lookup)


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsManagerOrReadOnly]


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = [IsManagerOrReadOnly]
    search_fields = ["name", "nip", "krs", "regon"]


class LeadBatchViewSet(viewsets.ModelViewSet):
    queryset = LeadBatch.objects.all()
    serializer_class = LeadBatchSerializer
    permission_classes = [IsManagerOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(request=ImportLeadsSerializer, responses=ImportResultSerializer)
    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_file(self, request):
        """Manager: import bazy kontaktów z pliku .xlsx do nowej paczki leadów.

        Leady trafiają do puli. Duplikaty (po numerze telefonu) i błędne wiersze
        są pomijane, a raport opisuje każdy z nich.
        """
        serializer = ImportLeadsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = run_import(
                data["file"],
                request.user,
                data.get("name", ""),
                data.get("product"),
            )
        except ImportFileError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        if not result["created"]:
            result["detail"] = "Nie zaimportowano żadnego leada."
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


MANAGER_ONLY_ACTIONS = (
    "destroy",
    "assign",
    "reassign",
    "unreachable",
    "recycle",
)


class LeadViewSet(OwnerScopedMixin, viewsets.ModelViewSet):
    queryset = Lead.objects.select_related("product", "company", "owner", "batch")
    permission_classes = [IsAgentOrManager, IsLeadOwnerOrManager]
    filterset_fields = ["status", "owner", "batch", "product"]
    search_fields = ["contact_name", "phone", "email", "company__name", "company__nip", "company__krs"]
    ordering_fields = ["next_contact_at", "last_activity_at"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return LeadDetailSerializer
        return LeadSerializer

    def get_permissions(self):
        # Leady nie znikają: usuwać może tylko manager; tak samo przepisywanie
        # i pula nieosiągalnych
        if self.action in MANAGER_ONLY_ACTIONS:
            return [IsManager()]
        return super().get_permissions()

    def perform_create(self, serializer):
        # Lead dodany przez agenta od razu należy do niego,
        # lead dodany przez managera trafia do puli (bez opiekuna)
        owner = None if is_manager(self.request.user) else self.request.user
        serializer.save(owner=owner)

    @extend_schema(
        request=None,
        responses=LeadSerializer,
        parameters=[
            OpenApiParameter("batch", int, description="Losuj tylko z tej paczki (id)"),
        ],
    )
    @action(detail=False, methods=["post"], url_path="next")
    def next_lead(self, request):
        """Losuje lead z puli i przypisuje go zalogowanemu agentowi."""
        batch = request.query_params.get("batch")
        if batch is not None and not batch.isdigit():
            return Response(
                {"detail": "Parametr batch musi być liczbą."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            lead = claim_next_lead(request.user, int(batch) if batch else None)
        except OverdueCallbacks as error:
            return Response(
                {
                    "detail": "Masz zaległe oddzwonienia. Zrób je, zanim dostaniesz nowego leada.",
                    "overdue_count": error.count,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except NoLeadsAvailable:
            return Response(
                {"detail": "Brak leadów w puli."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(LeadSerializer(lead).data)

    @extend_schema(responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"], renderer_classes=[XlsxRenderer])
    def export(self, request):
        """Eksport leadów do pliku .xlsx (te same filtry i wyszukiwanie co w liście).

        Agent dostaje swoje leady, manager wszystkie.
        """
        queryset = self.filter_queryset(self.get_queryset())
        response = Response(leads_to_xlsx(queryset))
        response["Content-Disposition"] = 'attachment; filename="leady.xlsx"'
        return response

    @extend_schema(request=AssignLeadSerializer, responses=LeadSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        """Manager: przypisuje leada agentowi albo zwraca go do puli (agent = null)."""
        serializer = AssignLeadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            lead = assign_lead(
                self.get_object(),
                serializer.validated_data["agent"],
                serializer.validated_data.get("reason", ""),
            )
        except InvalidAssignment as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(LeadSerializer(lead).data)

    @extend_schema(
        request=ReassignLeadsSerializer,
        responses={200: {"type": "object", "properties": {"reassigned": {"type": "integer"}}}},
    )
    @action(detail=False, methods=["post"])
    def reassign(self, request):
        """Manager: przepisuje wszystkie aktywne leady odchodzącego agenta na innego."""
        serializer = ReassignLeadsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        count = reassign_agent_leads(
            serializer.validated_data["from_agent"],
            serializer.validated_data["to_agent"],
            serializer.validated_data.get("reason", ""),
        )
        return Response({"reassigned": count})

    @extend_schema(
        responses=LeadSerializer(many=True),
        parameters=[
            OpenApiParameter(
                "ready", bool, description="true = tylko te po okresie karencji"
            ),
        ],
    )
    @action(detail=False, methods=["get"])
    def unreachable(self, request):
        """Manager: pula nieosiągalnych leadów (opcjonalnie tylko gotowe do zwrotu)."""
        ready = request.query_params.get("ready", "").lower() in ("1", "true")
        queryset = unreachable_leads(ready_only=ready).select_related(
            "product", "company", "owner", "batch"
        )
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(LeadSerializer(page, many=True).data)

    @extend_schema(
        request=None,
        responses={200: {"type": "object", "properties": {"recycled": {"type": "integer"}}}},
    )
    @action(detail=False, methods=["post"], url_path="recycle-unreachable")
    def recycle(self, request):
        """Manager: zwraca do obiegu nieosiągalne leady po okresie karencji."""
        return Response({"recycled": recycle_unreachable()})


class ActivityViewSet(
    OwnerScopedMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Historia kontaktów: można dodawać i czytać, nie można edytować ani usuwać."""

    queryset = Activity.objects.select_related("lead", "agent")
    serializer_class = ActivitySerializer
    permission_classes = [IsAgentOrManager, IsLeadOwnerOrManager]
    owner_lookup = "lead__owner"
    filterset_fields = ["lead", "agent", "outcome"]

    def perform_create(self, serializer):
        lead = serializer.validated_data["lead"]
        if not is_manager(self.request.user) and lead.owner_id != self.request.user.id:
            raise PermissionDenied("Aktywność możesz dodać tylko do swojego leada.")
        data = serializer.validated_data
        # Zapis rozmowy + aktualizacja leada (próby, status, termin) robi serwis
        serializer.instance = register_activity(
            lead=lead,
            agent=self.request.user,
            outcome=data["outcome"],
            note=data.get("note", ""),
            next_contact_at=data.get("next_contact_at"),
            next_contact_note=data.get("next_contact_note", ""),
        )


class LeadAssignmentViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet):
    queryset = LeadAssignment.objects.select_related("lead")
    serializer_class = LeadAssignmentSerializer
    permission_classes = [IsAgentOrManager, IsLeadOwnerOrManager]
    owner_lookup = "lead__owner"


class MyLeadsViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Leady, które prowadzi zalogowany użytkownik (także manager: tylko własne)."""

    serializer_class = LeadSerializer
    permission_classes = [IsAgentOrManager]
    filterset_fields = ["status", "batch", "product"]
    search_fields = ["contact_name", "phone", "email", "company__name", "company__nip", "company__krs"]
    ordering_fields = ["next_contact_at", "last_activity_at"]

    def get_queryset(self):
        if not self.request.user.is_authenticated:  # np. generowanie schematu Swaggera
            return Lead.objects.none()
        return Lead.objects.select_related("product", "company", "batch").filter(
            owner=self.request.user
        )

    @extend_schema(
        responses=DueLeadsSerializer,
        parameters=[
            OpenApiParameter(
                "minutes", int, description="Okno w minutach (domyślnie 15, max 1440)"
            ),
        ],
    )
    @action(detail=False, methods=["get"])
    def due(self, request):
        """Leady do zrobienia teraz: zaległe oraz z terminem w najbliższych minutach."""
        raw = request.query_params.get("minutes", "15")
        if not raw.isdigit() or not 0 <= int(raw) <= 1440:
            return Response(
                {"detail": "Parametr minutes musi być liczbą od 0 do 1440."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        minutes = int(raw)
        overdue, upcoming = due_leads(request.user, minutes)
        data = {"window_minutes": minutes, "overdue": overdue, "upcoming": upcoming}
        return Response(DueLeadsSerializer(data).data)

    @extend_schema(
        responses=ScheduleSerializer,
        parameters=[
            OpenApiParameter(
                "date", str, description="Dzień w formacie RRRR-MM-DD (domyślnie dziś)"
            ),
        ],
    )
    @action(detail=False, methods=["get"])
    def schedule(self, request):
        """Plan dnia: leady z terminem kontaktu w danym dniu (na dziś także zaległe)."""
        raw = request.query_params.get("date")
        try:
            day = date.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            return Response(
                {"detail": "Parametr date musi mieć format RRRR-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        overdue, scheduled = day_schedule(request.user, day)
        data = {"date": day, "overdue": overdue, "scheduled": scheduled}
        return Response(ScheduleSerializer(data).data)

    @extend_schema(responses={(200, "text/calendar"): OpenApiTypes.STR})
    @action(
        detail=False,
        methods=["get"],
        renderer_classes=[CalendarRenderer],
    )
    def calendar(self, request):
        """Plik .ics z terminami kontaktów: można go zaimportować do kalendarza."""
        response = Response(calendar_ics(calendar_leads(request.user)))
        response["Content-Disposition"] = 'attachment; filename="leadcrm.ics"'
        return response


class StatsViewSet(viewsets.ViewSet):
    """Statystyki: manager widzi cały zespół, agent tylko własne wyniki."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(
        responses=StatsSerializer,
        parameters=[
            OpenApiParameter(
                "days", int, description="Okno w dniach dla rozmów (domyślnie 7, 1-365)"
            ),
        ],
    )
    def list(self, request):
        """Lejek leadów, skuteczność agentów i liczba rozmów dzień po dniu."""
        raw = request.query_params.get("days", "7")
        if not raw.isdigit() or not 1 <= int(raw) <= 365:
            return Response(
                {"detail": "Parametr days musi być liczbą od 1 do 365."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = build_stats(request.user, int(raw), is_manager(request.user))
        return Response(StatsSerializer(data).data)