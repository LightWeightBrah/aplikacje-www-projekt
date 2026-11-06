from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


class Product(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    # Cena jednostkowa z katalogu (ustawia manager). Przy sprzedaży cena jest kopiowana do
    # SaleItem, więc późniejsza zmiana ceny nie zmienia wartości dawnych sprzedaży.
    price = models.DecimalField(
        "cena jednostkowa (zł)",
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    unit = models.CharField("jednostka", max_length=20, default="szt.")
    # Taryfy (abonamenty): cena pierwszego numeru z promocją i dopłata za każdy kolejny numer.
    # Zwykły produkt ma extra_price = 0 i wtedy wartość = cena × ilość.
    promo_price = models.DecimalField(
        "cena z promocją (zł)", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    extra_price = models.DecimalField(
        "każdy kolejny numer (zł)", max_digits=10, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    # Punkty oferty, po jednym w linii (trafiają do treści oferty dla klienta)
    features = models.TextField("co zawiera (punkty oferty)", blank=True)
    sort_order = models.PositiveSmallIntegerField("kolejność", default=100)

    def monthly_total(self, quantity=1, promo=False):
        """Wartość miesięczna: pierwszy numer (z promocją albo bez) + kolejne numery po extra_price."""
        quantity = max(int(quantity or 1), 1)
        first = self.promo_price if promo and self.promo_price is not None else self.price
        if self.extra_price:
            return first + (quantity - 1) * self.extra_price
        return first * quantity

    class Meta:
        verbose_name = "Produkt"
        verbose_name_plural = "Produkty"
        ordering = ["sort_order", "name"]

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
    # Adres z rejestru (CEIDG/KRS): ulica z numerem w jednym polu, np. „ul. Lipowa 5/2”
    street = models.CharField("ulica i numer", max_length=200, blank=True)
    postal_code = models.CharField("kod pocztowy", max_length=10, blank=True)
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
    # Baza przypisana jednemu agentowi (jak „własny Excel”): tylko on losuje z niej leady.
    # Puste = wspólna pula dla wszystkich.
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_batches",
        verbose_name="przypisana agentowi",
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
    # Osoba kontaktowa jest opcjonalna: w bazach z CEIDG zwykle znamy tylko firmę
    contact_name = models.CharField("osoba kontaktowa", max_length=150, blank=True)
    phone = models.CharField(max_length=30, db_index=True)
    phone_alt = models.CharField("dodatkowy telefon", max_length=30, blank=True)
    email = models.EmailField(blank=True)
    # Obecna sieć klienta (kody z OPERATORS, po przecinku, np. „play,orange”)
    operators = models.CharField("obecny operator", max_length=60, blank=True)
    # Stałe informacje o kliencie (nie wpis w historii): np. „ma 5 numerów w Play, umowa do marca”
    client_comment = models.TextField("informacje o kliencie", blank=True)
    # Kto i kiedy ostatnio zmienił te informacje (widać przy polu, np. „Anna, dziś 14:20”)
    client_comment_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="informacje zmienił",
    )
    client_comment_at = models.DateTimeField("informacje zmienione", null=True, blank=True)
    # Czy klient wie, skąd dzwonimy (przedstawiliśmy ofertę T-Mobile), czy tylko umówiliśmy termin
    knows_caller = models.BooleanField("wie, skąd dzwonimy", default=False)
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
        return f"{self.display_name} ({self.phone})"

    @property
    def display_name(self):
        """Najważniejsza nazwa leada: firma, a gdy jej brak osoba kontaktowa albo numer."""
        if self.company_id and self.company.name:
            return self.company.name
        return self.contact_name or self.phone

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
        # Rozmowa, po której wysłano ofertę (szczegóły w Offer); lead czeka na decyzję klienta
        OFFER = "offer", "Wysłana oferta"
        # Agent odrzuca leada bez dzwonienia (np. firma już nie istnieje); powód w skip_reason
        SKIPPED = "skipped", "Pominięty bez dzwonienia"

    class SkipReason(models.TextChoices):
        CLOSED = "closed", "Firma nie istnieje / zawieszona"
        HAS_TMOBILE = "has_tmobile", "Już jest w T-Mobile"
        NO_PHONE = "no_phone", "Brak numeru / numer stacjonarny"
        DUPLICATE = "duplicate", "Duplikat"
        OTHER = "other", "Inny powód"

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
    skip_reason = models.CharField(
        "powód pominięcia", max_length=20, choices=SkipReason.choices, blank=True
    )
    note = models.TextField(blank=True)
    # Ile sekund trwała rozmowa (wypełnia aplikacja dialera z rejestru połączeń telefonu; puste = nie wiadomo).
    # Z tego liczymy minuty rozmów na godzinę (cel managera: min. 15 min na godzinę pracy).
    call_seconds = models.PositiveIntegerField("czas rozmowy (s)", null=True, blank=True)
    # Na kiedy był zaplanowany ten kontakt (termin leada w chwili rozmowy) i na kiedy ustawiono
    # kolejny. Razem z created_at pokazują w historii: „miał być o 12:00, zadzwoniono 12:10,
    # następna próba o 13:10”.
    planned_at = models.DateTimeField(null=True, blank=True)
    rescheduled_to = models.DateTimeField(null=True, blank=True)
    # Stan leada sprzed tego wpisu: pozwala cofnąć ostatni zapis tuż po pomyłce (patrz services.undo_activity)
    undo_state = models.JSONField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Aktywność"
        verbose_name_plural = "Aktywności"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.lead} - {self.get_outcome_display()}"


# Wpisy, które nie są telefonem (nie liczą się jako próba ani rozmowa w statystykach)
NON_CALL_OUTCOMES = (Activity.Outcome.NOTE, Activity.Outcome.SKIPPED)

# Sieci, z których przenosimy klientów (kod, nazwa); kolejność = kolejność przycisków w karcie leada
OPERATORS = (
    ("play", "Play"),
    ("orange", "Orange"),
    ("plus", "Plus"),
    ("tmobile", "T-Mobile"),
    ("other", "Inna sieć"),
)
OPERATOR_CODES = tuple(code for code, _ in OPERATORS)


class SaleItem(models.Model):
    """Jedna pozycja sprzedaży: co i ile sprzedano w rozmowie z wynikiem „Sprzedane”.

    Nazwa, jednostka i cena są kopiowane z katalogu w chwili sprzedaży (zmiana ceny albo
    nazwy produktu później nie przepisuje historii), a `total` = ilość × cena jednostkowa.
    Data i agent sprzedaży to data i agent rozmowy (`activity`); usunięcie rozmowy
    (np. cofnięcie zapisu) usuwa też jej pozycje.
    """

    activity = models.ForeignKey(
        Activity,
        on_delete=models.CASCADE,
        related_name="sale_items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sale_items",
    )
    product_name = models.CharField(max_length=120)
    unit = models.CharField(max_length=20, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    total = models.DecimalField(max_digits=14, decimal_places=2)
    # Taryfy: na ile miesięcy umowa i ile wynosi prowizja (zobowiązanie × mnożnik z ustawień ofert)
    term_months = models.PositiveSmallIntegerField("okres umowy (mies.)", null=True, blank=True)
    promo = models.BooleanField("z promocją", default=False)
    commission = models.DecimalField("prowizja (zł)", max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Pozycja sprzedaży"
        verbose_name_plural = "Pozycje sprzedaży"
        ordering = ["id"]

    def __str__(self):
        return f"{self.product_name} × {self.quantity} = {self.total}"


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
    show_in_ranking = models.BooleanField(
        "pokazuj w rankingu",
        default=True,
        help_text="Dotyczy managerów (agenci są w rankingu zawsze): odznacz, gdy manager nie dzwoni.",
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
    # Dni pracy: cyfry 1–7 (1 = poniedziałek … 7 = niedziela), np. „12345” = pon–pt, „123456” = pon–sob
    work_days = models.CharField("dni pracy", max_length=7, default="12345")
    # Kolejne ponowne próby, gdy klient nie odebrał w umówionym terminie (minuty po nieodebranym telefonie):
    # 1. próba za [0], 2. za [1] … po ostatniej kolejne co tyle, ile ostatnia
    callback_retry_steps = models.JSONField(
        "ponowne próby po nieodebranym umówionym kontakcie (min)", default=list
    )
    claim_timeout_hours = models.PositiveIntegerField(
        "po ilu godzinach nietknięty lead wraca do puli", default=24
    )
    unreachable_cooldown_days = models.PositiveIntegerField(
        "po ilu dniach nieosiągalne leady można zwrócić do obiegu", default=30
    )
    # Losowanie nowych firm: False = wszystko losowo, True = najpierw firmy jednoosobowe, potem spółki
    companies_last = models.BooleanField("spółki na końcu przy losowaniu", default=False)
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


class OfferConfig(models.Model):
    """Ustawienia ofert (jeden wiersz, zmienia manager): dokąd idą oferty i jak liczymy prowizję.

    Tryb „forward”: oferta idzie na jeden adres (np. osoby, która ma firmowy e-mail i wysyła ją
    klientowi). Tryb „direct”: oferta idzie od razu do klienta (gdy agenci mają własne adresy).
    """

    class Mode(models.TextChoices):
        FORWARD = "forward", "Na jeden adres (np. do osoby wysyłającej oferty)"
        DIRECT = "direct", "Od razu do klienta"

    mode = models.CharField("sposób wysyłki", max_length=10, choices=Mode.choices, default=Mode.FORWARD)
    forward_email = models.EmailField("adres do przekazywania ofert", blank=True)
    forward_name = models.CharField("kto wysyła oferty (imię)", max_length=60, blank=True, default="Czarek")
    commission_24 = models.DecimalField("mnożnik prowizji – umowa 24 mies.", max_digits=5, decimal_places=2, default=6)
    commission_36 = models.DecimalField("mnożnik prowizji – umowa 36 mies.", max_digits=5, decimal_places=2, default=9)
    default_term = models.PositiveSmallIntegerField("domyślny okres umowy (mies.)", default=36)
    promo_label = models.CharField("nazwa promocji", max_length=80, blank=True, default="6 miesięcy gratis")
    greeting = models.TextField(
        "początek oferty", blank=True,
        default="Szanowni Państwo,\n\nnawiązując do naszej rozmowy, przesyłam propozycję przeniesienia usług do sieci T-Mobile, dopasowaną do Państwa potrzeb.",
    )
    closing = models.TextField("zakończenie oferty", blank=True, default="W razie pytań zapraszam do kontaktu.")
    signature = models.CharField("podpis pod ofertą", max_length=120, blank=True, default="Zespół Ofert T-Mobile")
    contact_phone = models.CharField(
        "telefon do kontaktu w ofercie", max_length=30, blank=True,
        help_text="Puste: w ofercie nie ma linii z telefonem (osoba wysyłająca może ją dopisać).",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = "Ustawienia ofert"
        verbose_name_plural = "Ustawienia ofert"

    def __str__(self):
        return "Ustawienia ofert"

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create()

    def multiplier(self, term_months):
        return self.commission_24 if int(term_months or 0) == 24 else self.commission_36


class Offer(models.Model):
    """Oferta przygotowana dla klienta (zapisana razem z rozmową „Wysłana oferta”)."""

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="offers")
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="offers")
    activity = models.OneToOneField(Activity, on_delete=models.SET_NULL, null=True, blank=True, related_name="offer")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="offers")
    product_name = models.CharField(max_length=120)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    term_months = models.PositiveSmallIntegerField(default=36)
    promo = models.BooleanField(default=False)
    monthly_total = models.DecimalField(max_digits=12, decimal_places=2)
    commission = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    client_email = models.EmailField(blank=True)
    recipient = models.EmailField("wysłano na adres", blank=True)
    sent = models.BooleanField("wysłana przez system", default=False)
    # Gotowy list do klienta (jak we wzorniku ofert): treść maila HTML i załącznik oferta-….html
    html = models.TextField("oferta jako HTML", blank=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Oferta"
        verbose_name_plural = "Oferty"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product_name} ×{self.quantity} dla {self.lead}"


class DialerDevice(models.Model):
    """Telefon agenta z aplikacją dialera (sparowany kodem z CRM)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dialer_devices")
    name = models.CharField(max_length=80, blank=True)
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Telefon z dialerem"
        verbose_name_plural = "Telefony z dialerem"

    def __str__(self):
        return f"{self.name or 'Telefon'} ({self.user})"


class DialerPairing(models.Model):
    """Jednorazowy kod parowania (ważny kilka minut), pokazywany w CRM jako kod QR i cyfry."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    code = models.CharField(max_length=8, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)


class CallRequest(models.Model):
    """Połączenie zlecone z CRM: telefon je wybiera i odsyła wynik (czy odebrał, ile trwało)."""

    class State(models.TextChoices):
        PENDING = "pending", "Czeka na telefon"
        DIALING = "dialing", "Telefon dzwoni"
        DONE = "done", "Zakończone"
        CANCELLED = "cancelled", "Anulowane"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="call_requests")
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True, related_name="call_requests")
    phone = models.CharField(max_length=30)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING, db_index=True)
    answered = models.BooleanField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Połączenie z dialera"
        verbose_name_plural = "Połączenia z dialera"
        ordering = ["-created_at"]


