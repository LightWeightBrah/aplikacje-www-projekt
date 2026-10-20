from pathlib import Path

from django.core.management.base import BaseCommand

from crm.sample_data import build_sample_workbook


class Command(BaseCommand):
    help = "Tworzy przykładowy plik Excela (fikcyjne dane) do wypróbowania importu."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            default="docs/przykladowa_baza.xlsx",
            help="gdzie zapisać plik (domyślnie docs/przykladowa_baza.xlsx)",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        build_sample_workbook().save(path)
        self.stdout.write(self.style.SUCCESS(f"Zapisano przykładowy plik: {path}"))