import re

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from .excel import MAX_IMPORT_BYTES
from .roles import AGENT, MANAGER
from .models import Activity, Company, Lead, LeadAssignment, LeadBatch, Product


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = "__all__"


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = "__all__"
        read_only_fields = ("created_at",)


class LeadBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadBatch
        fields = "__all__"
        read_only_fields = ("created_by", "created_at")


class ActivitySerializer(serializers.ModelSerializer):
    # Kto prowadził rozmowę (do historii kontaktów)
    agent_name = serializers.SerializerMethodField()

    # Pola tylko do zapisu: kiedy i w jakiej sprawie oddzwonić (trafiają do leada)
    next_contact_at = serializers.DateTimeField(
        write_only=True, required=False, allow_null=True
    )
    next_contact_note = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255
    )

    class Meta:
        model = Activity
        fields = (
            "id",
            "lead",
            "agent",
            "agent_name",
            "outcome",
            "note",
            "created_at",
            "next_contact_at",
            "next_contact_note",
        )
        read_only_fields = ("agent", "created_at")

    def get_agent_name(self, obj):
        return obj.agent.username if obj.agent else None

    def validate_next_contact_at(self, value):
        if value and value < timezone.now():
            raise serializers.ValidationError(
                "Termin kolejnego kontaktu nie może być w przeszłości."
            )
        return value

    def validate(self, attrs):
        if attrs["outcome"] == Activity.Outcome.CALLBACK and (
            not attrs.get("next_contact_at") or not attrs.get("next_contact_note")
        ):
            raise serializers.ValidationError(
                "Wynik 'Prosił o oddzwonienie' wymaga next_contact_at i next_contact_note."
            )
        return attrs


class LeadAssignmentSerializer(serializers.ModelSerializer):
    from_agent_name = serializers.CharField(source="from_agent.username", read_only=True, allow_null=True)
    to_agent_name = serializers.CharField(source="to_agent.username", read_only=True, allow_null=True)

    class Meta:
        model = LeadAssignment
        fields = "__all__"
        read_only_fields = (
            "lead",
            "from_agent",
            "to_agent",
            "reason",
            "assigned_at",
        )


class LeadSerializer(serializers.ModelSerializer):
    # Czytelne nazwy powiązanych obiektów (tabele w panelu nie muszą dopytywać o każdy id)
    company_name = serializers.CharField(source="company.name", read_only=True, allow_null=True)
    company_nip = serializers.CharField(source="company.nip", read_only=True, allow_null=True)
    owner_name = serializers.CharField(source="owner.username", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)
    batch_name = serializers.CharField(source="batch.name", read_only=True, allow_null=True)
    # Od kogo lead został przepisany obecnemu opiekunowi (null = wzięty z puli)
    transferred_from = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = "__all__"
        # Te pola zmienia wyłącznie system (losowanie, przepisywanie, historia),
        # a nie użytkownik wysyłający dane
        read_only_fields = (
            "owner",
            "attempts_count",
            "claimed_at",
            "last_activity_at",
            "created_at",
        )

    def get_transferred_from(self, obj):
        return getattr(obj, "transferred_from_name", None)

    # Walidacja na poziomie pojedynczego pola
    def validate_phone(self, value):
        digits = re.sub(r"\D", "", value)
        if not 9 <= len(digits) <= 15:
            raise serializers.ValidationError(
                "Numer telefonu musi mieć od 9 do 15 cyfr."
            )
        return value.strip()

    def validate_next_contact_at(self, value):
        if value and value < timezone.now():
            raise serializers.ValidationError(
                "Termin kolejnego kontaktu nie może być w przeszłości."
            )
        return value

    # Walidacja na poziomie całego obiektu (reguła biznesowa)
    def validate(self, attrs):
        # Przy częściowej aktualizacji (PATCH) brakujące pola bierzemy z istniejącego leada
        status = attrs.get("status", getattr(self.instance, "status", Lead.Status.NEW))
        next_at = attrs.get(
            "next_contact_at", getattr(self.instance, "next_contact_at", None)
        )
        note = attrs.get(
            "next_contact_note", getattr(self.instance, "next_contact_note", "")
        )
        if status == Lead.Status.CALLBACK and (not next_at or not note):
            raise serializers.ValidationError(
                "Status 'Do oddzwonienia' wymaga terminu i notatki."
            )
        return attrs


class LeadDetailSerializer(LeadSerializer):
    """Szczegóły leada razem z historią kontaktów i przypisań."""

    activities = ActivitySerializer(many=True, read_only=True)
    assignments = LeadAssignmentSerializer(many=True, read_only=True)


