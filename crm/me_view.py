from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .roles import can_view_team_stats, is_agent, is_manager


class MeView(APIView):
    """Dane zalogowanego użytkownika i jego rola (używa tego frontend po zalogowaniu)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if is_manager(user):
            role = "manager"
        elif is_agent(user):
            role = "agent"
        else:
            role = "none"
        return Response(
            {
                "id": user.pk,
                "username": user.username,
                "full_name": user.get_full_name() or user.username,
                "role": role,
                "can_view_team": can_view_team_stats(user),
            }
        )
