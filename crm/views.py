from rest_framework import viewsets

from .models import Activity, Company, Lead, LeadAssignment, LeadBatch, Product
from .serializers import (
    ActivitySerializer,
    CompanySerializer,
    LeadAssignmentSerializer,
    LeadBatchSerializer,
    LeadDetailSerializer,
    LeadSerializer,
    ProductSerializer,
)


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer


class LeadBatchViewSet(viewsets.ModelViewSet):
    queryset = LeadBatch.objects.all()
    serializer_class = LeadBatchSerializer

    def perform_create(self, serializer):
        # kto utworzył paczkę, ustawiamy automatycznie
        serializer.save(created_by=self.request.user)


class LeadViewSet(viewsets.ModelViewSet):
    queryset = Lead.objects.select_related("product", "company", "owner", "batch")
    filterset_fields = ["status", "owner", "batch", "product"]
    search_fields = ["contact_name", "phone", "email"]
    ordering_fields = ["next_contact_at", "last_activity_at"]

    def get_serializer_class(self):
        # pojedynczy lead pokazujemy razem z historią kontaktów
        if self.action == "retrieve":
            return LeadDetailSerializer
        return LeadSerializer


class ActivityViewSet(viewsets.ModelViewSet):
    queryset = Activity.objects.select_related("lead", "agent")
    serializer_class = ActivitySerializer
    filterset_fields = ["lead", "agent", "outcome"]

    def perform_create(self, serializer):
        # agentem jest zawsze zalogowany użytkownik
        serializer.save(agent=self.request.user)


class LeadAssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = LeadAssignment.objects.all()
    serializer_class = LeadAssignmentSerializer