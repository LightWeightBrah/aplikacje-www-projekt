"""Start CRM na serwerze firmy: grupy Agent / Manager i pierwsze konto managera.

Bez kont demo i bez fikcyjnych klientów. Hasło wpisuje się w konsoli (nie widać go
i nigdzie nie jest zapisane czystym tekstem). Agentów dodaje potem manager w CRM:
Zarządzanie → Użytkownicy.

    python manage.py start_firm
"""

import getpass

from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from crm.roles import AGENT, MANAGER


class Command(BaseCommand):
    help = "Tworzy grupy Agent i Manager oraz pierwsze konto managera (bez danych demo)."

    def add_arguments(self, parser):
        parser.add_argument("--username", help="login managera (bez tego komenda zapyta)")
        parser.add_argument("--name", default="", help="imię i nazwisko, np. \"Jan Kowalski\"")

    def handle(self, *args, **options):
        groups = {name: Group.objects.get_or_create(name=name)[0] for name in (AGENT, MANAGER)}
        self.stdout.write("Grupy Agent i Manager są gotowe.")

        User = get_user_model()
        username = (options["username"] or input("Login managera: ")).strip()
        if not username:
            raise CommandError("Podaj login.")
        if User.objects.filter(username=username).exists():
            raise CommandError(f"Konto „{username}” już istnieje. Hasło zmienisz w CRM albo: manage.py changepassword {username}")
        first, _, last = (options["name"] or input("Imię i nazwisko (Enter = pomiń): ")).strip().partition(" ")

        user = User(username=username, first_name=first, last_name=last.strip())
        for attempt in range(3):
            password = getpass.getpass("Hasło (nie widać go przy pisaniu): ")
            if password != getpass.getpass("Powtórz hasło: "):
                self.stderr.write("Hasła się różnią – spróbuj jeszcze raz.")
                continue
            try:
                password_validation.validate_password(password, user)
            except ValidationError as error:
                self.stderr.write("Za słabe hasło: " + " ".join(error.messages))
                continue
            break
        else:
            raise CommandError("Nie ustawiono hasła – uruchom komendę jeszcze raz.")

        user.set_password(password)
        user.save()
        user.groups.add(groups[MANAGER])
        self.stdout.write(self.style.SUCCESS(
            f"Gotowe: manager „{username}”. Zaloguj się w CRM, a agentów dodaj w Zarządzanie → Użytkownicy."
        ))
