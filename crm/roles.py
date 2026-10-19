AGENT = "Agent"
MANAGER = "Manager"


def is_manager(user):
    """Manager to członek grupy Manager albo superużytkownik."""
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name=MANAGER).exists()
    )


def is_agent(user):
    """Agent to członek grupy Agent."""
    return user.is_authenticated and user.groups.filter(name=AGENT).exists()


def visible_to(user, queryset, owner_lookup="owner"):
    """Manager widzi wszystko, agent tylko rekordy, których jest opiekunem."""
    if is_manager(user):
        return queryset
    if not user.is_authenticated:
        return queryset.none()
    return queryset.filter(**{owner_lookup: user})