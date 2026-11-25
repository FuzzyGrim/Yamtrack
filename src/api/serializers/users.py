from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from users.models import WATCH_PROVIDER_REGION_UNSET

from .fields import MediaTypeCompleteChoiceField

User = get_user_model()

MEDIA_TYPE_PREFERENCES = (
    "tv",
    "season",
    "movie",
    "anime",
    "manga",
    "game",
    "book",
    "comic",
    "boardgame",
)

# Preferences that apply to the whole application.
GENERAL_PREFERENCE_FIELDS = (
    *(f"{media_type}_enabled" for media_type in MEDIA_TYPE_PREFERENCES),
    "week_start_day",
    "watch_provider_region",
)

# Preferences that only change how the web UI looks or behaves.
WEBUI_PREFERENCE_FIELDS = (
    "home_sort",
    "home_hide_unreleased",
    *(
        f"{media_type}_{suffix}"
        for media_type in MEDIA_TYPE_PREFERENCES
        for suffix in ("layout", "sort", "status")
    ),
    "clickable_media_cards",
    "obfuscate_unseen_episodes",
    "quick_watch_date",
    "date_format",
    "time_format",
    "progress_bar",
    "hide_completed_recommendations",
    "hide_zero_rating",
    "calendar_layout",
    "lists_sort",
    "list_detail_sort",
    "list_detail_status",
)


class PreferencesSerializer(serializers.ModelSerializer):
    """Serializer for the preferences that apply to the whole application."""

    watch_provider_region = serializers.CharField(
        max_length=5,
        allow_blank=True,
        help_text=(
            "TMDB watch provider region code, an empty string to disable them or "
            f"`{WATCH_PROVIDER_REGION_UNSET}` if it was never set."
        ),
    )

    class Meta:  # noqa: D106
        model = User
        fields = GENERAL_PREFERENCE_FIELDS

    def validate_watch_provider_region(self, value):
        """Check the region is one of the TMDB watch provider regions."""
        if value == WATCH_PROVIDER_REGION_UNSET:
            return value

        regions = self.context.get("watch_provider_regions") or []
        if value not in {region[0] for region in regions}:
            msg = "Invalid watch provider region."
            raise serializers.ValidationError(msg)
        return value


class WebUIPreferencesSerializer(serializers.ModelSerializer):
    """Serializer for the preferences that only affect the web UI."""

    class Meta:  # noqa: D106
        model = User
        fields = WEBUI_PREFERENCE_FIELDS


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for the authenticated user."""

    enabled_media_types = serializers.SerializerMethodField()
    preferences = PreferencesSerializer(source="*", read_only=True)

    class Meta:  # noqa: D106
        model = User
        fields = (
            "id",
            "username",
            "is_demo",
            "profile_private",
            "enabled_media_types",
            "preferences",
        )
        read_only_fields = fields

    def get_enabled_media_types(self, obj) -> list[str]:
        """Return media types the user has enabled."""
        return obj.get_enabled_media_types()


class UserUpdateSerializer(serializers.ModelSerializer):
    """Serializer to update username and profile visibility."""

    class Meta:  # noqa: D106
        model = User
        fields = ("username", "profile_private")


class PasswordChangeSerializer(serializers.Serializer):
    """Serializer to change the password of the authenticated user."""

    old_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_old_password(self, value):
        """Check the current password."""
        if not self.context["request"].user.check_password(value):
            msg = "The old password is incorrect."
            raise serializers.ValidationError(msg)
        return value

    def validate_new_password(self, value):
        """Run the configured password validators."""
        try:
            validate_password(value, self.context["request"].user)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages)) from e
        return value


class TokenResponseSerializer(serializers.Serializer):
    """Serializer for the regenerated API token."""

    token = serializers.CharField()


class SearchCacheResponseSerializer(serializers.Serializer):
    """Serializer for the result of clearing the search cache."""

    deleted = serializers.IntegerField(help_text="Number of cache entries deleted.")


class PublicProfileSerializer(serializers.Serializer):
    """Serializer for the public information of a user."""

    username = serializers.CharField()
    enabled_media_types = serializers.ListField(child=MediaTypeCompleteChoiceField())
