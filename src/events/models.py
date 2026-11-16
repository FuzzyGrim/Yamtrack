import re
from datetime import UTC, datetime

from django.conf import settings
from django.db import models
from django.db.models import (
    Case,
    IntegerField,
    Min,
    Q,
    UniqueConstraint,
    Value,
    When,
)
from django.utils import timezone

from app import config
from app.models import TV, Anime, Item, MediaTypes, Season, Sources, Status
from app.services.item_merge import dedupe_cross_provider_items
from integrations.anime_mapping import resolve_provider_series_id

# Statuses that represent inactive tracking
# will be ignored when creating events
INACTIVE_TRACKING_STATUSES = [
    Status.PAUSED.value,
    Status.DROPPED.value,
]


_SEASON_PART_SUFFIX_RE = re.compile(r"(?i)\b(season|part|cour)\b.*$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_anime_title(title: str) -> str:
    """Strip season/part suffixes and punctuation for an exact-match fallback."""
    stripped = _SEASON_PART_SUFFIX_RE.sub("", title)
    return _NON_ALNUM_RE.sub("", stripped.lower())


class ReleaseTypes(models.TextChoices):
    """Extra release dates a movie can have next to its main (theatrical) date.

    The main release is stored with a blank release type.
    """

    DIGITAL = "digital", "Digital"
    PHYSICAL = "physical", "Physical"


# Any release date before this year is a placeholder, not a real release.
MIN_VALID_RELEASE_DATETIME = datetime(1900, 1, 1, tzinfo=UTC)


class SentinelDatetime:
    """Sentinel time for event without a specific time."""

    YEAR = 9999
    MONTH = 12
    DAY = 31
    HOUR = 11
    MINUTE = 59
    SECOND = 59
    MICROSECOND = 999999


def _unknown_time_q(field_name):
    """Match a datetime carrying the date-only sentinel time.

    DateTimeField extracts its parts in the active timezone, so the sentinel's
    11:59:59 UTC hour is not stable: it becomes 12:59 or 13:59 in Europe.
    Minute and second survive a timezone shift, and provider air times sit on
    minute boundaries, so matching :59:59 identifies the sentinel portably.
    """
    return Q(
        **{
            f"{field_name}__minute": SentinelDatetime.MINUTE,
            f"{field_name}__second": SentinelDatetime.SECOND,
        },
    )


def released_q(field_name, now=None):
    """Return a Q matching datetimes whose release moment has passed.

    A provider that knows only the release date stores the sentinel time. That
    value is an all-day date, not an instant: it counts as released once the
    date has ended in the active timezone. Comparing it directly with ``now``
    made an episode released at the sentinel's midday time on its listed date,
    which is the day before it airs for users east of UTC (#1588).

    The lower bound keeps year-1 unknown-date sentinels out of the query before
    its timezone-aware lookups run. SQLite's datetime extract/cast functions
    underflow datetime.MINYEAR when converting year 1 into a zone west of UTC,
    which surfaced as ``DataError: string or blob too big`` (#1717).
    """
    now = now or timezone.now()
    return (
        Q(**{f"{field_name}__gte": MIN_VALID_RELEASE_DATETIME})
        & Q(**{f"{field_name}__lte": now})
        & ~(
            _unknown_time_q(field_name)
            & Q(**{f"{field_name}__date__gte": timezone.localdate(now)})
        )
    )


def is_released_datetime(value, now=None):
    """Return whether a stored release datetime has passed."""
    if value is None:
        return False
    now = now or timezone.now()
    if (
        value.minute == SentinelDatetime.MINUTE
        and value.second == SentinelDatetime.SECOND
    ):
        return timezone.localtime(value).date() < timezone.localdate(now)
    return value <= now


# Columns dedupe_cross_provider_items reads; every other Item column is skipped.
_DEDUPE_ITEM_FIELDS = (
    "id",
    "media_id",
    "media_type",
    "season_number",
    "source",
    "provider_external_ids",
)


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

        tv_enabled = (
            MediaTypes.TV.value in enabled_types
            or MediaTypes.SEASON.value in enabled_types
        )
        active_tv_items = self._active_tv_items(user) if tv_enabled else []
        tv_query = self._build_tv_query(user, enabled_types, active_tv_items)
        combined_query = (user_query & active_status_query) | tv_query

        # Digital and physical dates are per country: only the user's own count.
        queryset = self.filter(
            Q(release_type="") | Q(region=user.watch_provider_region),
            combined_query,
            datetime__gte=start_datetime,
            datetime__lte=end_datetime,
        ).select_related("item")

        hidden_item_ids = self.hidden_duplicate_item_ids(
            user,
            enabled_types,
            active_tv_items,
        )
        if hidden_item_ids:
            queryset = queryset.exclude(item_id__in=hidden_item_ids)

        return self.sort_with_sentinel_last(queryset)

    def hidden_duplicate_item_ids(self, user, enabled_types, active_tv_items=None):
        """Return `Item` ids to exclude because a preferred duplicate exists.

        Combines the TMDB/TVDB cross-provider dedup (#639) with the
        Anime/TV cross-bucket dedup (#968), so both the calendar and
        release notifications hide the same duplicates.
        """
        return self._cross_provider_hidden_season_item_ids(
            user,
            enabled_types,
            active_tv_items,
        ) | self._cross_bucket_hidden_anime_item_ids(
            user,
            enabled_types,
        )

    def _active_tv_items(self, user):
        """Return the user's actively-tracked TV `Item`s with only the dedupe fields.

        The calendar needs this list twice (which shows to include, which
        duplicates to hide). Loading every column, including the large JSON
        ones, for thousands of shows was most of the page's time.
        """
        return list(
            Item.objects.filter(
                # Select from the user's own TV rows: excluding through the
                # reverse relation would also match other accounts' trackers.
                id__in=TV.objects.filter(user=user)
                .exclude(status__in=INACTIVE_TRACKING_STATUSES)
                .values("item_id"),
                media_type=MediaTypes.TV.value,
            )
            .only(*_DEDUPE_ITEM_FIELDS),
        )

    def _active_tv_show_media_ids(self, user, active_tv_items=None):
        """Return media_ids of the user's actively-tracked TV shows, deduped across providers."""
        items = (
            self._active_tv_items(user) if active_tv_items is None else active_tv_items
        )
        if not items:
            return []

        preferred_source = getattr(
            user,
            "tv_metadata_source_default",
            Sources.TMDB.value,
        )
        deduped = dedupe_cross_provider_items(items, preferred_source)
        return [item.media_id for item in deduped]

    def _cross_provider_hidden_season_item_ids(
        self,
        user,
        enabled_types,
        active_tv_items=None,
    ):
        """Return Season `Item` ids to hide because a preferred counterpart exists.

        A user can legitimately track the same show under both a TMDB and a
        TVDB identity (the #620 dual-identity model); each identity gets its
        own independent Season `Item`s and `Event`s. Without this, the
        calendar shows the same real episode twice - once per identity
        (#639). Uses the same verified cached cross-provider mapping already
        used for Home rows; Season rows fall back to their matching TMDB TV
        item's mapping when the child row has no `tvdb_id` of its own.
        """
        if not (
            MediaTypes.TV.value in enabled_types
            or MediaTypes.SEASON.value in enabled_types
        ):
            return set()

        all_active_tv_items = (
            self._active_tv_items(user) if active_tv_items is None else active_tv_items
        )
        if not all_active_tv_items:
            return set()

        preferred_source = getattr(
            user,
            "tv_metadata_source_default",
            Sources.TMDB.value,
        )
        kept_tv_media_ids = {
            item.media_id
            for item in dedupe_cross_provider_items(
                all_active_tv_items,
                preferred_source,
            )
        }

        # Seasons of TV shows discarded by the parent-level dedup above are
        # duplicates too, even when their own season numbers don't line up
        # with the kept show's (e.g. TMDB absolute S1 vs TVDB split S4) and
        # so wouldn't be caught by the per-show dedupe below (#1202).
        discarded_tv_media_ids = {
            item.media_id
            for item in all_active_tv_items
            if item.media_id not in kept_tv_media_ids
        }
        hidden_ids = set(
            Item.objects.filter(
                media_type=MediaTypes.SEASON.value,
                media_id__in=discarded_tv_media_ids,
            ).values_list("id", flat=True),
        )

        season_items = list(
            Item.objects.filter(
                media_type=MediaTypes.SEASON.value,
                media_id__in=kept_tv_media_ids,
            ).only(*_DEDUPE_ITEM_FIELDS),
        )
        if season_items:
            deduped = dedupe_cross_provider_items(season_items, preferred_source)
            if len(deduped) < len(season_items):
                kept_ids = {item.id for item in deduped}
                hidden_ids.update(
                    item.id for item in season_items if item.id not in kept_ids
                )

        return hidden_ids

    def _cross_bucket_hidden_anime_item_ids(self, user, enabled_types):
        """Return Anime `Item` ids to hide because a tracked TV counterpart exists.

        A user can track the same real show both in the Anime bucket (MAL)
        and the TV bucket (TMDB/TVDB) - each gets its own independent `Item`
        and `Event`s, so without this the calendar and release notifications
        fire twice for the same episode (#968). Uses the pinned Kometa
        Anime-IDs mapping (`integrations.anime_mapping`) to resolve a MAL id
        to its verified TMDB/TVDB series id. Newly announced anime aren't in
        that static snapshot yet (#1000), so as a guarded fallback - only
        when the verified lookup misses, and only against the user's own
        actively-tracked shows, never the global DB - an anime item whose
        normalized title exactly matches a tracked TV show's is also hidden.
        The TV bucket is always kept since it carries season/episode
        structure; the Anime bucket duplicate is hidden.
        """
        if MediaTypes.ANIME.value not in enabled_types:
            return set()
        if not (
            MediaTypes.TV.value in enabled_types
            or MediaTypes.SEASON.value in enabled_types
        ):
            return set()

        active_anime_items = list(
            Item.objects.filter(
                media_type=MediaTypes.ANIME.value,
                source=Sources.MAL.value,
                # Select from the user's own rows; excluding through the reverse
                # relation would also match other accounts' paused trackers.
                id__in=Anime.objects.filter(user=user)
                .exclude(status__in=INACTIVE_TRACKING_STATUSES)
                .values("item_id"),
            )
            .only(*_DEDUPE_ITEM_FIELDS, "title"),
        )
        if not active_anime_items:
            return set()

        active_tv_rows = list(
            TV.objects.filter(
                user=user,
                item__media_type=MediaTypes.TV.value,
            )
            .exclude(status__in=INACTIVE_TRACKING_STATUSES)
            .values_list("item__source", "item__media_id", "item__title"),
        )
        if not active_tv_rows:
            return set()

        active_tv_shows = {(source, media_id) for source, media_id, _ in active_tv_rows}
        active_tv_title_slugs = {
            slug
            for _, _, title in active_tv_rows
            if (slug := _normalize_anime_title(title))
        }

        hidden_ids = set()
        for anime_item in active_anime_items:
            tmdb_id = resolve_provider_series_id(anime_item.media_id, "tmdb")
            tvdb_id = resolve_provider_series_id(anime_item.media_id, "tvdb")
            has_verified_match = (
                tmdb_id and (Sources.TMDB.value, tmdb_id) in active_tv_shows
            ) or (tvdb_id and (Sources.TVDB.value, tvdb_id) in active_tv_shows)
            title_slug = _normalize_anime_title(anime_item.title)
            has_title_fallback_match = title_slug and title_slug in active_tv_title_slugs
            if has_verified_match or has_title_fallback_match:
                hidden_ids.add(anime_item.id)

        return hidden_ids

    def _build_tv_query(self, user, enabled_types, active_tv_items=None):
        """Build query for TV shows based on TV status and season statuses."""
        if not (
            MediaTypes.TV.value in enabled_types
            or MediaTypes.SEASON.value in enabled_types
        ):
            return Q()

        # Get active TV shows
        active_tv_shows = self._active_tv_show_media_ids(user, active_tv_items)

        if not active_tv_shows:
            return Q()

        season_query = Q(
            item__media_type=MediaTypes.SEASON.value,
            item__media_id__in=active_tv_shows,
        )
        hidden_season_ids = self._dropped_season_item_ids(user, active_tv_shows)
        if hidden_season_ids:
            season_query &= ~Q(item_id__in=hidden_season_ids)
        return season_query

    def _dropped_season_item_ids(self, user, show_media_ids):
        """Return Season `Item` ids hidden by a dropped or paused season.

        A dropped/paused season hides itself and every later season of the
        same show. This used to be a correlated EXISTS in the calendar query,
        which SQLite ran once per event in the month, each time walking every
        season the user tracks: at 10k items that was 2 of the page's 3.4
        seconds (SURFACE-16). Two small queries up front give the same rows.
        """
        first_hidden_number = dict(
            Season.objects.filter(
                user=user,
                status__in=INACTIVE_TRACKING_STATUSES,
                item__media_id__in=show_media_ids,
                item__season_number__isnull=False,
            )
            .values("item__media_id")
            .annotate(first_number=Min("item__season_number"))
            .values_list("item__media_id", "first_number"),
        )
        if not first_hidden_number:
            return []
        return [
            item_id
            for item_id, media_id, season_number in Item.objects.filter(
                media_type=MediaTypes.SEASON.value,
                media_id__in=first_hidden_number,
                season_number__isnull=False,
            ).values_list("id", "media_id", "season_number")
            if season_number >= first_hidden_number[media_id]
        ]

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
    # Blank for the item's main release date. Digital and physical dates differ
    # per country, so those events also carry the region they apply to and only
    # show for users whose watch provider region matches.
    release_type = models.CharField(
        max_length=10,
        choices=ReleaseTypes,
        blank=True,
        default="",
    )
    region = models.CharField(max_length=5, blank=True, default="")
    notification_sent = models.BooleanField(default=False)
    # notification_sent is global, so it cannot say who was actually reached.
    # Users whose real-time alert failed are recorded here so the daily digest
    # can still list the event for them.
    alert_failed_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="+",
    )
    objects = EventManager()

    class Meta:
        """Meta class for Event model."""

        ordering = ["-datetime"]
        # The calendar filters one month out of every event ever stored.
        # Without this index SQLite scans the whole table (SCAN events_event).
        indexes = [
            models.Index(fields=["datetime"], name="events_event_datetime_idx"),
        ]
        constraints = [
            UniqueConstraint(
                fields=["item", "content_number"],
                name="unique_item_content_number",
            ),
            UniqueConstraint(
                fields=["item", "release_type", "region"],
                condition=Q(content_number__isnull=True),
                name="unique_item_release_null_content_number",
            ),
        ]

    def __str__(self):
        """Return event title."""
        if self.release_type:
            return f"{self.item} ({self.get_release_type_display()} release)"

        if self.content_number is not None and self.item.media_type == MediaTypes.SEASON.value and self.item.season_number is not None:
            return f"{self.item.title} {self.season_episode_label}"

        if self.content_number:
            return (
                f"{self.item.__str__()} "
                f"{config.get_unit(self.item.media_type, short=True)}"
                f"{self.content_number}"
            )

        return self.item.__str__()

    @property
    def readable_content_number(self):
        """Return the episode number in a readable format."""
        if self.content_number is None:
            return ""

        return (
            f"{config.get_unit(self.item.media_type, short=True)}{self.content_number}"
        )

    @property
    def season_episode_label(self):
        """Return "S02E05" for a season's episode, else the plain episode number."""
        item = self.item
        if (
            item.media_type == "season"
            and item.season_number is not None
            and self.content_number is not None
        ):
            return f"S{item.season_number:02d}E{self.content_number:02d}"
        return self.readable_content_number

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
