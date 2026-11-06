from django.core.management.base import BaseCommand

from crm.tariffs import STANDARD_TARIFFS, load_standard_tariffs


class Command(BaseCommand):
    help = (
        "Wgrywa taryfy T-Mobile z wzornika ofert (S, M100, M, L, VIP) do „Taryfy i produkty”. "
        "Istniejące taryfy o tej samej nazwie zostają bez zmian (chyba że podasz --overwrite). "
        "Działa także na serwerze – to nie są dane klientów."
    )

    def add_arguments(self, parser):
        parser.add_argument("--overwrite", action="store_true",
                            help="przywróć ceny z wzornika także w taryfach, które już są")

    def handle(self, *args, **options):
        created, updated = load_standard_tariffs(overwrite=options["overwrite"])
        skipped = len(STANDARD_TARIFFS) - len(created) - len(updated)
        self.stdout.write(self.style.SUCCESS(
            f"Dodane: {', '.join(created) or 'brak'} · zaktualizowane: {', '.join(updated) or 'brak'}"
            + (f" · bez zmian: {skipped}" if skipped else "")
        ))
