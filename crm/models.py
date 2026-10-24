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
        WON = "won", "Sprzedany"
        LOST = "lost", "Bez sprzedaży"
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
    # Termin, który klient sam wskazał (oddzwonienie / zainteresowany). Zostaje, gdy agent
    # nie dodzwoni się w terminie i system przesuwa ponowną próbę, żeby było widać, o co chodziło.
    agreed_contact_at = models.DateTimeField(null=True, blank=True)
    # Ile razy z rzędu nie odebrał, gdy był umówiony kontakt (zerowane po rozmowie)
    missed_calls = models.PositiveIntegerField(default=0)
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
        # Zasada: lead do oddzwonienia musi mieć termin (sprawa jest opcjonalna)
        if self.status == self.Status.CALLBACK and not self.next_contact_at:
            raise ValidationError("Status 'Do oddzwonienia' wymaga terminu.")


class Activity(models.Model):
    class Outcome(models.TextChoices):
        NO_ANSWER = "no_answer", "Nie odebrał"
        CALLBACK = "callback", "Prosił o oddzwonienie"
        INTERESTED = "interested", "Zainteresowany"
        SOLD = "sold", "Sprzedane"
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
    # Na kiedy był zaplanowany ten kontakt (termin leada w chwili rozmowy) i na kiedy ustawiono
    # kolejny. Razem z created_at pokazują w historii: „miał być o 12:00, zadzwoniono 12:10,
    # następna próba o 13:10”.
    planned_at = models.DateTimeField(null=True, blank=True)
    rescheduled_to = models.DateTimeField(null=True, blank=True)
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


class AgentProfile(models.Model):
    """Ustawienia agenta nadawane przez managera (na razie: podgląd wyników zespołu)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_profile",
    )
    can_view_team_stats = models.BooleanField(
        "może widzieć wyniki innych agentów",
        default=True,
        help_text="Odznacz, aby ukryć ranking i statystyki innych agentów przed tym agentem.",
    )
    must_change_password = models.BooleanField(
        "musi ustawić nowe hasło",
        default=False,
        help_text="Włączane po założeniu konta lub resecie hasła przez managera; zdejmowane po zmianie hasła przez użytkownika.",
    )
    avatar = models.TextField(
        "zdjęcie profilowe",
        blank=True,
        help_text="Małe zdjęcie jako data URL (przeglądarka zmniejsza je przed wysłaniem).",
    )

    class Meta:
        verbose_name = "Profil agenta"
        verbose_name_plural = "Profile agentów"

    def __str__(self):
        return f"Profil: {self.user}"


class TeamRules(models.Model):
    """Zasady pracy z leadami dla całego zespołu (jeden wiersz, zmienia go manager).

    Wartości startowe pochodzą z config/settings.py; po pierwszej zmianie przez managera
    obowiązują te zapisane tutaj. Odczyt: crm.rules.get_rules().
    """

    max_attempts_per_agent = models.PositiveSmallIntegerField(
        "limit prób jednego agenta", default=3
    )
    unreachable_after_agents = models.PositiveSmallIntegerField(
        "ilu agentów bez skutku, by lead był nieosiągalny", default=5
    )
    retry_after_first_minutes = models.PositiveIntegerField(
        "ponowna próba po 1. nieodebraniu (min)", default=120
    )
    retry_after_second_minutes = models.PositiveIntegerField(
        "ponowna próba po 2. nieodebraniu (min)", default=1440
    )
    retry_after_next_minutes = models.PositiveIntegerField(
        "ponowna próba po kolejnych nieodebraniach (min)", default=2880
    )
    work_start_hour = models.PositiveSmallIntegerField("początek dnia pracy (godzina)", default=9)
    work_end_hour = models.PositiveSmallIntegerField("koniec dnia pracy (godzina)", default=17)
    callback_retry_minutes = models.PositiveIntegerField(
        "ponowna próba po nieodebranym umówionym kontakcie (min)", default=60
    )
    claim_timeout_hours = models.PositiveIntegerField(
        "po ilu godzinach nietknięty lead wraca do puli", default=24
    )
    unreachable_cooldown_days = models.PositiveIntegerField(
        "po ilu dniach nieosiągalne leady można zwrócić do obiegu", default=30
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "Ustawienia zespołu"
        verbose_name_plural = "Ustawienia zespołu"

    def __str__(self):
        return "Ustawienia zespołu"