class Announcement(models.Model):
    """Informacje dla zespołu (panel „Solid info” obok dzwonienia): promocje, argumenty, ogłoszenia."""

    title = models.CharField("tytuł", max_length=120)
    body = models.TextField("treść", blank=True)
    pinned = models.BooleanField("przypięte na górze", default=False)
    is_active = models.BooleanField("widoczne", default=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Informacja dla zespołu"
        verbose_name_plural = "Informacje dla zespołu"
        ordering = ["-pinned", "-created_at"]

    def __str__(self):
        return self.title


class WorkDay(models.Model):
    """Dzień pracy agenta: kiedy przyszedł i kiedy wyszedł.

    `first_seen` i `last_seen` ustawia CRM sam przy prawdziwych ruchach agenta (logowanie, otwarcie
    „Dzwonienia”, „Następny klient”, zapis rozmowy, telefon) – samo odświeżanie strony w tle się nie liczy.
    `ended_at` – agent się wylogował; kolejny ruch w CRM tego dnia znów go „otwiera”.
    `breaks` – przerwy (co najmniej 15 min bez ruchu) jako lista par [początek, koniec] w ISO.
    Przerwy tylko pokazujemy – czas pracy liczy się od przyjścia do wyjścia razem z nimi.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="work_days")
    date = models.DateField("dzień")
    first_seen = models.DateTimeField("początek pracy")
    last_seen = models.DateTimeField("ostatnia aktywność")
    ended_at = models.DateTimeField("wylogował się", null=True, blank=True)
    breaks = models.JSONField("przerwy", default=list, blank=True)

    class Meta:
        verbose_name = "Dzień pracy"
        verbose_name_plural = "Dni pracy"
        ordering = ["-date", "user__username"]
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="unique_work_day")]

    def __str__(self):
        return f"{self.user} {self.date}"


class AccessDevice(models.Model):
    """Komputer albo telefon, który może otworzyć CRM (jak certyfikat w przeglądarce).

    Na serwerze firmy (DEVICE_LOCK) urządzenie bez aktywacji widzi tylko ekran
    „To urządzenie nie ma dostępu” – nawet ekranu logowania. Aktywacja = jednorazowy kod
    od managera; przeglądarka dostaje wtedy tajny klucz w ciasteczku. W bazie trzymamy
    tylko skrót (hash) klucza, więc wyciek bazy nie daje dostępu.
    """

    name = models.CharField("nazwa urządzenia", max_length=80)
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="kod wydał")
    created_at = models.DateTimeField("aktywowane", auto_now_add=True)
    last_seen = models.DateTimeField("ostatnio używane", null=True, blank=True)
    revoked_at = models.DateTimeField("zablokowane", null=True, blank=True)

    class Meta:
        verbose_name = "Urządzenie z dostępem"
        verbose_name_plural = "Urządzenia z dostępem"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ActivationCode(models.Model):
    """Jednorazowy kod aktywacji urządzenia (ważny 24 h). W bazie tylko skrót kodu."""

    code_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    device = models.ForeignKey(AccessDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "Kod aktywacji urządzenia"
        verbose_name_plural = "Kody aktywacji urządzeń"
