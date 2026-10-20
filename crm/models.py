from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


class Product(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Produkt"
        verbose_name_plural = "Produkty"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Company(models.Model):
    name = models.CharField(max_length=200)
    nip = models.CharField(
        max_length=10,
        blank=True,
        db_index=True,
        validators=[RegexValidator(r"^\d{10}$", "NIP musi mieć dokładnie 10 cyfr.")],
    )
    krs = models.CharField(
        max_length=10,
        blank=True,
        db_index=True,
        validators=[RegexValidator(r"^\d{10}$", "KRS musi mieć dokładnie 10 cyfr.")],
    )
    regon = models.CharField(
        max_length=14,
        blank=True,
        validators=[RegexValidator(r"^(\d{9}|\d{14})$", "REGON ma 9 lub 14 cyfr.")],
    )
    city = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Firma"
        verbose_name_plural = "Firmy"
        ordering = ["name"]

    def __str__(self):
        return self.name


class LeadBatch(models.Model):
    """Paczka leadów, np. jeden import z pliku Excel/CSV (jedna 'baza')."""

    name = models.CharField(max_length=150, unique=True)
    source_file_name = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Baza leadów"
        verbose_name_plural = "Bazy leadów"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Nowy"
        CALLBACK = "callback", "Do oddzwonienia"
        INTERESTED = "interested", "Zainteresowany"
        OFFER = "offer", "Oferta wysłana"
        WON = "won", "Wygrany"
        LOST = "lost", "Przegrany"
        DO_NOT_CALL = "do_not_call", "Nie dzwonić"
        UNREACHABLE = "unreachable", "Nieosiągalny"

    batch = models.ForeignKey(
        LeadBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    # owner = None oznacza, że lead jest w puli (nikt go nie prowadzi)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    contact_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, db_index=True)
    email = models.EmailField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    next_contact_at = models.DateTimeField(null=True, blank=True, db_index=True)
    next_contact_note = models.CharField(max_length=255, blank=True)
    attempts_count = models.PositiveIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Lead"
        verbose_name_plural = "Leady"
        ordering = ["next_contact_at", "-created_at"]

    def __str__(self):
        return f"{self.contact_name} ({self.phone})"

    def clean(self):
        # Zasada: lead do oddzwonienia musi mieć termin i notatkę
        if self.status == self.Status.CALLBACK:
            if not self.next_contact_at or not self.next_contact_note:
                raise ValidationError(
                    "Status 'Do oddzwonienia' wymaga terminu i notatki."
                )


class Activity(models.Model):
    class Outcome(models.TextChoices):
        NO_ANSWER = "no_answer", "Nie odebrał"
        CALLBACK = "callback", "Prosił o oddzwonienie"
        INTERESTED = "interested", "Zainteresowany"
        REFUSED = "refused", "Odmowa"
        WRONG_NUMBER = "wrong_number", "Zły numer"
        NOTE = "note", "Notatka"

    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="activities",
    )
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Aktywność"
        verbose_name_plural = "Aktywności"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.lead} - {self.get_outcome_display()}"


class LeadAssignment(models.Model):
    """Historia przypisań leada: kto go prowadził i dlaczego się zmienił."""

    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    from_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments_from",
    )
    to_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments_to",
    )
    reason = models.CharField(max_length=255, blank=True)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Przypisanie leada"
        verbose_name_plural = "Przypisania leadów"
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.lead}: {self.from_agent} -> {self.to_agent}"