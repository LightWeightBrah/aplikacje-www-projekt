import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .excel import MAX_IMPORT_BYTES
from .roles import AGENT, MANAGER
from .models import (
    OPERATOR_CODES, Activity, Company, Lead, LeadAssignment, LeadBatch, Product, SaleItem, TeamRules,
)
from .rules import (
    CALLBACK_STEP_LIMITS,
    CALLBACK_STEPS_MAX,
    FIELD_LIMITS,
    attempt_times,
    get_rules,
    suggest_retry,
)


class ProductSerializer(serializers.ModelSerializer):
    """Produkt z katalogu: nazwa, cena jednostkowa (zł) i jednostka. Zmienia je manager."""

    class Meta:
        model = Product
        fields = "__all__"


# Największa ilość jednej pozycji w jednej sprzedaży (zabezpieczenie przed literówką, np. 5000 zamiast 5)
MAX_SALE_QUANTITY = 9999


class SaleItemSerializer(serializers.ModelSerializer):
    """Pozycja sprzedaży: co i ile sprzedano, z ceną z chwili sprzedaży."""

    class Meta:
        model = SaleItem
        fields = ("id", "product", "product_name", "unit", "unit_price", "quantity", "total", "term_months", "promo", "commission")
        read_only_fields = fields


class SaleLineSerializer(serializers.Serializer):
    """Wejście: jedna sprzedawana pozycja (id produktu z katalogu i ilość)."""

    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_SALE_QUANTITY)
    # taryfy: okres umowy (z niego liczy się prowizja) i czy z promocją
    term_months = serializers.ChoiceField(choices=(24, 36), required=False, allow_null=True)
    promo = serializers.BooleanField(required=False, default=False)


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = "__all__"
        read_only_fields = ("created_at",)


class LeadBatchSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(source="assigned_to.username", read_only=True, allow_null=True)

    class Meta:
        model = LeadBatch
        fields = "__all__"
        read_only_fields = ("created_by", "created_at")

    def validate_assigned_to(self, user):
        if user is not None and not user.is_active:
            raise serializers.ValidationError("To konto jest nieaktywne.")
        return user


class ActivityNoteSerializer(serializers.Serializer):
    """Poprawa komentarza w już zapisanym kontakcie (zmienia się tylko treść notatki)."""

    note = serializers.CharField(allow_blank=True, max_length=2000, trim_whitespace=True)


