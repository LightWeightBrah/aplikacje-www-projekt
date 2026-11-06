"""Kod aktywacji urządzenia z konsoli serwera – na pierwsze urządzenie (potem manager robi to w CRM).

    python manage.py device_code
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.device_lock import new_code


class Command(BaseCommand):
    help = "Wypisuje jednorazowy kod aktywacji urządzenia (ważny 24 h)."

    def handle(self, *args, **options):
        code, expires = new_code()
        self.stdout.write(self.style.SUCCESS(f"Kod aktywacji: {code}"))
        self.stdout.write(f"Działa raz, do {timezone.localtime(expires):%d.%m.%Y %H:%M}. "
                          "Otwórz adres CRM na urządzeniu i wpisz ten kod.")
