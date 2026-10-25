import re

from django.contrib.auth import get_user_model
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .excel import MAX_IMPORT_BYTES
from .roles import AGENT, MANAGER
from .models import Activity, Company, Lead, LeadAssignment, LeadBatch, Product, TeamRules
from .rules import FIELD_LIMITS, attempt_times, get_rules, suggest_retry


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
            "planned_at",
            "rescheduled_to",
            "next_contact_at",
            "next_contact_note",
        )
        read_only_fields = ("agent", "created_at", "planned_at", "rescheduled_to")

    @extend_schema_field(OpenApiTypes.STR)
    def get_agent_name(self, obj):
        return obj.agent.username if obj.agent else None

    def validate_next_contact_at(self, value):
        if value and value < timezone.now():
            raise serializers.ValidationError(
                "Termin kolejnego kontaktu nie może być w przeszłości."
            )
        return value

    def validate(self, attrs):
        # Kontakt obiecany klientowi (oddzwonić / zainteresowany) musi mieć termin; sprawa jest opcjonalna
        if attrs["outcome"] == Activity.Outcome.CALLBACK and not attrs.get("next_contact_at"):
            raise serializers.ValidationError(
                "Wynik 'Prosił o oddzwonienie' wymaga next_contact_at."
            )
        if attrs["outcome"] == Activity.Outcome.INTERESTED and not attrs.get("next_contact_at"):
            raise serializers.ValidationError(
                "Wynik 'Zainteresowany' wymaga next_contact_at (kiedy ponownie zadzwonić)."
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
    company_krs = serializers.CharField(source="company.krs", read_only=True, allow_null=True)
    owner_name = serializers.CharField(source="owner.username", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)
    batch_name = serializers.CharField(source="batch.name", read_only=True, allow_null=True)
    # Od kogo lead został przepisany obecnemu opiekunowi (null = wzięty z puli)
    transferred_from = serializers.SerializerMethodField()
    # Ile razy obecny opiekun nie dodzwonił się do leada i kiedy system proponuje następną próbę
    owner_no_answers = serializers.SerializerMethodField()
    suggested_retry_at = serializers.SerializerMethodField()
    # Ostatni kontakt (bez samych notatek): do linii „Ostatnio: Nie odebrał · dziś 22:09” na listach
    last_contact_outcome = serializers.SerializerMethodField()
    last_contact_at = serializers.SerializerMethodField()
    last_contact_agent = serializers.SerializerMethodField()

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
            "agreed_contact_at",
            "missed_calls",
        )

    @extend_schema_field(OpenApiTypes.STR)
    def get_last_contact_outcome(self, obj):
        return getattr(obj, "last_contact_outcome", None)

    @extend_schema_field(OpenApiTypes.DATETIME)
    def get_last_contact_at(self, obj):
        value = getattr(obj, "last_contact_at", None)
        return value.isoformat() if value else None

    @extend_schema_field(OpenApiTypes.INT)
    def get_last_contact_agent(self, obj):
        return getattr(obj, "last_contact_agent", None)

    @extend_schema_field(OpenApiTypes.STR)
    def get_transferred_from(self, obj):
        return getattr(obj, "transferred_from_name", None)

    def _rules(self):
        # jedno zapytanie o ustawienia na całą listę leadów
        if "team_rules" not in self.context:
            self.context["team_rules"] = get_rules()
        return self.context["team_rules"]

    @extend_schema_field(OpenApiTypes.INT)
    def get_owner_no_answers(self, obj):
        if obj.owner_id is None:
            return 0
        value = getattr(obj, "owner_no_answers", None)
        if value is None:
            value = Activity.objects.filter(
                lead=obj, agent_id=obj.owner_id, outcome=Activity.Outcome.NO_ANSWER
            ).count()
        return value

    @extend_schema_field(OpenApiTypes.DATETIME)
    def get_suggested_retry_at(self, obj):
        """Termin ponownej próby, który system ustawi po kolejnym nieodebraniu (null = ostatnia próba)."""
        if obj.owner_id is None or obj.status != Lead.Status.NEW:
            return None
        tries = self.get_owner_no_answers(obj)
        rules = self._rules()
        if tries + 1 >= rules.max_attempts_per_agent:
            return None
        acts = getattr(obj, "no_answer_acts", None)
        if acts is None:
            acts = Activity.objects.filter(lead=obj, outcome=Activity.Outcome.NO_ANSWER)
        now = timezone.now()
        earlier = attempt_times(rules, [(a.created_at, a.rescheduled_to) for a in acts]) + [now]   # + próba, którą agent właśnie robi
        return suggest_retry(rules, tries, now, earlier).isoformat()

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
        if status == Lead.Status.CALLBACK and not next_at:
            raise serializers.ValidationError("Status 'Do oddzwonienia' wymaga terminu.")
        if status == Lead.Status.INTERESTED and not next_at:
            raise serializers.ValidationError("Status 'Zainteresowany' wymaga terminu kolejnego kontaktu.")
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


class AgentAccessBulkSerializer(serializers.Serializer):
    """Odpowiedź POST /api/agents/team-access/: ustawiona wartość i liczba zmienionych agentów."""

    can_view_team_stats = serializers.BooleanField()
    updated = serializers.IntegerField()


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
    avatar = serializers.CharField(help_text="Zdjęcie profilowe (data URL) albo pusty tekst.")
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


class TeamRulesSerializer(serializers.ModelSerializer):
    """Ustawienia zespołu (limit prób, ponowne próby, zwroty do puli) – zmienia je manager."""

    updated_by_name = serializers.CharField(source="updated_by.username", read_only=True, default=None)

    class Meta:
        model = TeamRules
        fields = (
            "max_attempts_per_agent",
            "unreachable_after_agents",
            "retry_after_first_minutes",
            "retry_after_second_minutes",
            "retry_after_next_minutes",
            "work_start_hour",
            "work_end_hour",
            "callback_retry_minutes",
            "claim_timeout_hours",
            "unreachable_cooldown_days",
            "updated_at",
            "updated_by_name",
        )
        read_only_fields = ("updated_at", "updated_by_name")

    def validate(self, attrs):
        for name, value in attrs.items():
            lo, hi, unit, _ = FIELD_LIMITS[name]
            if not lo <= value <= hi:
                raise serializers.ValidationError(
                    {name: f"Wartość musi być z zakresu od {lo} do {hi} ({unit})."}
                )
        start = attrs.get("work_start_hour", getattr(self.instance, "work_start_hour", 8))
        end = attrs.get("work_end_hour", getattr(self.instance, "work_end_hour", 20))
        if start >= end:
            raise serializers.ValidationError(
                {"work_end_hour": "Koniec dnia pracy musi być później niż jego początek."}
            )
        return attrs
