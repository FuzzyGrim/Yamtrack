from rest_framework import serializers

from api.serializers.common import image_url, media_summary_from_item
from social.models import Follow, FollowStatus


def profile_payload(user, request=None, viewer=None):
    """Serialize a profile with mobile-relevant preferences."""
    viewer = viewer or getattr(request, "user", None)
    following = requested = followed_by = blocked = False
    if viewer and viewer.is_authenticated and viewer != user:
        following = Follow.objects.filter(
            from_user=viewer,
            to_user=user,
            status=FollowStatus.ACCEPTED,
        ).exists()
        requested = Follow.objects.filter(
            from_user=viewer,
            to_user=user,
            status=FollowStatus.PENDING,
        ).exists()
        followed_by = Follow.objects.filter(
            from_user=user,
            to_user=viewer,
            status=FollowStatus.ACCEPTED,
        ).exists()
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "email": user.email if viewer == user else None,
        "bio": user.bio,
        "pronouns": user.pronouns,
        "location": user.location,
        "avatar_url": image_url(request, user.profile_picture) if user.profile_picture else None,
        "is_private": user.profile_private,
        "viewer_relationship": {
            "following": following,
            "followed_by": followed_by,
            "requested": requested,
            "blocked": blocked,
        },
        "counts": {
            "followers": user.follower_edges.filter(status=FollowStatus.ACCEPTED).count(),
            "following": user.following_edges.filter(status=FollowStatus.ACCEPTED).count(),
            "diary_entries": user.diaryentry_set.count(),
            "lists": user.customlist_set.count(),
        },
        "hof": hof_payload(user, request=request),
        "preferences": preferences_payload(user),
    }


def preferences_payload(user):
    """Serialize mobile-relevant preferences."""
    return {
        "enabled_media_types": user.get_enabled_media_types(),
        "date_format": user.date_format,
        "time_format": user.time_format,
        "week_start_day": user.week_start_day,
        "quick_watch_date": user.quick_watch_date,
        "release_notifications_enabled": user.release_notifications_enabled,
        "daily_digest_enabled": user.daily_digest_enabled,
    }


def hof_payload(user, request=None):
    """Serialize Hall of Fame item map."""
    return {
        media_type: media_summary_from_item(item, request=request, user=user) if item else None
        for media_type, item in user.get_hall_of_fame_items().items()
    }


class ProfileUpdateSerializer(serializers.Serializer):
    """Fields iOS can update on the current user."""

    username = serializers.CharField(max_length=150, required=False)
    display_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    bio = serializers.CharField(max_length=500, required=False, allow_blank=True)
    pronouns = serializers.CharField(max_length=50, required=False, allow_blank=True)
    location = serializers.CharField(max_length=100, required=False, allow_blank=True)
    is_private = serializers.BooleanField(source="profile_private", required=False)


class PreferencesSerializer(serializers.Serializer):
    """Mobile-supported user preferences."""

    enabled_media_types = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    date_format = serializers.CharField(required=False)
    time_format = serializers.CharField(required=False)
    week_start_day = serializers.CharField(required=False)
    quick_watch_date = serializers.CharField(required=False)
    release_notifications_enabled = serializers.BooleanField(required=False)
    daily_digest_enabled = serializers.BooleanField(required=False)
