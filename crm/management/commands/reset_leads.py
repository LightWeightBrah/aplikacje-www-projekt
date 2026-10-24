"""Czyści wszystkie leady i zakłada świeże dane demo (tylko do testów na komputerze deweloperskim)."""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crm.models import Activity, Company, Lead, LeadAssignment, LeadBatch


class Command(BaseCommand):
    help = (
        "Usuwa WSZYSTKIE leady, historię rozmów, przypisania, bazy i firmy, a potem wgrywa dane demo "
        "(seed_demo). Konta użytkowników, produkty i ustawienia zespołu zostają. "
        "Działa tylko przy DEBUG=True i wymaga wpisania RESET."
    )

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="bez pytania o potwierdzenie (nadal tylko DEBUG=True)")
        parser.add_argument("--empty", action="store_true", help="tylko wyczyść, bez wgrywania danych demo")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Odmowa: ta komenda kasuje wszystkie leady i działa tylko przy DEBUG=True "
                "(środowisko testowe). Na serwerze produkcyjnym jest zablokowana."
            )
        counts = (
            ("leady", Lead.objects.count()),
            ("rozmowy (historia)", Activity.objects.count()),
            ("przypisania", LeadAssignment.objects.count()),
            ("bazy", LeadBatch.objects.count()),
            ("firmy", Company.objects.count()),
        )
        self.stdout.write(self.style.WARNING("Zostanie usunięte: " + ", ".join(f"{n}: {c}" for n, c in counts)))
        if not options["yes"]:
            answer = input("Wpisz RESET i Enter, aby potwierdzić (cokolwiek innego anuluje): ")
            if answer.strip() != "RESET":
                self.stdout.write("Anulowano, nic nie zmieniono.")
                return
        with transaction.atomic():
            Activity.objects.all().delete()
            LeadAssignment.objects.all().delete()
            Lead.objects.all().delete()
            LeadBatch.objects.all().delete()
            Company.objects.all().delete()
        self.stdout.write(self.style.SUCCESS("Baza leadów wyczyszczona."))
        if not options["empty"]:
            call_command("seed_demo", stdout=self.stdout)
