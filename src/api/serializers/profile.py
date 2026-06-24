from django.contrib.auth import get_user_model
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import Q
from rest_framework import serializers

from api.serializers.common import image_url, media_summary_from_item
from app.models import BasicMedia, DiaryEntry, MediaTypes, Status, Tag
from social.models import Follow, FollowStatus
from users.forms import PasswordChangeForm
from users.models import (
    DateFormatChoices,
    QuickWatchDateChoices,
    TimeFormatChoices,
    WeekStartDayChoices,
)

PROFILE_PRIMARY_MEDIA_TYPES = [
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
]


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
            "library_items": _media_count(user, exclude_status=Status.PLANNING.value),
            "reviews": DiaryEntry.objects.filter(user=user).filter(_review_q()).count(),
            "planned_items": _media_count(user, status=Status.PLANNING.value),
            "liked_items": DiaryEntry.objects.filter(user=user, liked=True).values("item_id").distinct().count(),
            "tags": Tag.objects.filter(diary_entries__user=user).distinct().count(),
        },
        "hof": hof_payload(user, request=request),
        "preferences": preferences_payload(user),
    }


def _review_q():
    return Q(review__gt="") | Q(review_title__gt="")


def _media_count(user, *, status=None, exclude_status=None):
    total = 0
    for media_type in PROFILE_PRIMARY_MEDIA_TYPES:
        queryset = BasicMedia.objects.get_media_list(
            user,
            media_type,
            status or "All",
            "title",
        )
        if exclude_status:
            queryset = queryset.exclude(status=exclude_status)
        total += len(queryset)
    return total


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

    def validate_username(self, value):
        user = self.context["request"].user
        if user.is_demo and value != user.username:
            raise serializers.ValidationError("Changing the username is not allowed for the demo account.")
        try:
            UnicodeUsernameValidator()(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        User = get_user_model()
        if User.objects.exclude(pk=user.pk).filter(username=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def save(self, **kwargs):
        user = kwargs["user"]
        update_fields = []
        for field, value in self.validated_data.items():
            if getattr(user, field) != value:
                setattr(user, field, value)
                update_fields.append(field)
        if update_fields:
            try:
                user.save(update_fields=update_fields)
            except IntegrityError as exc:
                raise serializers.ValidationError({"username": ["A user with that username already exists."]}) from exc
        return user


class PreferencesSerializer(serializers.Serializer):
    """Mobile-supported user preferences."""

    enabled_media_types = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    date_format = serializers.ChoiceField(choices=DateFormatChoices.choices, required=False)
    time_format = serializers.ChoiceField(choices=TimeFormatChoices.choices, required=False)
    week_start_day = serializers.ChoiceField(choices=WeekStartDayChoices.choices, required=False)
    quick_watch_date = serializers.ChoiceField(choices=QuickWatchDateChoices.choices, required=False)
    release_notifications_enabled = serializers.BooleanField(required=False)
    daily_digest_enabled = serializers.BooleanField(required=False)

    def validate_enabled_media_types(self, value):
        allowed = set(MediaTypes.values) - {MediaTypes.EPISODE.value}
        invalid = sorted(set(value) - allowed)
        if invalid:
            message = f"Unsupported media type(s): {', '.join(invalid)}."
            raise serializers.ValidationError(message)
        if not value:
            raise serializers.ValidationError("At least one media type must be enabled.")
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        if user.is_demo and attrs:
            raise serializers.ValidationError("This section is view-only for demo accounts.")
        enabled = attrs.get("enabled_media_types", user.get_enabled_media_types())
        if not enabled:
            raise serializers.ValidationError({"enabled_media_types": ["At least one media type must be enabled."]})
        return attrs

    def save(self, **kwargs):
        user = kwargs["user"]
        data = dict(self.validated_data)
        update_fields = []
        enabled = data.pop("enabled_media_types", None)
        if enabled is not None:
            enabled = set(enabled)
            for media_type in MediaTypes.values:
                field = f"{media_type}_enabled"
                if media_type != MediaTypes.EPISODE.value and hasattr(user, field):
                    value = media_type in enabled
                    if getattr(user, field) != value:
                        setattr(user, field, value)
                        update_fields.append(field)
        for field, value in data.items():
            if getattr(user, field) != value:
                setattr(user, field, value)
                update_fields.append(field)
        if update_fields:
            user.save(update_fields=update_fields)
        return user


class PasswordChangeSerializer(serializers.Serializer):
    """Validate current-user password changes."""

    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        form = PasswordChangeForm(
            user=self.context["request"].user,
            data={
                "old_password": attrs["old_password"],
                "new_password1": attrs["new_password"],
                "new_password2": attrs["new_password_confirm"],
            },
        )
        if not form.is_valid():
            field_map = {
                "new_password1": "new_password",
                "new_password2": "new_password_confirm",
            }
            errors = {
                field_map.get(field, field): list(messages)
                for field, messages in form.errors.items()
            }
            raise serializers.ValidationError(errors)
        attrs["form"] = form
        return attrs

    def save(self, **kwargs):
        return self.validated_data["form"].save()
