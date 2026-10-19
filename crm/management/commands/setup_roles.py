from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from crm.roles import AGENT, MANAGER

DEMO_PASSWORD = "demo12345"  # tylko dane demonstracyjne na lokalną bazę
DEMO_USERS = (
    ("agent1", AGENT),
    ("agent2", AGENT),
    ("manager1", MANAGER),
)


class Command(BaseCommand):
    help = "Tworzy grupy Agent i Manager. Z opcją --demo także konta demo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--demo",
            action="store_true",
            help="utwórz też konta demo: agent1, agent2, manager1",
        )

    def handle(self, *args, **options):
        groups = {}
        for name in (AGENT, MANAGER):
            group, created = Group.objects.get_or_create(name=name)
            groups[name] = group
            self.stdout.write(f"Grupa {name}: {'utworzona' if created else 'już istnieje'}")

        if options["demo"]:
            User = get_user_model()
            for username, role in DEMO_USERS:
                user, created = User.objects.get_or_create(username=username)
                if created:
                    user.set_password(DEMO_PASSWORD)
                    user.save()
                user.groups.add(groups[role])
                self.stdout.write(f"Konto {username} ({role}): {'utworzone' if created else 'już istnieje'}")
            self.stdout.write(f"Hasło kont demo: {DEMO_PASSWORD}")