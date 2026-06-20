from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import can_view_user_profile
from api.serializers.common import find_item, image_url
from api.serializers.profile import (
    PreferencesSerializer,
    ProfileUpdateSerializer,
    hof_payload,
    preferences_payload,
    profile_payload,
)
from app.models import MediaTypes


class MeView(APIView):
    """Current user profile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(profile_payload(request.user, request=request, viewer=request.user))

    def patch(self, request):
        serializer = ProfileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(request.user, field, value)
        request.user.save()
        return Response(profile_payload(request.user, request=request, viewer=request.user))


class AvatarView(APIView):
    """Upload current user's avatar."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        avatar = request.FILES.get("avatar")
        if avatar is None:
            return Response({"avatar": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        request.user.profile_picture = avatar
        request.user.save(update_fields=["profile_picture"])
        return Response({"avatar_url": image_url(request, request.user.profile_picture)})


class PreferencesView(APIView):
    """Mobile preference subset."""

    permission_classes = [IsAuthenticated]

    def patch(self, request):
        serializer = PreferencesSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        enabled = data.pop("enabled_media_types", None)
        if enabled is not None:
            for media_type in MediaTypes.values:
                if media_type != MediaTypes.EPISODE.value and hasattr(request.user, f"{media_type}_enabled"):
                    setattr(request.user, f"{media_type}_enabled", media_type in enabled)
        for field, value in data.items():
            setattr(request.user, field, value)
        request.user.save()
        return Response(preferences_payload(request.user))


class PublicProfileView(APIView):
    """Public profile by username."""

    permission_classes = [AllowAny]

    def get(self, request, username):
        User = get_user_model()
        user = get_object_or_404(User, username=username)
        if not can_view_user_profile(request.user, user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(profile_payload(user, request=request, viewer=request.user))


class UserSearchView(APIView):
    """Search users by username/display name."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        User = get_user_model()
        query = request.query_params.get("q", "").strip()
        users = User.objects.all()
        if query:
            users = users.filter(username__icontains=query)
        users = [user for user in users[:25] if can_view_user_profile(request.user, user)]
        return Response(
            {
                "results": [
                    {
                        "id": user.id,
                        "username": user.username,
                        "display_name": user.display_name or user.username,
                        "avatar_url": image_url(request, user.profile_picture) if user.profile_picture else None,
                    }
                    for user in users
                ],
            },
        )


class MeHOFView(APIView):
    """Current user's Hall of Fame map."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"items": hof_payload(request.user, request=request)})


class HOFItemView(APIView):
    """Set or clear a Hall of Fame item."""

    permission_classes = [IsAuthenticated]

    def put(self, request, media_type):
        item = find_item(request.data["ref"])
        if item is None:
            return Response({"ref": ["Item must exist before it can be added to Hall of Fame."]}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.set_hall_of_fame_item(media_type, item):
            return Response(status=status.HTTP_404_NOT_FOUND)
        request.user.save(update_fields=[f"hof_{media_type}"])
        return Response({"items": hof_payload(request.user, request=request)})

    def delete(self, request, media_type):
        if not request.user.clear_hall_of_fame_item(media_type):
            return Response(status=status.HTTP_404_NOT_FOUND)
        request.user.save(update_fields=[f"hof_{media_type}"])
        return Response({"items": hof_payload(request.user, request=request)})


class UserHOFView(APIView):
    """Public user's Hall of Fame map."""

    permission_classes = [AllowAny]

    def get(self, request, username):
        User = get_user_model()
        user = get_object_or_404(User, username=username)
        if not can_view_user_profile(request.user, user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response({"items": hof_payload(user, request=request)})
