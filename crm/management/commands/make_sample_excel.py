from pathlib import Path

from django.core.management.base import BaseCommand

from crm.sample_data import build_ceidg_sample_workbook, build_sample_workbook


class Command(BaseCommand):
    help = (
        "Tworzy przykładowe pliki Excela (fikcyjne dane) do wypróbowania importu: "
        "zwykły z nagłówkami i drugi w układzie bazy z CEIDG."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            default="docs/przykladowa_baza.xlsx",
            help="gdzie zapisać plik z nagłówkami (domyślnie docs/przykladowa_baza.xlsx)",
        )
        parser.add_argument(
            "--ceidg",
            default="docs/przykladowa_baza_ceidg.xlsx",
            help="gdzie zapisać plik w układzie CEIDG (domyślnie docs/przykladowa_baza_ceidg.xlsx)",
        )

    def handle(self, *args, **options):
        for path, build in ((options["path"], build_sample_workbook), (options["ceidg"], build_ceidg_sample_workbook)):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            build().save(path)
            self.stdout.write(self.style.SUCCESS(f"Zapisano przykładowy plik: {path}"))
