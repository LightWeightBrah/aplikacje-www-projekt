from datetime import timedelta

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
from crm.roles import AGENT
from crm.services import register_activity

BATCH_NAME = "Demo - baza startowa"

PRODUCTS = ("Internet światłowodowy", "Abonament komórkowy", "Leasing samochodowy")

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

        products = [Product.objects.get_or_create(name=name)[0] for name in PRODUCTS]
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
        register_activity(
            lead,
            agent1,
            Activity.Outcome.INTERESTED,
            "Zainteresowana abonamentem",
            next_contact_at=now + timedelta(days=1),
            next_contact_note="Wysłać ofertę abonamentu i dopytać o decyzję",
        )

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
        for owner, names in (
            (agent1, ("Julia Adamska", "Kamil Borkowski")),
            (agent2, ("Lena Czerwińska",)),
        ):
            for contact_name in names:
                lead = new_lead(contact_name, owner=owner)
                register_activity(lead, owner, Activity.Outcome.SOLD, "Podpisana umowa")

        # Nowy agent z wyłączonym podglądem rankingu (pokaz blokady 403).
        agent3, created = User.objects.get_or_create(username="agent3")
        if created:
            agent3.set_password("demo12345")
            agent3.save()
        agent3.groups.add(Group.objects.get(name=AGENT))
        AgentProfile.objects.update_or_create(
            user=agent3, defaults={"can_view_team_stats": False}
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Gotowe: {batch.leads.count()} leadów w bazie '{BATCH_NAME}'."
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