class ActivitySerializer(serializers.ModelSerializer):
    # Kto prowadził rozmowę (do historii kontaktów)
    agent_name = serializers.SerializerMethodField()
    # Nazwa klienta (firma albo osoba) – lista „Historia dziś” w dzwonieniu
    lead_name = serializers.CharField(source="lead.display_name", read_only=True)

    # Pola tylko do zapisu: kiedy i w jakiej sprawie oddzwonić (trafiają do leada)
    next_contact_at = serializers.DateTimeField(
        write_only=True, required=False, allow_null=True
    )
    next_contact_note = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255
    )
    # Tylko do zapisu, przy wyniku „Sprzedane”: co sprzedano i ile sztuk. Wartość liczy system
    # z cen katalogu (pozycje wracają w `sale_items`, suma w `sale_total`).
    sale_lines = SaleLineSerializer(many=True, write_only=True, required=False)
    sale_items = serializers.SerializerMethodField()
    sale_total = serializers.SerializerMethodField()
    # Tylko do zapisu: czy klient wie, skąd dzwonimy (przy umówionym oddzwonieniu)
    introduced = serializers.BooleanField(write_only=True, required=False, allow_null=True, default=None)
    # Krótko o ofercie wysłanej w tej rozmowie (null = bez oferty)
    offer = serializers.SerializerMethodField()

    class Meta:
        model = Activity
        fields = (
            "id",
            "lead",
            "agent",
            "agent_name",
            "lead_name",
            "outcome",
            "skip_reason",
            "note",
            "call_seconds",
            "created_at",
            "planned_at",
            "rescheduled_to",
            "next_contact_at",
            "next_contact_note",
            "sale_lines",
            "sale_items",
            "sale_total",
            "introduced",
            "offer",
        )
        # call_seconds ustawi aplikacja dialera osobnym endpointem, nie formularz rozmowy
        read_only_fields = ("agent", "created_at", "planned_at", "rescheduled_to", "call_seconds")

    @extend_schema_field(OpenApiTypes.STR)
    def get_agent_name(self, obj):
        return obj.agent.username if obj.agent else None

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_offer(self, obj):
        if obj.outcome != Activity.Outcome.OFFER:
            return None
        offer = getattr(obj, "offer", None)
        if offer is None:
            return None
        return {
            "id": offer.pk, "product_name": offer.product_name, "quantity": offer.quantity,
            "term_months": offer.term_months, "promo": offer.promo, "monthly_total": str(offer.monthly_total),
            "commission": str(offer.commission), "client_email": offer.client_email,
            "recipient": offer.recipient, "sent": offer.sent,
        }

    @extend_schema_field(SaleItemSerializer(many=True))
    def get_sale_items(self, obj):
        if obj.outcome != Activity.Outcome.SOLD:
            return []
        return SaleItemSerializer(obj.sale_items.all(), many=True).data

    @extend_schema_field(OpenApiTypes.STR)
    def get_sale_total(self, obj):
        """Wartość sprzedaży (suma pozycji) albo null, gdy to nie sprzedaż lub nie podano pozycji."""
        if obj.outcome != Activity.Outcome.SOLD:
            return None
        items = list(obj.sale_items.all())
        if not items:
            return None
        return str(sum((item.total for item in items), Decimal("0.00")))

    def validate_sale_lines(self, lines):
        """Ten sam produkt wpisany dwa razy to jedna pozycja; sprzedawać można tylko aktywne produkty."""
        merged = {}
        for line in lines:
            product = line["product"]
            if not product.is_active:
                raise serializers.ValidationError(
                    f"Produkt „{product.name}” jest wyłączony ze sprzedaży."
                )
            key = (product.pk, line.get("term_months"), bool(line.get("promo")))
            prev = merged.get(key, (product, 0))[1]
            merged[key] = (product, prev + line["quantity"])
        for (_pk, term, promo), (product, quantity) in merged.items():
            if quantity > MAX_SALE_QUANTITY:
                raise serializers.ValidationError(
                    f"Ilość produktu „{product.name}” nie może przekraczać {MAX_SALE_QUANTITY}."
                )
        return [
            {"product": product, "quantity": quantity, "term_months": term, "promo": promo}
            for (_pk, term, promo), (product, quantity) in merged.items()
        ]

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
        if attrs["outcome"] == Activity.Outcome.OFFER and not attrs.get("next_contact_at"):
            raise serializers.ValidationError("Wysłana oferta wymaga terminu follow-upu (next_contact_at).")
        if attrs["outcome"] == Activity.Outcome.INTERESTED and not attrs.get("next_contact_at"):
            raise serializers.ValidationError(
                "Wynik 'Zainteresowany' wymaga next_contact_at (kiedy ponownie zadzwonić)."
            )
        skipped = attrs["outcome"] == Activity.Outcome.SKIPPED
        if skipped and not attrs.get("skip_reason"):
            raise serializers.ValidationError("Wybierz, dlaczego pomijasz tego leada.")
        if attrs.get("skip_reason") and not skipped:
            raise serializers.ValidationError("Powód pominięcia (skip_reason) podaje się tylko przy wyniku 'skipped'.")
        lines = attrs.get("sale_lines") or []
        if lines and attrs["outcome"] != Activity.Outcome.SOLD:
            raise serializers.ValidationError(
                "Sprzedane produkty (sale_lines) można podać tylko przy wyniku 'Sprzedane'."
            )
        # Gdy w katalogu są aktywne produkty, sprzedaż musi mówić, co sprzedano (bez tego nie ma przychodu).
        # Pusty katalog nie blokuje agenta: wtedy sprzedaż zapisuje się bez wartości.
        if (
            attrs["outcome"] == Activity.Outcome.SOLD
            and not lines
            and Product.objects.filter(is_active=True).exists()
        ):
            raise serializers.ValidationError(
                "Wynik 'Sprzedane' wymaga listy sprzedanych produktów (sale_lines: produkt i ilość)."
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
    company_regon = serializers.CharField(source="company.regon", read_only=True, allow_null=True)
    company_street = serializers.CharField(source="company.street", read_only=True, allow_null=True)
    company_postal_code = serializers.CharField(source="company.postal_code", read_only=True, allow_null=True)
    company_city = serializers.CharField(source="company.city", read_only=True, allow_null=True)
    # Najważniejsza nazwa do wyświetlania: firma, a bez niej osoba albo numer
    display_name = serializers.CharField(read_only=True)
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
    # Kto ostatnio zmienił „Stałe informacje o kliencie” (imię i nazwisko albo login)
    client_comment_by_name = serializers.SerializerMethodField()

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
            "client_comment_by",
            "client_comment_at",
        )

    @extend_schema_field(OpenApiTypes.STR)
    def get_client_comment_by_name(self, obj):
        user = obj.client_comment_by
        return (user.get_full_name() or user.username) if user else None

    def update(self, instance, validated_data):
        # Zmiana stałych informacji: zapamiętujemy, kto i kiedy (pokazujemy przy polu)
        new = validated_data.get("client_comment")
        if new is not None and new.strip() != (instance.client_comment or "").strip():
            request = self.context.get("request")
            validated_data["client_comment_by"] = request.user if request and request.user.is_authenticated else None
            validated_data["client_comment_at"] = timezone.now()
        return super().update(instance, validated_data)

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

    def validate_phone_alt(self, value):
        value = value.strip()
        if value and not 9 <= len(re.sub(r"\D", "", value)) <= 15:
            raise serializers.ValidationError("Dodatkowy telefon musi mieć od 9 do 15 cyfr.")
        return value

    def validate_operators(self, value):
        """Kody sieci po przecinku (np. „play,orange”); kolejność jak w OPERATORS, bez powtórzeń."""
        codes = [c.strip().lower() for c in value.split(",") if c.strip()]
        unknown = [c for c in codes if c not in OPERATOR_CODES]
        if unknown:
            raise serializers.ValidationError(
                f"Nieznany operator: {', '.join(unknown)}. Dozwolone: {', '.join(OPERATOR_CODES)}."
            )
        return ",".join(c for c in OPERATOR_CODES if c in codes)

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
    # segment pracy: offer / callback / retry / new / closed (do kolorów i list w „Dzwonieniu”)
    segment = serializers.SerializerMethodField()

    @extend_schema_field(OpenApiTypes.STR)
    def get_segment(self, obj):
        from .work import segment_of
        return segment_of(obj)


