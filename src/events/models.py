from datetime import UTC, datetime

from django.db import models
from django.db.models import (
    Case,
    IntegerField,
    Min,
    OuterRef,
    Q,
    Subquery,
    UniqueConstraint,
    Value,
    When,
)
from django.utils import timezone

from app import media_type_config
from app.models import TV, Item, MediaTypes, Season, Status

# Statuses that represent inactive tracking
# will be ignored when creating events
INACTIVE_TRACKING_STATUSES = [
    Status.PAUSED.value,
    Status.DROPPED.value,
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
        """Get all upcoming media events of the specified user."""
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

        tv_query = self._build_tv_query(user, enabled_types)
        combined_query = (user_query & active_status_query) | tv_query

        queryset = self.filter(
            combined_query,
            datetime__gte=start_datetime,
            datetime__lte=end_datetime,
        ).select_related("item")

        return self.sort_with_sentinel_last(queryset)

    def _build_tv_query(self, user, enabled_types):
        """Build query for TV shows based on TV status and season statuses."""
        if not (
            MediaTypes.TV.value in enabled_types
            or MediaTypes.SEASON.value in enabled_types
        ):
            return Q()

        # Get active TV shows
        active_tv_shows = (
            TV.objects.filter(
                user=user,
                item__media_type=MediaTypes.TV.value,
            )
            .exclude(
                status__in=INACTIVE_TRACKING_STATUSES,
            )
            .values_list("item__media_id", flat=True)
        )

        if not active_tv_shows:
            return Q()

        # Subquery to find the first season with inactive status for each TV show
        first_dropped_seasons = (
            Season.objects.filter(
                user=user,
                item__media_id=OuterRef("media_id"),
                status__in=INACTIVE_TRACKING_STATUSES,
            )
            .values("item__media_id")
            .annotate(min_season=Min("item__season_number"))
            .values("min_season")
        )

        # Get all media_ids and their first dropped season numbers
        dropped_seasons = (
            Item.objects.filter(
                media_id__in=active_tv_shows,
                media_type=MediaTypes.SEASON.value,
            )
            .annotate(
                first_dropped_season=Subquery(first_dropped_seasons),
            )
            .filter(first_dropped_season__isnull=False)
            .values_list("media_id", "first_dropped_season")
        )

        # Build exclusion query
        exclude_query = Q()
        for media_id, first_dropped_season in dropped_seasons:
            exclude_query |= Q(
                item__media_type=MediaTypes.SEASON.value,
                item__media_id=media_id,
                item__season_number__gte=first_dropped_season,
            )

        return (
            Q(
                item__media_type=MediaTypes.SEASON.value,
                item__media_id__in=active_tv_shows,
            )
            & ~exclude_query
        )

    def sort_with_sentinel_last(self, queryset):
        """Sort events with sentinel time last."""
        today = timezone.now().date()
        sentinel_dt = timezone.localtime(
            datetime(
                today.year,
                today.month,
                today.day,
                SentinelDatetime.HOUR,
                SentinelDatetime.MINUTE,
                SentinelDatetime.SECOND,
                SentinelDatetime.MICROSECOND,
                tzinfo=UTC,
            ),
        )

        return queryset.annotate(
            is_sentinel=Case(
                When(
                    datetime__hour=sentinel_dt.hour,
                    datetime__minute=sentinel_dt.minute,
                    datetime__second=sentinel_dt.second,
                    then=Value(1),
                ),
                default=Value(0),
                output_field=IntegerField(),
            ),
        ).order_by("datetime__date", "is_sentinel", "datetime")


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
