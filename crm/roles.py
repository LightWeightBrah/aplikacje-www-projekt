AGENT = "Agent"
MANAGER = "Manager"


def is_manager(user):
    """Manager to członek grupy Manager albo superużytkownik."""
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name=MANAGER).exists()
    )