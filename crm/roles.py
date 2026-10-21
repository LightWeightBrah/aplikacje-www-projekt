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


def can_view_team_stats(user):
    """Czy użytkownik może oglądać ranking i wyniki innych agentów.

    Manager zawsze może. Agent może, dopóki manager mu tego nie wyłączy;
    brak profilu oznacza zgodę (domyślnie wszyscy widzą ranking).
    """
    if not user.is_authenticated:
        return False
    if is_manager(user):
        return True
    from .models import AgentProfile

    profile = AgentProfile.objects.filter(user=user).first()
    return profile is None or profile.can_view_team_stats
