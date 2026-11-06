import io
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crm.models import (
    Activity,
    AgentProfile,
    Company,
    Lead,
    LeadAssignment,
    LeadBatch,
    Product,
)
from crm.excel import import_leads
from crm.roles import AGENT
from crm.sample_data import build_ceidg_sample_workbook
from crm.models import Announcement, OfferConfig
from crm.services import register_activity
from crm.work import create_offer

BATCH_NAME = "Demo - baza startowa"
CEIDG_BATCH_NAME = "Demo - baza CEIDG agent1"

# Taryfy firmowe (jak we wzorniku ofert zespołu): nazwa, cena netto 1. numeru, cena z promocją,
# każdy kolejny numer, punkty oferty. Ceny zmienia manager na stronie „Taryfy i produkty”.
COMMON = [
    "Nielimitowane rozmowy, SMS-y i MMS-y w kraju",
    "Roaming w UE: 23 GB",
    "Prędkość internetu: 150 Mb/s",
]
UNLIMITED = ["Internet bez limitu", "Karta internetowa bez limitu"]


def features(*extra):
    return chr(10).join(COMMON + list(extra))   # chr(10) = nowa linia: jeden punkt oferty w linii


PRODUCTS = (
    ("S", Decimal("45"), Decimal("50"), Decimal("25"),
     features("Internet: 50 GB przez 3 miesiące, potem 40 GB (współdzielony)", "Karta internetowa: 40 GB")),
    ("M100", Decimal("55"), Decimal("65"), Decimal("25"),
     features("Internet: 100 GB (współdzielony)", "Karta internetowa: 100 GB")),
    ("M", Decimal("70"), Decimal("85"), Decimal("25"), features(*UNLIMITED)),
    ("L", Decimal("85"), Decimal("105"), Decimal("25"), features(*UNLIMITED)),
    ("VIP", Decimal("105"), Decimal("140"), Decimal("25"), features(*UNLIMITED)),
)

ANNOUNCEMENTS = (
    ("Promocja: 6 miesięcy gratis", "Przy umowie na 36 miesięcy klient może dostać 6 miesięcy gratis (cena pierwszego numeru wyższa – patrz taryfy). Zawsze proponuj najpierw 36 miesięcy.", True),
    ("Cel dnia", "Minimum 15 minut rozmów na każdą godzinę pracy. Licznik minut jest w prawym panelu.", False),
    ("Jak zacząć rozmowę", "Dzień dobry, dzwonię w sprawie usług komórkowych dla firmy – czy rozmawiam z osobą, która decyduje o abonamentach?", False),
    ("Spółka na linii? (plakietka „Spółka”)", "Najpierw zapytaj, kto decyduje o telefonach (zarząd, właściciel, biuro). Gdy odbiera sekretariat: poproś o imię decydującego i dobrą godzinę, zapisz to w „Oddzwonić”. Spółki mają zwykle więcej numerów, więc i większą prowizję.", False),
)

COMPANIES = (
    ("Alfa Systemy Sp. z o.o.", "5250000001", "0000100001", "Olsztyn"),
    ("Beta Transport S.A.", "5250000002", "0000100002", "Gdańsk"),
    ("Gamma Biuro Rachunkowe", "5250000003", "", "Warszawa"),
    ("Delta Budownictwo Sp. z o.o.", "5250000004", "0000100004", "Elbląg"),
    ("Omega Logistyka Sp. z o.o.", "5250000005", "0000100005", "Poznań"),
    ("Sigma Serwis Jan Nowak", "5250000006", "", "Ostróda"),
)

POOL_CONTACTS = (
    "Adam Wiśniewski",
    "Barbara Kamińska",
    "Cezary Lewandowski",
    "Dorota Zielińska",
    "Edward Szymański",
    "Filip Woźniak",
    "Grażyna Dąbrowska",
    "Henryk Kozłowski",
    "Irena Jankowska",
    "Janusz Mazur",
    "Karolina Krawczyk",
    "Leszek Piotrowski",
)


