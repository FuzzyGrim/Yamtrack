from datetime import datetime

from django.db import models
from django.db.models import Q, UniqueConstraint
from django.utils import timezone

from app import media_type_config
from app.models import Item, Media, MediaTypes, Season

# Statuses that represent inactive tracking
# will be ignored when creating events
INACTIVE_TRACKING_STATUSES = [
    Media.Status.PAUSED.value,
    Media.Status.DROPPED.value,
]


class SentinelDatetime:
    """Sentinel time for event without a specific time."""

    YEAR = 9999
    MONTH = 12
    DAY = 31
    HOUR = 11
    MINUTE = 59
    SECOND = 59
    MICROSECOND = 999999


class EventManager(models.Manager):
    """Custom manager for the Event model."""

    def get_user_events(self, user, first_day, last_day):
        """Get all upcoming media events of the specified user.

        For TV shows:
        - If the latest season the user is tracking has an active status,
          show events for all seasons of that TV show
        - If the latest season has an inactive status, don't show TV show events

        For other media types:
        - Only show events for media the user is actively tracking
        """
        # Convert date objects to datetime objects with timezone awareness
        start_datetime = timezone.make_aware(
            datetime.combine(first_day, datetime.min.time()),
        )
        end_datetime = timezone.make_aware(
            datetime.combine(last_day, datetime.max.time()),
        )

        enabled_types = user.get_enabled_media_types()
        non_tv_types = [
            media_type
            for media_type in enabled_types
            if media_type not in [MediaTypes.TV.value, MediaTypes.SEASON.value]
        ]

        # Build base query for non-TV media types
        user_query = Q()
        active_status_query = Q()

        for media_type in non_tv_types:
            user_query |= Q(**{f"item__{media_type}__user": user})
            active_status_query &= ~Q(
                **{f"item__{media_type}__status__in": INACTIVE_TRACKING_STATUSES},
            )

        tv_query = Q()

        if (
            MediaTypes.TV.value in enabled_types
            or MediaTypes.SEASON.value in enabled_types
        ):
            user_seasons = Season.objects.filter(
                user=user,
            ).select_related("item")

            # Track the latest season for each TV show
            latest_seasons = {}

            # Find the latest season for each TV show in a single pass
            for season in user_seasons:
                tv_id = season.item.media_id
                season_number = season.item.season_number

                if (
                    tv_id not in latest_seasons
                    or season_number > latest_seasons[tv_id].item.season_number
                ):
                    latest_seasons[tv_id] = season

            # Identify TV shows where the latest season has active status
            tv_shows_with_active_latest_season = {
                tv_id
                for tv_id, season in latest_seasons.items()
                if season.status not in INACTIVE_TRACKING_STATUSES
            }

            if tv_shows_with_active_latest_season:
                # Include all seasons from TV shows where the latest season is active
                tv_query = Q(
                    item__media_type=MediaTypes.SEASON.value,
                    item__media_id__in=tv_shows_with_active_latest_season,
                )

        combined_query = (user_query & active_status_query) | tv_query

        # Return events matching our criteria
        return self.filter(
            combined_query,
            datetime__gte=start_datetime,
            datetime__lte=end_datetime,
        ).order_by("datetime").select_related("item")


class Event(models.Model):
    """Calendar event model."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    content_number = models.IntegerField(null=True)
    datetime = models.DateTimeField()
    notification_sent = models.BooleanField(default=False)
    objects = EventManager()

    class Meta:
        """Meta class for Event model."""

        ordering = ["-datetime"]
        constraints = [
            UniqueConstraint(
                fields=["item", "content_number"],
                name="unique_item_content_number",
            ),
            UniqueConstraint(
                fields=["item"],
                condition=Q(content_number__isnull=True),
                name="unique_item_null_content_number",
            ),
        ]

    def __str__(self):
        """Return event title."""
        if self.content_number:
            return (
                f"{self.item.__str__()} "
                f"{media_type_config.get_unit(self.item.media_type, short=True)}"
                f"{self.content_number}"
            )

        return self.item.__str__()

    @property
    def readable_content_number(self):
        """Return the episode number in a readable format."""
        if self.content_number is None:
            return ""

        return (
            f"{media_type_config.get_unit(self.item.media_type, short=True)}"
            f"{self.content_number}"
        )

    @property
    def is_sentinel_time(self):
        """Check if the event time is sentinel time."""
        return (
            self.datetime.hour == SentinelDatetime.HOUR
            and self.datetime.minute == SentinelDatetime.MINUTE
            and self.datetime.second == SentinelDatetime.SECOND
            and self.datetime.microsecond == SentinelDatetime.MICROSECOND
        )

    @property
    def is_max_datetime(self):
        """Check if the event datetime is sentinel datetime."""
        max_hour = 23
        return (
            self.datetime.year == SentinelDatetime.YEAR
            and self.datetime.month == SentinelDatetime.MONTH
            and self.datetime.day == SentinelDatetime.DAY
            and self.datetime.hour == max_hour
            and self.datetime.minute == SentinelDatetime.MINUTE
            and self.datetime.second == SentinelDatetime.SECOND
        )

    @property
    def display_time(self):
        """Return the display time of the event."""
        if self.is_sentinel_time:
            return ""

        localized_value = timezone.localtime(self.datetime)
        return f"at {localized_value.strftime('%H:%M')}"
