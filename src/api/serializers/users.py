from django.contrib.auth import get_user_model
from rest_framework import serializers

from users.models import WATCH_PROVIDER_REGION_UNSET

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

PREFERENCE_FIELDS = (
    "home_sort",
    "home_hide_unreleased",
    *(
        f"{media_type}_{suffix}"
        for media_type in MEDIA_TYPE_PREFERENCES
        for suffix in ("enabled", "layout", "sort", "status")
    ),
    "clickable_media_cards",
    "obfuscate_unseen_episodes",
    "quick_watch_date",
    "date_format",
    "time_format",
    "week_start_day",
    "progress_bar",
    "hide_completed_recommendations",
    "hide_zero_rating",
    "watch_provider_region",
    "calendar_layout",
    "lists_sort",
    "list_detail_sort",
    "list_detail_status",
)


class PreferencesSerializer(serializers.ModelSerializer):
    """Serializer for the user preferences."""

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
        fields = PREFERENCE_FIELDS

    def validate_watch_provider_region(self, value):
        """Check the region is one of the TMDB watch provider regions."""
        if value == WATCH_PROVIDER_REGION_UNSET:
            return value

        regions = self.context.get("watch_provider_regions") or []
        if value not in {region[0] for region in regions}:
            msg = "Invalid watch provider region."
            raise serializers.ValidationError(msg)
        return value


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
