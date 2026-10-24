from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .excel import (
    XLSX_CONTENT_TYPE,
    ImportFileError,
    XlsxRenderer,
    import_leads as run_import,
    leads_to_xlsx,
)
from .filters import LeadFilter
from .models import (
    Activity,
    AgentProfile,
    Company,
    Lead,
    LeadAssignment,
    LeadBatch,
    Product,
)
from .permissions import (
    IsAgentOrManager,
    IsLeadOwnerOrManager,
    IsManager,
    IsManagerOrReadOnly,
)
from .roles import AGENT, can_view_team_stats, is_manager, visible_to
from .serializers import (
    ActivitySerializer,
    AgentAccessBulkSerializer,
    AgentAccessSerializer,
    AgentSerializer,
    AssignLeadSerializer,
    ImportLeadsSerializer,
    ImportResultSerializer,
    CompanySerializer,
    DueLeadsSerializer,
    LeadAssignmentSerializer,
    LeadBatchSerializer,
    LeadDetailSerializer,
    LeaderboardSerializer,
    LeadSerializer,
    ProductSerializer,
    ReassignLeadsSerializer,
    ScheduleSerializer,
    StatsSerializer,
    assignable_users,
)
from .services import (
    lookup_leads,
    ACTIVE_STATUSES,
    CalendarRenderer,
    InvalidAssignment,
    NoLeadsAvailable,
    OverdueCallbacks,
    claim_next_lead,
    assign_lead,
    LEADERBOARD_SORT_KEYS,
    build_stats,
    leaderboard,
    PROMISED_STATUSES,
    with_owner_tries,
    with_transfer_info,
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
    filterset_class = LeadFilter
    search_fields = ["contact_name", "phone", "email", "company__name", "company__nip", "company__krs"]
    ordering_fields = [
        "next_contact_at",
        "last_activity_at",
        "created_at",
        "contact_name",
        "status",
        "attempts_count",
    ]

    def get_queryset(self):
        return with_owner_tries(with_transfer_info(super().get_queryset()))

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

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "q", str, description="Telefon, NIP, KRS, nazwisko, firma albo e-mail (min. 2 znaki)"
            ),
        ],
        responses=LeadSerializer(many=True),
    )
    @action(detail=False, methods=["get"], url_path="lookup")
    def lookup(self, request):
        """Szybkie wyszukiwanie leada (np. po numerze dzwoniącego).

        Agent szuka wśród swoich leadów, manager wśród wszystkich. Wynik to maks. 8 leadów,
        każdy z polem `match` (exact = dokładne trafienie numeru/NIP/KRS) i `matched_on`
        (po czym znaleziono: phone, nip, krs, name, company, email)."""
        found = lookup_leads(self.get_queryset(), request.query_params.get("q", ""))
        data = []
        for lead, match, field in found:
            item = dict(LeadSerializer(lead).data)
            item["match"] = match
            item["matched_on"] = field
            data.append(item)
        return Response(data)

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
                "ready", bool, description="true = tylko te, które czekają już wymagany czas (domyślnie 30 dni od ostatniej próby)"
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
        """Manager: zwraca do puli nieosiągalne leady, które czekają już wymagany czas (domyślnie 30 dni)."""
        return Response({"recycled": recycle_unreachable()})


class AgentViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Manager: lista agentów i managerów (do przypisywania leadów i filtrów).

    `PATCH /api/agents/{id}/` pozwala włączyć lub wyłączyć agentowi podgląd wyników innych.
    """

    serializer_class = AgentSerializer
    permission_classes = [IsManager]
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        return assignable_users()

    def list(self, request, *args, **kwargs):
        active = Q(leads__status__in=ACTIVE_STATUSES)
        overdue = Q(leads__status__in=PROMISED_STATUSES) & Q(leads__next_contact_at__lt=timezone.now())
        users = self.get_queryset().annotate(
            active_leads=Count("leads", filter=active, distinct=True),
            overdue_leads=Count("leads", filter=overdue, distinct=True),
        )
        blocked = set(
            AgentProfile.objects.filter(can_view_team_stats=False).values_list(
                "user_id", flat=True
            )
        )
        rows = [
            {
                "id": u.pk,
                "username": u.username,
                "full_name": u.get_full_name() or u.username,
                "role": "manager" if is_manager(u) else "agent",
                "active_leads": u.active_leads,
                "overdue_leads": u.overdue_leads,
                "can_view_team_stats": u.pk not in blocked,
            }
            for u in users.order_by("username")
        ]
        return Response(AgentSerializer(rows, many=True).data)

    @extend_schema(request=AgentAccessSerializer, responses=AgentAccessSerializer)
    def partial_update(self, request, pk=None):
        """Manager włącza lub wyłącza agentowi podgląd wyników innych agentów."""
        agent = self.get_object()
        if is_manager(agent):
            return Response(
                {"detail": "Manager zawsze widzi wyniki zespołu."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = AgentAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        AgentProfile.objects.update_or_create(
            user=agent,
            defaults={
                "can_view_team_stats": serializer.validated_data["can_view_team_stats"]
            },
        )
        return Response(
            {"can_view_team_stats": serializer.validated_data["can_view_team_stats"]}
        )

    @extend_schema(request=AgentAccessSerializer, responses=AgentAccessBulkSerializer)
    @action(detail=False, methods=["post"], url_path="team-access")
    def team_access(self, request):
        """Manager włącza lub wyłącza podgląd wyników zespołu wszystkim agentom naraz."""
        serializer = AgentAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = serializer.validated_data["can_view_team_stats"]
        agents = get_user_model().objects.filter(is_active=True, groups__name=AGENT).distinct()
        with transaction.atomic():
            for agent in agents:
                AgentProfile.objects.update_or_create(
                    user=agent, defaults={"can_view_team_stats": value}
                )
        return Response({"can_view_team_stats": value, "updated": agents.count()})


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
            # brak pola = zostaje dotychczasowa sprawa; puste pole = agent świadomie ją czyści
            next_contact_note=data.get("next_contact_note"),
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
        queryset = Lead.objects.select_related("product", "company", "owner", "batch").filter(
            owner=self.request.user
        )
        return with_owner_tries(with_transfer_info(queryset))

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


TEAM_STATS_DISABLED = "Podgląd wyników innych agentów jest wyłączony przez managera."

PERIOD_PARAMETERS = [
    OpenApiParameter("days", int, description="Okno w dniach (domyślnie 7, 1-365)"),
    OpenApiParameter(
        "date_from", OpenApiTypes.DATE, description="Początek zakresu (RRRR-MM-DD), razem z date_to"
    ),
    OpenApiParameter(
        "date_to", OpenApiTypes.DATE, description="Koniec zakresu włącznie (RRRR-MM-DD)"
    ),
]


def parse_period(params):
    """Okres z parametrów zapytania: (date_from, date_to, błąd). Błąd to gotowa odpowiedź 400."""
    if "date_from" in params or "date_to" in params:
        try:
            date_from = date.fromisoformat(params.get("date_from", ""))
            date_to = date.fromisoformat(params.get("date_to", ""))
        except ValueError:
            return None, None, Response(
                {"detail": "Podaj obie daty w formacie RRRR-MM-DD (date_from i date_to)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if date_from > date_to:
            return None, None, Response(
                {"detail": "Data początkowa nie może być późniejsza niż końcowa."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (date_to - date_from).days + 1 > 365:
            return None, None, Response(
                {"detail": "Zakres może mieć maksymalnie 365 dni."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return date_from, date_to, None
    raw = params.get("days", "7")
    if not raw.isdigit() or not 1 <= int(raw) <= 365:
        return None, None, Response(
            {"detail": "Parametr days musi być liczbą od 1 do 365."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    date_to = timezone.localdate()
    return date_to - timedelta(days=int(raw) - 1), date_to, None


class StatsViewSet(viewsets.ViewSet):
    """Statystyki: manager widzi cały zespół, agent własne wyniki (i cudze, jeśli wolno)."""

    permission_classes = [IsAgentOrManager]

    @extend_schema(
        responses=StatsSerializer,
        parameters=PERIOD_PARAMETERS
        + [
            OpenApiParameter(
                "agent",
                int,
                description=(
                    "Id agenta, którego wyniki chcesz zobaczyć (tylko liczby, bez leadów). "
                    "Agent może to robić, dopóki manager mu nie wyłączy podglądu (403)."
                ),
            ),
            OpenApiParameter(
                "scope",
                str,
                enum=["team", "me"],
                description=(
                    "Tylko dla managera: `team` (domyślnie) = cały zespół i pula, "
                    "`me` = wyłącznie jego własna praca. Dla agenta bez znaczenia."
                ),
            ),
        ],
    )
    def list(self, request):
        """Lejek leadów, skuteczność agentów i rozmowy dzień po dniu.

        Okres: `days` (ostatnie N dni) albo zakres `date_from` + `date_to` (do 365 dni).
        `?agent=ID` zwraca wyniki wskazanego agenta (bez jego leadów i bez puli).
        """
        date_from, date_to, error = parse_period(request.query_params)
        if error:
            return error
        manager = is_manager(request.user)
        if request.query_params.get("scope") not in (None, "", "team", "me"):
            return Response(
                {"detail": "Parametr scope przyjmuje wartości: team, me."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_agent = request.query_params.get("agent")
        if raw_agent is None or raw_agent == str(request.user.pk):
            if request.query_params.get("scope") == "me":
                manager = False  # manager ogląda tylko własną pracę, jak agent
            data = build_stats(request.user, 0, manager, date_from, date_to)
            return Response(StatsSerializer(data).data)

        if not raw_agent.isdigit():
            return Response(
                {"detail": "Parametr agent musi być numerem id agenta."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not can_view_team_stats(request.user):
            raise PermissionDenied(TEAM_STATS_DISABLED)
        target = assignable_users().filter(pk=int(raw_agent)).first()
        if target is None:
            return Response(
                {"detail": "Nie ma takiego agenta."}, status=status.HTTP_404_NOT_FOUND
            )
        data = build_stats(target, 0, False, date_from, date_to)
        if not manager:
            # Agent widzi cudze liczby rozmów, ale nie stan leadów i terminów innego agenta.
            data["total_leads"] = 0
            data["leads_by_status"] = {code: 0 for code in data["leads_by_status"]}
            data["scheduled"] = {key: 0 for key in data["scheduled"]}
            for row in data["agents"]:
                row["leads_total"] = row["leads_active"] = row["leads_won"] = 0
        return Response(StatsSerializer(data).data)


@extend_schema(
    responses=LeaderboardSerializer,
    parameters=PERIOD_PARAMETERS
    + [
        OpenApiParameter(
            "sort",
            str,
            enum=list(LEADERBOARD_SORT_KEYS),
            description="Wg czego układać ranking: sold (domyślnie), calls, interested",
        )
    ],
)
class LeaderboardView(APIView):
    """Ranking agentów (tylko liczby wyników). Agent z wyłączonym podglądem dostaje 403."""

    permission_classes = [IsAgentOrManager]

    def get(self, request):
        return leaderboard_response(request)


def leaderboard_response(request):
    if not can_view_team_stats(request.user):
        raise PermissionDenied(TEAM_STATS_DISABLED)
    date_from, date_to, error = parse_period(request.query_params)
    if error:
        return error
    sort = request.query_params.get("sort", "sold")
    if sort not in LEADERBOARD_SORT_KEYS:
        return Response(
            {"detail": "Parametr sort musi mieć wartość: sold, calls lub interested."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    rows = leaderboard(date_from, date_to, sort, me_id=request.user.pk)
    return Response(
        LeaderboardSerializer(
            {"date_from": date_from, "date_to": date_to, "sort": sort, "results": rows}
        ).data
    )
