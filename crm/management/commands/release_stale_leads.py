from django.conf import settings
from django.core.management.base import BaseCommand

from crm.services import release_stale_leads


class Command(BaseCommand):
    help = (
        "Zwraca do puli leady przypisane agentom, których agent nie ruszył "
        "od LEAD_CLAIM_TIMEOUT_HOURS godzin."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="tylko policz leady, nic nie zmieniaj",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        count = release_stale_leads(dry_run=dry_run)
        hours = settings.LEAD_CLAIM_TIMEOUT_HOURS
        if dry_run:
            self.stdout.write(f"Do zwrotu do puli (brak aktywności > {hours} h): {count}")
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Zwrócono do puli {count} leadów (brak aktywności > {hours} h)."
            ))