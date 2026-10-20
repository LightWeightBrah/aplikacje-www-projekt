import re

from django.utils import timezone
from rest_framework import serializers

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
            "outcome",
            "note",
            "created_at",
            "next_contact_at",
            "next_contact_note",
        )
        read_only_fields = ("agent", "created_at")

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


class DueLeadsSerializer(serializers.Serializer):
    """Opis odpowiedzi endpointu my-leads/due (używany głównie w dokumentacji Swagger)."""

    window_minutes = serializers.IntegerField()
    overdue = LeadSerializer(many=True)
    upcoming = LeadSerializer(many=True)