class AgentSerializer(serializers.Serializer):
    """Użytkownik, któremu można przypisywać leady (lista dla panelu managera)."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    full_name = serializers.CharField()
    role = serializers.CharField()
    active_leads = serializers.IntegerField()
    overdue_leads = serializers.IntegerField()
    can_view_team_stats = serializers.BooleanField()


class AgentAccessSerializer(serializers.Serializer):
    """Zmiana uprawnienia agenta (PATCH /api/agents/{id}/, tylko manager)."""

    can_view_team_stats = serializers.BooleanField()


class DueLeadsSerializer(serializers.Serializer):
    """Opis odpowiedzi endpointu my-leads/due (używany głównie w dokumentacji Swagger)."""

    window_minutes = serializers.IntegerField()
    overdue = LeadSerializer(many=True)
    upcoming = LeadSerializer(many=True)


def assignable_users():
    """Użytkownicy, którym można przypisać leada: agenci i managerowie."""
    return get_user_model().objects.filter(
        is_active=True, groups__name__in=[AGENT, MANAGER]
    ).distinct()


class AssignLeadSerializer(serializers.Serializer):
    """Dane do przypisania leada: agent (null = zwrot do puli) i powód."""

    agent = serializers.PrimaryKeyRelatedField(
        queryset=assignable_users(), allow_null=True
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ReassignLeadsSerializer(serializers.Serializer):
    """Przepisanie wszystkich aktywnych leadów jednego agenta na drugiego."""

    from_agent = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.all())
    to_agent = serializers.PrimaryKeyRelatedField(queryset=assignable_users())
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        if attrs["from_agent"] == attrs["to_agent"]:
            raise serializers.ValidationError("Agenci muszą być różni.")
        return attrs


class ScheduleSerializer(serializers.Serializer):
    """Opis odpowiedzi my-leads/schedule (plan dnia agenta)."""

    date = serializers.DateField()
    overdue = LeadSerializer(many=True)
    scheduled = LeadSerializer(many=True)


class AgentStatsSerializer(serializers.Serializer):
    agent = serializers.IntegerField()
    username = serializers.CharField()
    leads_total = serializers.IntegerField()
    leads_active = serializers.IntegerField()
    leads_won = serializers.IntegerField()
    calls = serializers.IntegerField()
    answered = serializers.IntegerField()
    interested = serializers.IntegerField()
    refused = serializers.IntegerField()
    sold = serializers.IntegerField()
    no_answer = serializers.IntegerField()
    callbacks = serializers.IntegerField()
    wrong_number = serializers.IntegerField()
    answer_rate = serializers.FloatField(allow_null=True)
    interest_rate = serializers.FloatField(allow_null=True)


class DailyCallsSerializer(serializers.Serializer):
    date = serializers.DateField()
    calls = serializers.IntegerField()


class HourlyCallsSerializer(serializers.Serializer):
    hour = serializers.IntegerField()
    calls = serializers.IntegerField()


class StatsSerializer(serializers.Serializer):
    """Opis odpowiedzi /api/stats/ (lejek, skuteczność agentów, rozmowy dzień po dniu)."""

    days = serializers.IntegerField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    total_leads = serializers.IntegerField()
    pool_leads = serializers.IntegerField(allow_null=True)
    leads_by_status = serializers.DictField(child=serializers.IntegerField())
    outcomes = serializers.DictField(child=serializers.IntegerField())
    scheduled = serializers.DictField(child=serializers.IntegerField())
    agents = AgentStatsSerializer(many=True)
    daily_calls = DailyCallsSerializer(many=True)
    hourly_calls = HourlyCallsSerializer(many=True)


class ImportLeadsSerializer(serializers.Serializer):
    """Formularz importu: plik .xlsx, opcjonalnie nazwa bazy i produkt dla wszystkich leadów."""

    file = serializers.FileField(help_text="Plik .xlsx; pierwszy wiersz = nagłówki")
    name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=150,
        help_text="Nazwa bazy (domyślnie: nazwa pliku i data)",
    )
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.all(), required=False, allow_null=True
    )

    def validate_file(self, value):
        if not value.name.lower().endswith(".xlsx"):
            raise serializers.ValidationError("Wgraj plik w formacie .xlsx.")
        if value.size > MAX_IMPORT_BYTES:
            raise serializers.ValidationError("Plik jest za duży (max 5 MB).")
        return value


class ImportIssueSerializer(serializers.Serializer):
    row = serializers.IntegerField()
    message = serializers.CharField(required=False)
    phone = serializers.CharField(required=False)
    reason = serializers.CharField(required=False)


class ImportResultSerializer(serializers.Serializer):
    """Raport z importu: ile leadów powstało, co pominięto i dlaczego."""

    batch = serializers.DictField(allow_null=True)
    total_rows = serializers.IntegerField()
    created = serializers.IntegerField()
    duplicates = ImportIssueSerializer(many=True)
    errors = ImportIssueSerializer(many=True)


class LeaderboardRowSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    agent = serializers.IntegerField()
    username = serializers.CharField()
    full_name = serializers.CharField()
    sold = serializers.IntegerField()
    calls = serializers.IntegerField()
    answered = serializers.IntegerField()
    interested = serializers.IntegerField()
    answer_rate = serializers.FloatField(allow_null=True)
    is_me = serializers.BooleanField()


class LeaderboardSerializer(serializers.Serializer):
    """Opis odpowiedzi /api/leaderboard/ (ranking agentów w wybranym okresie)."""

    date_from = serializers.DateField()
    date_to = serializers.DateField()
    sort = serializers.CharField()
    results = LeaderboardRowSerializer(many=True)
