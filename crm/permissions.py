from rest_framework.permissions import SAFE_METHODS, BasePermission

from .roles import is_agent, is_manager


class IsAgentOrManager(BasePermission):
    """Dostęp do API mają tylko użytkownicy z rolą Agent lub Manager."""

    message = "Brak roli Agent lub Manager."

    def has_permission(self, request, view):
        return is_agent(request.user) or is_manager(request.user)


class IsManager(BasePermission):
    message = "Ta operacja jest dostępna tylko dla managera."

    def has_permission(self, request, view):
        return is_manager(request.user)


class IsManagerOrReadOnly(BasePermission):
    """Odczyt dla agentów i managerów, zapis tylko dla managera."""

    message = "Zmiany w tych danych może wprowadzać tylko manager."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return is_agent(request.user) or is_manager(request.user)
        return is_manager(request.user)


class IsLeadOwnerOrManager(BasePermission):
    """Do leada (i jego historii) ma dostęp jego opiekun albo manager."""

    message = "To nie jest Twój lead."

    def has_object_permission(self, request, view, obj):
        lead = getattr(obj, "lead", obj)  # Activity i LeadAssignment mają pole lead
        return is_manager(request.user) or lead.owner_id == request.user.id