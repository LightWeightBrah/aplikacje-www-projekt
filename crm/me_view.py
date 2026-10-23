from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .avatar import validate_avatar
from .models import AgentProfile
from .roles import can_view_team_stats, is_agent, is_manager


def _avatar_of(user):
    profile = AgentProfile.objects.filter(user=user).only("avatar").first()
    return profile.avatar if profile else ""


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    full_name = serializers.CharField()
    role = serializers.ChoiceField(choices=["manager", "agent", "none"])
    can_view_team = serializers.BooleanField()
    avatar = serializers.CharField(help_text="Zdjęcie jako data URL albo pusty tekst.")


class AvatarSerializer(serializers.Serializer):
    image = serializers.CharField(help_text="Zdjęcie jako data URL (PNG, JPEG lub WebP, do 150 KB).")


class MeView(APIView):
    """Dane zalogowanego użytkownika i jego rola (używa tego frontend po zalogowaniu)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeSerializer)
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
                "avatar": _avatar_of(user),
            }
        )


class MeAvatarView(APIView):
    """Zdjęcie profilowe zalogowanego użytkownika: POST {"image": data URL} ustawia, DELETE usuwa."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=AvatarSerializer, responses=AvatarSerializer)
    def post(self, request):
        image = request.data.get("image")
        error = validate_avatar(image)
        if error:
            return Response({"image": [error]}, status=status.HTTP_400_BAD_REQUEST)
        AgentProfile.objects.update_or_create(user=request.user, defaults={"avatar": image})
        return Response({"avatar": image})

    @extend_schema(request=None, responses={204: None})
    def delete(self, request):
        AgentProfile.objects.filter(user=request.user).update(avatar="")
        return Response(status=status.HTTP_204_NO_CONTENT)