class Command(BaseCommand):
    help = "Tworzy dane demo: konta, produkty, firmy, bazę leadów i przykładowe aktywności."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="usuń wcześniej utworzoną bazę demo i utwórz ją od nowa",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        batch = LeadBatch.objects.filter(name=BATCH_NAME).first()
        if batch and not options["reset"]:
            self.stdout.write("Dane demo już istnieją. Użyj --reset, aby utworzyć je od nowa.")
            return
        if batch:
            batch.leads.all().delete()
            batch.delete()

        call_command("setup_roles", "--demo", stdout=self.stdout)
        User = get_user_model()
        agent1 = User.objects.get(username="agent1")
        agent2 = User.objects.get(username="agent2")
        manager = User.objects.get(username="manager1")

        products = []
        for order, (name, price, promo, extra, features) in enumerate(PRODUCTS, start=1):
            product, _ = Product.objects.update_or_create(
                name=name, defaults={"price": price, "promo_price": promo, "extra_price": extra,
                                     "features": features, "unit": "mies.", "sort_order": order, "is_active": True},
            )
            products.append(product)
        # stare ogólne produkty demo (sprzed taryf T-Mobile) usuwamy; sprzedaże pamiętają nazwę i cenę,
        # więc historia i przychód zostają bez zmian
        Product.objects.exclude(pk__in=[p.pk for p in products]).filter(
            name__in=("Internet światłowodowy", "Abonament komórkowy", "Leasing samochodowy")).delete()
        config = OfferConfig.get()
        if not config.forward_email:
            config.forward_email = "oferty@example.com"
            config.save(update_fields=["forward_email"])
        for title, body, pinned in ANNOUNCEMENTS:
            Announcement.objects.get_or_create(title=title, defaults={"body": body, "pinned": pinned, "author": manager})
        companies = [
            Company.objects.get_or_create(
                name=name, defaults={"nip": nip, "krs": krs, "city": city}
            )[0]
            for name, nip, krs, city in COMPANIES
        ]
        batch = LeadBatch.objects.create(
            name=BATCH_NAME, source_file_name="demo.xlsx", created_by=manager
        )

        counter = iter(range(1, 100))

        def new_lead(contact_name, owner=None, **fields):
            number = next(counter)
            fields.setdefault("company", companies[number % len(companies)])
            fields.setdefault("product", products[number % len(products)])
            lead = Lead.objects.create(
                batch=batch,
                owner=owner,
                contact_name=contact_name,
                phone=f"500000{number:03d}",
                email=f"kontakt{number}@example.com",
                **fields,
            )
            if owner:
                LeadAssignment.objects.create(
                    lead=lead,
                    to_agent=owner,
                    reason="Losowanie z puli (leads/next)",
                )
                Lead.objects.filter(pk=lead.pk).update(claimed_at=timezone.now())
            return lead

        now = timezone.now()

        for contact_name in POOL_CONTACTS:
            new_lead(contact_name)

        lead = new_lead("Marek Olszewski", owner=agent1)
        register_activity(lead, agent1, Activity.Outcome.NO_ANSWER, "Nie odbiera")

        lead = new_lead("Anna Wójcik", owner=agent1)
        register_activity(
            lead,
            agent1,
            Activity.Outcome.CALLBACK,
            "Prosiła o kontakt po spotkaniu",
            next_contact_at=now + timedelta(minutes=10),
            next_contact_note="Oddzwonić w sprawie oferty",
        )

        lead = new_lead("Piotr Sikora", owner=agent1)
        register_activity(
            lead,
            agent1,
            Activity.Outcome.CALLBACK,
            "Chce porównać ceny",
            next_contact_at=now + timedelta(hours=5),
            next_contact_note="Przesłać porównanie ofert",
        )

        lead = new_lead("Ewa Baran", owner=agent1)
        create_offer(lead, agent1, products[2], 3, 36, True, lead.email, now + timedelta(days=1),
                     "Zapytać, czy oferta M dla 3 numerów jest OK")
        # oferta, której follow-up właśnie mija: „Następny klient” poda ją jako pierwszą
        lead = new_lead("Karol Nowicki", owner=agent1)
        create_offer(lead, agent1, products[3], 2, 36, False, lead.email, now + timedelta(minutes=1),
                     "Klient miał przeczytać ofertę do dziś")

        lead = new_lead("Tomasz Pawlak", owner=agent1)
        register_activity(lead, agent1, Activity.Outcome.REFUSED, "Ma już ofertę")

        new_lead(
            "Zofia Michalska",
            owner=agent2,
            status=Lead.Status.CALLBACK,
            next_contact_at=now - timedelta(hours=2),
            next_contact_note="Zaległe oddzwonienie w sprawie leasingu",
        )

        lead = new_lead("Robert Górski", owner=agent2)
        register_activity(lead, agent2, Activity.Outcome.WRONG_NUMBER, "Zły numer")

        lead = new_lead("Magdalena Rutkowska", owner=agent2)
        register_activity(
            lead,
            agent2,
            Activity.Outcome.INTERESTED,
            "Prosi o ofertę na maila",
            next_contact_at=now + timedelta(days=1, hours=2),
            next_contact_note="Omówić ofertę przesłaną mailem",
        )

        # Dzisiejsze sprzedaże, żeby ranking "Dziś" nie był pusty na pokazie.
        # (agent, klient, co sprzedano: pary indeks produktu + ilość)
        for owner, contact_name, basket in (
            (agent1, "Julia Adamska", ((0, 1), (1, 3))),
            (agent1, "Kamil Borkowski", ((2, 1),)),
            (agent2, "Lena Czerwińska", ((1, 5),)),
        ):
            lead = new_lead(contact_name, owner=owner)
            register_activity(
                lead,
                owner,
                Activity.Outcome.SOLD,
                "Podpisana umowa",
                sale_items=[(products[index], quantity, 36, False) for index, quantity in basket],
            )

        # Nowy agent z wyłączonym podglądem rankingu (pokaz blokady 403).
        agent3, created = User.objects.get_or_create(username="agent3")
        if created:
            agent3.set_password("demo12345")
            agent3.save()
        agent3.groups.add(Group.objects.get(name=AGENT))
        AgentProfile.objects.update_or_create(
            user=agent3, defaults={"can_view_team_stats": False}
        )

        # Własna baza agent1 w układzie z CEIDG (fikcyjne firmy), jak Excel od managera
        ceidg = LeadBatch.objects.filter(name=CEIDG_BATCH_NAME).first()
        if ceidg:
            ceidg.leads.all().delete()
            ceidg.delete()
        buffer = io.BytesIO()
        build_ceidg_sample_workbook().save(buffer)
        upload = SimpleUploadedFile("baza_ceidg_demo.xlsx", buffer.getvalue())
        report = import_leads(upload, manager, CEIDG_BATCH_NAME, assigned_to=agent1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Gotowe: {batch.leads.count()} leadów w bazie '{BATCH_NAME}' "
                f"i {report['created']} firm w bazie agent1 '{CEIDG_BATCH_NAME}'."
            )
        )
        self.stdout.write(
            "Konta demo: agent1, agent2, manager1 (hasło: demo12345)."
        )
        self.stdout.write(
            "agent2 ma zaległe oddzwonienie, więc POST /api/leads/next/ zwróci mu 409."
        )
        self.stdout.write(
            "agent3 ma wyłączony podgląd rankingu: GET /api/leaderboard/ zwróci mu 403."
        )