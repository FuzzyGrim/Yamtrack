import logging

from django.contrib.auth import get_user_model, update_session_auth_hash
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.permissions import can_view_user_profile
from api.serializers.common import (
    MediaRefSerializer,
    find_item,
    get_or_create_item_from_metadata,
    image_url,
    media_summary_from_item,
)
from api.serializers.profile import (
    PasswordChangeSerializer,
    PreferencesSerializer,
    ProfileUpdateSerializer,
    hof_payload,
    preferences_payload,
    profile_payload,
)
from app.models import MediaLike, MediaTypes
from app.providers import services as provider_services
from app.services import set_media_like
from social.models import SocialAuditLog

logger = logging.getLogger(__name__)

MAX_AVATAR_SIZE = 5 * 1024 * 1024
ALLOWED_AVATAR_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

HOF_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
}


class HOFItemWriteSerializer(serializers.Serializer):
    """Validate Hall of Fame item writes."""

    ref = MediaRefSerializer()


class MeView(APIView):
    """Current user profile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(profile_payload(request.user, request=request, viewer=request.user))

    def patch(self, request):
        old_private = request.user.profile_private
        serializer = ProfileUpdateSerializer(data=request.data, partial=True, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(user=request.user)
        if old_private != request.user.profile_private:
            SocialAuditLog.objects.create(
                actor=request.user,
                action="profile_visibility_update",
                target_user=request.user,
                metadata={"from": old_private, "to": request.user.profile_private},
            )
        return Response(profile_payload(request.user, request=request, viewer=request.user))


class LikedMediaView(APIView):
    """Current user's canonical liked media."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        likes = MediaLike.objects.filter(user=request.user).select_related("item").order_by("-created_at", "-id")
        media_type = request.query_params.get("media_type")
        if media_type:
            likes = likes.filter(item__media_type=media_type)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(likes, request, view=self)
        return paginator.get_paginated_response(
            [media_summary_from_item(like.item, request=request, user=request.user) for like in page],
        )

    def post(self, request):
        serializer = HOFItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = self._item_for_ref(serializer.validated_data["ref"], create=True)
        set_media_like(request.user, item, liked=True)
        return Response({"liked": True, "media": media_summary_from_item(item, request=request, user=request.user)})

    def delete(self, request):
        serializer = HOFItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = self._item_for_ref(serializer.validated_data["ref"], create=False)
        if item is not None:
            set_media_like(request.user, item, liked=False)
        return Response({"liked": False})

    def _item_for_ref(self, ref, *, create):
        item = find_item(ref)
        if item is not None or not create:
            return item
        metadata = provider_services.get_media_metadata(
            ref["media_type"],
            ref["media_id"],
            ref["source"],
            [ref.get("season_number")] if ref.get("season_number") is not None else None,
            ref.get("episode_number"),
        )
        return get_or_create_item_from_metadata(ref, metadata)


class AvatarView(APIView):
    """Upload current user's avatar."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        avatar = request.FILES.get("avatar")
        if avatar is None:
            return Response({"avatar": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if avatar.content_type not in ALLOWED_AVATAR_CONTENT_TYPES:
            return Response({"avatar": ["Upload a JPEG, PNG, or WebP image."]}, status=status.HTTP_400_BAD_REQUEST)
        if avatar.size > MAX_AVATAR_SIZE:
            return Response({"avatar": ["Avatar must be 5 MB or smaller."]}, status=status.HTTP_400_BAD_REQUEST)
        old_avatar = request.user.profile_picture
        request.user.profile_picture = avatar
        try:
            request.user.save(update_fields=["profile_picture"])
        except OSError:
            logger.exception("Failed to save avatar for user: %s", request.user.username)
            return Response(
                {"avatar": ["Could not save your avatar. Please try again later."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if old_avatar and old_avatar.name != request.user.profile_picture.name:
            try:
                old_avatar.delete(save=False)
            except OSError:
                logger.warning("Failed to delete replaced avatar for user: %s", request.user.username)
        return Response({"avatar_url": image_url(request, request.user.profile_picture)})

    def delete(self, request):
        old_avatar = request.user.profile_picture
        request.user.profile_picture = None
        request.user.save(update_fields=["profile_picture"])
        if old_avatar:
            try:
                old_avatar.delete(save=False)
            except OSError:
                logger.warning("Failed to delete avatar for user: %s", request.user.username)
        return Response({"avatar_url": None})


class PreferencesView(APIView):
    """Mobile preference subset."""

    permission_classes = [IsAuthenticated]

    def patch(self, request):
        serializer = PreferencesSerializer(data=request.data, partial=True, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(user=request.user)
        return Response(preferences_payload(request.user))


class PasswordChangeView(APIView):
    """Change the current user's password."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        update_session_auth_hash(request, user)
        return Response({"detail": "Password updated."})


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
        if media_type not in HOF_MEDIA_TYPES:
            return Response({"media_type": ["Unsupported Hall of Fame media type."]}, status=status.HTTP_400_BAD_REQUEST)
        serializer = HOFItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ref = serializer.validated_data["ref"]
        if ref["media_type"] != media_type:
            return Response({"ref": ["media_type must match URL media_type."]}, status=status.HTTP_400_BAD_REQUEST)
        item = find_item(ref)
        if item is None:
            metadata = provider_services.get_media_metadata(
                ref["media_type"],
                ref["media_id"],
                ref["source"],
                [ref.get("season_number")] if ref.get("season_number") is not None else None,
                ref.get("episode_number"),
            )
            item = get_or_create_item_from_metadata(ref, metadata)
        request.user.set_hall_of_fame_item(media_type, item)
        request.user.save(update_fields=[f"hof_{media_type}"])
        return Response({"items": hof_payload(request.user, request=request)})

    def delete(self, request, media_type):
        if media_type not in HOF_MEDIA_TYPES:
            return Response({"media_type": ["Unsupported Hall of Fame media type."]}, status=status.HTTP_400_BAD_REQUEST)
        request.user.clear_hall_of_fame_item(media_type)
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
