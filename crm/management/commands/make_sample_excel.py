from pathlib import Path

from django.core.management.base import BaseCommand

from crm.sample_data import DEMO_PARTS, build_ceidg_sample_workbook, build_demo_ceidg_workbook, build_sample_workbook


class Command(BaseCommand):
    help = (
        "Tworzy przykładowe pliki Excela (fikcyjne dane) do wypróbowania importu: "
        "zwykły z nagłówkami, mały w układzie bazy z CEIDG i większe bazy do pokazu "
        "(docs/przykladowa_baza_pokaz_1..3.xlsx, po kilkadziesiąt firm)."
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
        parser.add_argument(
            "--demo-dir",
            default="docs",
            help="folder na większe bazy do pokazu (domyślnie docs)",
        )

    def handle(self, *args, **options):
        files = [(options["path"], build_sample_workbook), (options["ceidg"], build_ceidg_sample_workbook)]
        for part in range(1, DEMO_PARTS + 1):
            files.append((Path(options["demo_dir"]) / f"przykladowa_baza_pokaz_{part}.xlsx",
                          lambda part=part: build_demo_ceidg_workbook(part)))
        for path, build in files:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            build().save(path)
            self.stdout.write(self.style.SUCCESS(f"Zapisano przykładowy plik: {path}"))
