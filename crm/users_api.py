"""Zarządzanie kontami (tylko manager): lista, dodanie agenta, edycja nazwy,
reset hasła oraz dezaktywacja i ponowna aktywacja konta.

Konta nigdy nie są usuwane: historia rozmów i przypisań odwołuje się do użytkownika.
Dezaktywowany użytkownik nie zaloguje się, a jego tokeny przestają działać.
"""

import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .avatar import validate_avatar
from .models import Activity, AgentProfile, Lead, LeadAssignment
from .me_view import AvatarSerializer
from .permissions import IsManager
from .roles import AGENT, MANAGER, is_manager
from .serializers import assignable_users
from .services import ACTIVE_STATUSES, reassign_agent_leads, release_agent_leads

User = get_user_model()

# Bez znaków łatwych do pomylenia (0/O, 1/l/I), żeby hasło dało się przeczytać przez telefon
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password(length=12):
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def role_of(user):
    return "manager" if is_manager(user) else "agent"


def delete_blocker(user):
    """Powód, dla którego konta nie wolno usunąć, albo None.

    Usunięcie zostawiłoby leady bez opiekuna, a rozmowy i przypisania bez autora,
    więc dozwolone jest tylko dla kont bez żadnej historii (np. testowych).
    """
    if Lead.objects.filter(owner=user).exists():
        return "Ma przypisane leady. Przekaż je albo zdezaktywuj konto."
    if Activity.objects.filter(agent=user).exists():
        return "Ma historię rozmów. Zostaw konto zdezaktywowane, żeby historia była kompletna."
    if LeadAssignment.objects.filter(Q(from_agent=user) | Q(to_agent=user)).exists():
        return "Ma historię przypisań leadów. Zostaw konto zdezaktywowane."
    return None


def managed_users():
    """Konta z rolą Agent lub Manager (także zdezaktywowane) oraz superużytkownicy."""
    return (
        User.objects.filter(
            Q(groups__name__in=[AGENT, MANAGER]) | Q(is_superuser=True)
        )
        .distinct()
        .annotate(
            active_leads=Count(
                "leads", filter=Q(leads__status__in=ACTIVE_STATUSES), distinct=True
            )
        )
        .order_by("-is_active", "username")
    )


# ---------- serializery ----------


class UserRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    full_name = serializers.CharField()
    role = serializers.CharField(help_text="agent albo manager")
    is_active = serializers.BooleanField()
    is_me = serializers.BooleanField()
    is_superuser = serializers.BooleanField()
    active_leads = serializers.IntegerField()
    last_login = serializers.DateTimeField(allow_null=True)
    avatar = serializers.CharField(help_text="Zdjęcie jako data URL albo pusty tekst.")
    avatar = serializers.CharField(help_text="Zdjęcie profilowe jako data URL albo pusty tekst.")
    can_delete = serializers.BooleanField(help_text="Czy konto da się usunąć (brak historii).")


def user_row(user, requester, active_leads=None, avatar=None):
    if active_leads is None:
        active_leads = Lead.objects.filter(owner=user, status__in=ACTIVE_STATUSES).count()
    if avatar is None:
        profile = AgentProfile.objects.filter(user=user).only("avatar").first()
        avatar = profile.avatar if profile else ""
    return {
        "id": user.pk,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.get_full_name() or user.username,
        "role": role_of(user),
        "is_active": user.is_active,
        "is_me": user.pk == requester.pk,
        "is_superuser": user.is_superuser,
        "active_leads": active_leads,
        "last_login": user.last_login,
        "avatar": avatar,
        "can_delete": delete_blocker(user) is None and not user.is_superuser and user.pk != requester.pk,
    }


class UserCreateSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=["agent", "manager"], default="agent")
    password = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        trim_whitespace=False,
        help_text="Puste = system wygeneruje hasło i pokaże je raz w odpowiedzi.",
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "role", "password"]
        extra_kwargs = {
            "first_name": {"required": True, "allow_blank": False},
            "last_name": {"required": True, "allow_blank": False},
        }

    def validate_username(self, value):
        value = value.strip()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ten login jest już zajęty.")
        return value


class UserUpdateSerializer(serializers.ModelSerializer):
    role = serializers.CharField(required=False, help_text="agent albo manager")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "role"]
        extra_kwargs = {
            # starsze konta (np. założone w adminie) mogą nie mieć imienia i nazwiska
            "first_name": {"allow_blank": True},
            "last_name": {"allow_blank": True},
            # unikalność sprawdzamy niżej bez względu na wielkość liter
            "username": {"validators": [User._meta.get_field("username").validators[0]]},
        }

    def validate_role(self, value):
        if value not in ("agent", "manager"):
            raise serializers.ValidationError("Dozwolone role: agent, manager.")
        return value

    def validate_username(self, value):
        value = value.strip()
        if User.objects.filter(username__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("Ten login jest już zajęty.")
        return value


class ResetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        help_text="Puste = system wygeneruje nowe hasło.",
    )


