from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

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
    CompanySerializer,
    DueLeadsSerializer,
    LeadAssignmentSerializer,
    LeadBatchSerializer,
    LeadDetailSerializer,
    LeadSerializer,
    ProductSerializer,
)
from .services import NoLeadsAvailable, OverdueCallbacks, claim_next_lead, due_leads


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
        # Leady nie znikają: usuwać może tylko manager
        if self.action == "destroy":
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
        serializer.save(agent=self.request.user)


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
    search_fields = ["contact_name", "phone", "email"]
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