class AgentSerializer(serializers.Serializer):
    """Użytkownik, któremu można przypisywać leady (lista dla panelu managera)."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    full_name = serializers.CharField()
    role = serializers.CharField()
    active_leads = serializers.IntegerField()
    overdue_leads = serializers.IntegerField()
    can_view_team_stats = serializers.BooleanField()
    show_in_ranking = serializers.BooleanField()


class AgentAccessBulkSerializer(serializers.Serializer):
    """Odpowiedź POST /api/agents/team-access/: ustawiona wartość i liczba zmienionych agentów."""

    can_view_team_stats = serializers.BooleanField()
    updated = serializers.IntegerField()


class AgentAccessSerializer(serializers.Serializer):
    """Zmiana uprawnienia agenta (PATCH /api/agents/{id}/, tylko manager).

    Agentowi zmienia się `can_view_team_stats`, managerowi `show_in_ranking`.
    """

    can_view_team_stats = serializers.BooleanField(required=False)
    show_in_ranking = serializers.BooleanField(required=False)


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
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    no_answer = serializers.IntegerField()
    callbacks = serializers.IntegerField()
    wrong_number = serializers.IntegerField()
    answer_rate = serializers.FloatField(allow_null=True)
    interest_rate = serializers.FloatField(allow_null=True)
    offers = serializers.IntegerField()
    commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    calls_per_sale = serializers.FloatField(allow_null=True)
    # tylko w widoku całego zespołu (manager): minuty rozmów, cel i czas pracy w okresie
    minutes = serializers.FloatField(required=False)
    goal = serializers.IntegerField(required=False)
    worked_minutes = serializers.IntegerField(required=False)


class DailyCallsSerializer(serializers.Serializer):
    date = serializers.DateField()
    calls = serializers.IntegerField()


class HourlyCallsSerializer(serializers.Serializer):
    hour = serializers.IntegerField()
    calls = serializers.IntegerField()


class ProductSalesSerializer(serializers.Serializer):
    """Sprzedaż jednego produktu w okresie: ile sztuk i na jaką kwotę."""

    product_name = serializers.CharField()
    unit = serializers.CharField(allow_blank=True)
    quantity = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)


class StatsSerializer(serializers.Serializer):
    """Opis odpowiedzi /api/stats/ (lejek, skuteczność agentów, rozmowy dzień po dniu, przychód)."""

    days = serializers.IntegerField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    revenue = serializers.DecimalField(
        max_digits=14, decimal_places=2, help_text="Przychód ze sprzedaży w okresie (zł)."
    )
    commission = serializers.DecimalField(
        max_digits=14, decimal_places=2, default=0, help_text="Prowizja ze sprzedaży w okresie (zł)."
    )
    sales_by_product = ProductSalesSerializer(many=True)
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
    assigned_to = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.filter(is_active=True),
        required=False,
        allow_null=True,
        help_text="Agent, dla którego jest ta baza (puste = wspólna pula)",
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
    layout = serializers.CharField(help_text="headers = kolumny po nazwach, ceidg = układ bazy z CEIDG")
    duplicates = ImportIssueSerializer(many=True)
    errors = ImportIssueSerializer(many=True)
    warnings = ImportIssueSerializer(many=True)


class LeaderboardRowSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    agent = serializers.IntegerField()
    username = serializers.CharField()
    full_name = serializers.CharField()
    avatar = serializers.CharField(help_text="Zdjęcie profilowe (data URL) albo pusty tekst.")
    sold = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    calls = serializers.IntegerField()
    answered = serializers.IntegerField()
    interested = serializers.IntegerField()
    answer_rate = serializers.FloatField(allow_null=True)
    minutes = serializers.FloatField(help_text="Minuty rozmów z dialera w okresie")
    commission = serializers.DecimalField(max_digits=14, decimal_places=2)
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
            "work_days",
            "callback_retry_steps",
            "claim_timeout_hours",
            "unreachable_cooldown_days",
            "companies_last",
            "updated_at",
            "updated_by_name",
        )
        read_only_fields = ("updated_at", "updated_by_name")

    def validate(self, attrs):
        for name, value in attrs.items():
            if name not in FIELD_LIMITS:
                continue
            lo, hi, unit, _ = FIELD_LIMITS[name]
            if not lo <= value <= hi:
                raise serializers.ValidationError(
                    {name: f"Wartość musi być z zakresu od {lo} do {hi} ({unit})."}
                )
        if "work_days" in attrs:
            raw = str(attrs["work_days"] or "")
            if not raw or any(c not in "1234567" for c in raw):
                raise serializers.ValidationError({"work_days": "Zaznacz co najmniej jeden dzień pracy."})
            attrs["work_days"] = "".join(sorted(set(raw)))
        if "callback_retry_steps" in attrs:
            steps, (lo, hi) = attrs["callback_retry_steps"], CALLBACK_STEP_LIMITS
            if not isinstance(steps, list) or not 1 <= len(steps) <= CALLBACK_STEPS_MAX:
                raise serializers.ValidationError(
                    {"callback_retry_steps": f"Ustaw od 1 do {CALLBACK_STEPS_MAX} prób."})
            if any(isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi for v in steps):
                raise serializers.ValidationError(
                    {"callback_retry_steps": f"Każda próba: od {lo} min do {hi // 1440} dni."})
        start = attrs.get("work_start_hour", getattr(self.instance, "work_start_hour", 8))
        end = attrs.get("work_end_hour", getattr(self.instance, "work_end_hour", 20))
        if start >= end:
            raise serializers.ValidationError(
                {"work_end_hour": "Koniec dnia pracy musi być później niż jego początek."}
            )
        return attrs
