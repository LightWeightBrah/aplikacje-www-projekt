from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

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
    must_change_password = serializers.BooleanField(
        help_text="Tak, gdy użytkownik loguje się hasłem tymczasowym od managera."
    )


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
                "must_change_password": AgentProfile.objects.filter(
                    user=user, must_change_password=True
                ).exists(),
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


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        trim_whitespace=False,
        required=False,
        allow_blank=True,
        help_text="Wymagane, chyba że konto ma hasło tymczasowe od managera (wtedy pomijane).",
    )
    new_password = serializers.CharField(trim_whitespace=False)


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class MePasswordView(APIView):
    """Użytkownik sam zmienia hasło. Stare logowania wygasają, więc odpowiedź zawiera nowe tokeny."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=ChangePasswordSerializer, responses=TokenPairSerializer)
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        forced = AgentProfile.objects.filter(user=user, must_change_password=True).exists()
        current = serializer.validated_data.get("current_password", "")
        new = serializer.validated_data["new_password"]
        # Po haśle tymczasowym użytkownik już się nim zalogował, więc nie pytamy o nie drugi raz
        if not forced and not user.check_password(current):
            return Response(
                {"current_password": ["Obecne hasło jest nieprawidłowe."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.check_password(new):
            return Response(
                {"new_password": ["Wybierz inne hasło niż obecne."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(new)
        user.save(update_fields=["password"])
        AgentProfile.objects.filter(user=user).update(must_change_password=False)
        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})