class PasswordResultSerializer(serializers.Serializer):
    password = serializers.CharField()
    generated = serializers.BooleanField()


class DeactivateSerializer(serializers.Serializer):
    handover = serializers.ChoiceField(
        choices=["pool", "agent"],
        required=False,
        help_text="Co zrobić z aktywnymi leadami: `pool` = zwrot do puli, `agent` = przekazanie.",
    )
    to_agent = serializers.PrimaryKeyRelatedField(
        queryset=assignable_users(), required=False
    )

    def validate(self, attrs):
        if attrs.get("handover") == "agent" and "to_agent" not in attrs:
            raise serializers.ValidationError(
                {"to_agent": "Wskaż, komu przekazać leady."}
            )
        return attrs


class DeactivateResultSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()
    leads_moved = serializers.IntegerField()


# ---------- widok ----------


class UserViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Manager zarządza kontami: `/api/users/`."""

    permission_classes = [IsManager]
    pagination_class = None
    filter_backends = []
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return managed_users()

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action == "partial_update":
            return UserUpdateSerializer
        return UserRowSerializer

    # -- pomocnicze --

    def _guard_superuser(self, target):
        """Zwykły manager nie rusza kont administratorów systemu."""
        if target.is_superuser and not self.request.user.is_superuser:
            return Response(
                {"detail": "Konta administratora systemu nie można zmieniać z panelu managera."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    # -- operacje --

    @extend_schema(responses=UserRowSerializer(many=True))
    def list(self, request, *args, **kwargs):
        users = list(self.get_queryset())
        avatars = dict(AgentProfile.objects.filter(user__in=users).values_list("user_id", "avatar"))
        rows = [
            user_row(u, request.user, u.active_leads, avatars.get(u.pk, ""))
            for u in users
        ]
        return Response(UserRowSerializer(rows, many=True).data)

    @extend_schema(request=UserCreateSerializer, responses=UserRowSerializer)
    def create(self, request, *args, **kwargs):
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = data["role"]
        password = data.get("password") or ""
        generated = not password
        if generated:
            password = generate_password()
        with transaction.atomic():
            user = User.objects.create_user(
                username=data["username"],
                password=password,
                first_name=data["first_name"],
                last_name=data["last_name"],
            )
            group, _ = Group.objects.get_or_create(name=MANAGER if role == "manager" else AGENT)
            user.groups.add(group)
            AgentProfile.objects.update_or_create(user=user, defaults={"must_change_password": True})
        body = user_row(user, request.user, 0)
        body["password"] = password if generated else None
        return Response(body, status=status.HTTP_201_CREATED)

    @extend_schema(request=UserUpdateSerializer, responses=UserRowSerializer)
    def partial_update(self, request, *args, **kwargs):
        target = self.get_object()
        blocked = self._guard_superuser(target)
        if blocked:
            return blocked
        serializer = UserUpdateSerializer(target, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        role = data.pop("role", None)
        if role is not None and role != role_of(target):
            if target.pk == request.user.pk:
                return Response(
                    {"role": ["Nie możesz zmienić własnej roli."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if target.is_superuser:
                return Response(
                    {"role": ["Rola administratora systemu wynika z jego uprawnień."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if role == "agent" and target.is_active and not any(
                is_manager(u) for u in User.objects.filter(is_active=True).exclude(pk=target.pk)
            ):
                return Response(
                    {"role": ["To ostatni aktywny manager, nie można go zmienić na agenta."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        with transaction.atomic():
            for field, value in data.items():
                setattr(target, field, value)
            if data:
                target.save(update_fields=list(data))
            if role is not None and role != role_of(target):
                new_group, _ = Group.objects.get_or_create(name=MANAGER if role == "manager" else AGENT)
                target.groups.remove(*Group.objects.filter(name__in=[AGENT, MANAGER]))
                target.groups.add(new_group)
        target = User.objects.get(pk=target.pk)  # świeże grupy
        return Response(user_row(target, request.user))

    @extend_schema(request=ResetPasswordSerializer, responses=PasswordResultSerializer)
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        """Ustawia nowe hasło (podane albo wygenerowane). Stare logowania przestają działać."""
        target = self.get_object()
        blocked = self._guard_superuser(target)
        if blocked:
            return blocked
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        password = serializer.validated_data.get("password") or ""
        generated = not password
        if generated:
            password = generate_password()
        target.set_password(password)
        target.save(update_fields=["password"])
        if target.pk != request.user.pk:  # własne hasło manager i tak ustawia sam
            AgentProfile.objects.update_or_create(user=target, defaults={"must_change_password": True})
        return Response({"password": password, "generated": generated})

    @extend_schema(request=DeactivateSerializer, responses=DeactivateResultSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """Blokuje konto. Gdy użytkownik ma aktywne leady, trzeba wskazać, co z nimi zrobić."""
        target = self.get_object()
        blocked = self._guard_superuser(target)
        if blocked:
            return blocked
        if target.pk == request.user.pk:
            return Response(
                {"detail": "Nie możesz zdezaktywować własnego konta."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not target.is_active:
            return Response(
                {"detail": "To konto jest już nieaktywne."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if is_manager(target):
            others = [
                u for u in User.objects.filter(is_active=True).exclude(pk=target.pk)
                if is_manager(u)
            ]
            if not others:
                return Response(
                    {"detail": "To ostatni aktywny manager, nie można go zdezaktywować."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = DeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        handover = serializer.validated_data.get("handover")
        to_agent = serializer.validated_data.get("to_agent")

        with transaction.atomic():
            # Blokada wiersza: nikt nie zmieni opiekuna leadów w trakcie przekazywania
            locked = list(Lead.objects.select_for_update().filter(
                owner=target, status__in=ACTIVE_STATUSES
            ).values_list("pk", flat=True))
            if locked and handover is None:
                return Response(
                    {
                        "detail": f"Ten użytkownik ma aktywne leady ({len(locked)}). "
                        "Wskaż, komu je przekazać albo zwróć je do puli.",
                        "active_leads": len(locked),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            moved = 0
            if locked and handover == "agent":
                if to_agent.pk == target.pk:
                    return Response(
                        {"detail": "Leadów nie można przekazać temu samemu użytkownikowi."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                moved = reassign_agent_leads(target, to_agent)
            elif locked:
                moved = release_agent_leads(target)
            target.is_active = False
            target.save(update_fields=["is_active"])
        return Response({"is_active": False, "leads_moved": moved})

    @extend_schema(
        methods=["POST"], request=AvatarSerializer, responses=AvatarSerializer
    )
    @extend_schema(methods=["DELETE"], request=None, responses={204: None})
    @action(detail=True, methods=["post", "delete"])
    def avatar(self, request, pk=None):
        """Manager ustawia (POST {"image": data URL}) albo usuwa (DELETE) zdjęcie profilowe użytkownika."""
        target = self.get_object()
        blocked = self._guard_superuser(target)
        if blocked:
            return blocked
        if request.method == "DELETE":
            AgentProfile.objects.filter(user=target).update(avatar="")
            return Response(status=status.HTTP_204_NO_CONTENT)
        image = request.data.get("image")
        error = validate_avatar(image)
        if error:
            return Response({"image": [error]}, status=status.HTTP_400_BAD_REQUEST)
        AgentProfile.objects.update_or_create(user=target, defaults={"avatar": image})
        return Response({"image": image})

    @extend_schema(request=None, responses={204: None})
    def destroy(self, request, *args, **kwargs):
        """Trwale usuwa konto bez historii (np. testowe). Konta z historią tylko się dezaktywuje."""
        target = self.get_object()
        blocked = self._guard_superuser(target)
        if blocked:
            return blocked
        if target.pk == request.user.pk:
            return Response(
                {"detail": "Nie możesz usunąć własnego konta."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target.is_superuser:
            return Response(
                {"detail": "Konta administratora systemu nie można usunąć z panelu."},
                status=status.HTTP_403_FORBIDDEN,
            )
        reason = delete_blocker(target)
        if reason:
            return Response({"detail": reason}, status=status.HTTP_409_CONFLICT)
        if is_manager(target) and target.is_active and not any(
            is_manager(u) for u in User.objects.filter(is_active=True).exclude(pk=target.pk)
        ):
            return Response(
                {"detail": "To ostatni aktywny manager, nie można go usunąć."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        target.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses=DeactivateResultSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Przywraca dostęp zdezaktywowanemu kontu."""
        target = self.get_object()
        blocked = self._guard_superuser(target)
        if blocked:
            return blocked
        target.is_active = True
        target.save(update_fields=["is_active"])
        return Response({"is_active": True, "leads_moved": 0})
