import logging
import uuid
from types import SimpleNamespace

from django.apps import apps
from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import (
    CheckConstraint,
    Count,
    F,
    Max,
    Prefetch,
    Q,
    UniqueConstraint,
    Window,
)
from django.db.models.functions import RowNumber
from django.utils import timezone
from model_utils import FieldTracker
from model_utils.fields import MonitorField
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import app
import events
import users
from app import providers
from app.mixins import CalendarTriggerMixin
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class Sources(models.TextChoices):
    """Choices for the source of the item."""

    TMDB = "tmdb", "The Movie Database"
    MAL = "mal", "MyAnimeList"
    MANGAUPDATES = "mangaupdates", "MangaUpdates"
    IGDB = "igdb", "Internet Game Database"
    OPENLIBRARY = "openlibrary", "Open Library"
    HARDCOVER = "hardcover", "Hardcover"
    COMICVINE = "comicvine", "Comic Vine"
    MANUAL = "manual", "Manual"


class MediaTypes(models.TextChoices):
    """Choices for the media type of the item."""

    TV = "tv", "TV Show"
    SEASON = "season", "TV Season"
    EPISODE = "episode", "Episode"
    MOVIE = "movie", "Movie"
    ANIME = "anime", "Anime"
    MANGA = "manga", "Manga"
    GAME = "game", "Game"
    BOOK = "book", "Book"
    COMIC = "comic", "Comic"


class Item(CalendarTriggerMixin, models.Model):
    """Model to store basic information about media items."""

    # limited by uuid for manual entries
    media_id = models.CharField(max_length=36)
    source = models.CharField(
        max_length=20,
        choices=Sources,
    )
    media_type = models.CharField(
        max_length=10,
        choices=MediaTypes,
        default=MediaTypes.MOVIE.value,
    )
    title = models.TextField()
    image = models.URLField()  # if add default, custom media entry will show the value
    season_number = models.PositiveIntegerField(null=True, blank=True)
    episode_number = models.PositiveIntegerField(null=True, blank=True)
    poster_accent_color = models.CharField(max_length=8, blank=True, default="")
    total_pages = models.PositiveIntegerField(null=True, blank=True)  # For books

    class Meta:
        """Meta options for the model."""

        constraints = [
            # Ensures items without season/episode numbers are unique
            UniqueConstraint(
                fields=["media_id", "source", "media_type"],
                condition=Q(season_number__isnull=True, episode_number__isnull=True),
                name="unique_item_without_season_episode",
            ),
            # Ensures seasons are unique within a show
            UniqueConstraint(
                fields=["media_id", "source", "media_type", "season_number"],
                condition=Q(season_number__isnull=False, episode_number__isnull=True),
                name="unique_item_with_season",
            ),
            # Ensures episodes are unique within a season
            UniqueConstraint(
                fields=[
                    "media_id",
                    "source",
                    "media_type",
                    "season_number",
                    "episode_number",
                ],
                condition=Q(season_number__isnull=False, episode_number__isnull=False),
                name="unique_item_with_season_episode",
            ),
            # Enforces that season items must have a season number but no episode number
            CheckConstraint(
                condition=Q(
                    media_type=MediaTypes.SEASON.value,
                    season_number__isnull=False,
                    episode_number__isnull=True,
                )
                | ~Q(media_type=MediaTypes.SEASON.value),
                name="season_number_required_for_season",
            ),
            # Enforces that episode items must have both season and episode numbers
            CheckConstraint(
                condition=Q(
                    media_type=MediaTypes.EPISODE.value,
                    season_number__isnull=False,
                    episode_number__isnull=False,
                )
                | ~Q(media_type=MediaTypes.EPISODE.value),
                name="season_and_episode_required_for_episode",
            ),
            # Prevents season/episode numbers from being set on non-TV media types
            CheckConstraint(
                condition=Q(
                    ~Q(
                        media_type__in=[
                            MediaTypes.SEASON.value,
                            MediaTypes.EPISODE.value,
                        ],
                    ),
                    season_number__isnull=True,
                    episode_number__isnull=True,
                )
                | Q(media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value]),
                name="no_season_episode_for_other_types",
            ),
            # Validate source choices
            CheckConstraint(
                condition=Q(source__in=Sources.values),
                name="%(app_label)s_%(class)s_source_valid",
            ),
            # Validate media_type choices
            CheckConstraint(
                condition=Q(media_type__in=MediaTypes.values),
                name="%(app_label)s_%(class)s_media_type_valid",
            ),
        ]
        ordering = ["media_id"]

    def __str__(self):
        """Return the name of the item."""
        name = self.title
        if self.season_number is not None:
            name += f" S{self.season_number}"
            if self.episode_number is not None:
                name += f"E{self.episode_number}"
        return name

    @classmethod
    def generate_manual_id(cls):
        """Generate a new ID for manual items.

        Uses a UUID to ensure uniqueness.
        """
        return str(uuid.uuid4())

    def fetch_releases(self, delay):
        """Fetch releases for the item."""
        if self._disable_calendar_triggers:
            return

        if self.media_type == MediaTypes.SEASON.value:
            # Get or create the TV item for this season
            try:
                tv_item = Item.objects.get(
                    media_id=self.media_id,
                    source=self.source,
                    media_type=MediaTypes.TV.value,
                )
            except Item.DoesNotExist:
                # Get metadata for the TV show
                tv_metadata = providers.services.get_media_metadata(
                    MediaTypes.TV.value,
                    self.media_id,
                    self.source,
                )
                tv_item = Item.objects.create(
                    media_id=self.media_id,
                    source=self.source,
                    media_type=MediaTypes.TV.value,
                    title=tv_metadata["title"],
                    image=tv_metadata["image"],
                )
                logger.info("Created TV item %s for season %s", tv_item, self)

            # Process the TV item instead of the season
            items_to_process = [tv_item]
        else:
            items_to_process = [self]

        if delay:
            events.tasks.reload_calendar.delay(items_to_process=items_to_process)
        else:
            events.tasks.reload_calendar(items_to_process=items_to_process)


class MediaLike(models.Model):
    """Canonical user-level like for a media item."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="media_likes")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["user", "item"],
                name="app_medialike_unique_user_item",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at", "-id"]),
            models.Index(fields=["item", "-created_at"]),
        ]
        ordering = ["-created_at", "-id"]


class MediaManager(models.Manager):
    """Custom manager for media models."""

    def get_historical_models(self):
        """Return list of historical model names."""
        return [f"historical{media_type}" for media_type in MediaTypes.values]

    def get_media_list(self, user, media_type, status_filter, sort_filter, search=None):
        """Get media list based on filters, sorting and search."""
        model = apps.get_model(app_label="app", model_name=media_type)
        queryset = model.objects.filter(user=user.id)

        if status_filter != users.models.MediaStatusChoices.ALL:
            queryset = queryset.filter(status=status_filter)

        if search:
            queryset = queryset.filter(item__title__icontains=search)

        queryset = queryset.annotate(
            repeats=Window(
                expression=Count("id"),
                partition_by=[F("item")],
            ),
            row_number=Window(
                expression=RowNumber(),
                partition_by=[F("item")],
                order_by=F("created_at").desc(),
            ),
        ).filter(row_number=1)

        queryset = queryset.select_related("item")
        queryset = self._apply_prefetch_related(queryset, media_type)

        if sort_filter:
            return self._sort_media_list(queryset, sort_filter, media_type)
        return queryset

    def _apply_prefetch_related(self, queryset, media_type):
        """Apply appropriate prefetch_related based on media type."""
        # Apply media-specific prefetches
        if media_type == MediaTypes.TV.value:
            return queryset.prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.select_related("item"),
                ),
                Prefetch(
                    "seasons__episodes",
                    queryset=Episode.objects.select_related("item"),
                ),
            )

        base_queryset = queryset.prefetch_related(
            Prefetch(
                "item__event_set",
                queryset=events.models.Event.objects.all(),
                to_attr="prefetched_events",
            ),
        )

        if media_type == MediaTypes.SEASON.value:
            return base_queryset.prefetch_related(
                Prefetch(
                    "episodes",
                    queryset=Episode.objects.select_related("item"),
                ),
            )

        return base_queryset

    def _sort_media_list(self, queryset, sort_filter, media_type=None):
        """Sort media list using SQL sorting with annotations for calculated fields."""
        if media_type == MediaTypes.TV.value:
            return self._sort_tv_media_list(queryset, sort_filter)
        if media_type == MediaTypes.SEASON.value:
            return self._sort_season_media_list(queryset, sort_filter)

        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_tv_media_list(self, queryset, sort_filter):
        """Sort TV media list based on the sort criteria."""
        if sort_filter == "start_date":
            # Annotate with the minimum start_date from related seasons/episodes
            queryset = queryset.annotate(
                calculated_start_date=models.Min(
                    "seasons__episodes__end_date",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            return queryset.order_by(
                models.F("calculated_start_date").asc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "end_date":
            # Annotate with the maximum end_date from related seasons/episodes
            queryset = queryset.annotate(
                calculated_end_date=models.Max(
                    "seasons__episodes__end_date",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            return queryset.order_by(
                models.F("calculated_end_date").desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "progress":
            # Annotate with the sum of episodes watched (excluding season 0)
            queryset = queryset.annotate(
                # Count episodes in regular seasons (season_number > 0)
                calculated_progress=models.Count(
                    "seasons__episodes",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            return queryset.order_by(
                "-calculated_progress",
                models.functions.Lower("item__title"),
            )

        # Default to generic sorting
        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_season_media_list(self, queryset, sort_filter):
        """Sort Season media list based on the sort criteria."""
        if sort_filter == "start_date":
            # Annotate with the minimum end_date from related episodes
            queryset = queryset.annotate(
                calculated_start_date=models.Min("episodes__end_date"),
            )
            return queryset.order_by(
                models.F("calculated_start_date").asc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "end_date":
            # Annotate with the maximum end_date from related episodes
            queryset = queryset.annotate(
                calculated_end_date=models.Max("episodes__end_date"),
            )
            return queryset.order_by(
                models.F("calculated_end_date").desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        if sort_filter == "progress":
            # Annotate with the maximum episode number
            queryset = queryset.annotate(
                calculated_progress=models.Max("episodes__item__episode_number"),
            )
            return queryset.order_by(
                "-calculated_progress",
                models.functions.Lower("item__title"),
            )

        # Default to generic sorting
        return self._sort_generic_media_list(queryset, sort_filter)

    def _sort_generic_media_list(self, queryset, sort_filter):
        """Apply generic sorting logic for all media types."""
        # Handle sorting by date fields with special null handling
        if sort_filter in ("start_date", "end_date"):
            # For start_date, sort ascending (earliest first)
            if sort_filter == "start_date":
                return queryset.order_by(
                    models.F(sort_filter).asc(nulls_last=True),
                    models.functions.Lower("item__title"),
                )
            # For other date fields, sort descending (latest first)
            return queryset.order_by(
                models.F(sort_filter).desc(nulls_last=True),
                models.functions.Lower("item__title"),
            )

        # Handle sorting by Item fields
        item_fields = [f.name for f in Item._meta.fields]
        if sort_filter in item_fields:
            if sort_filter == "title":
                # Case-insensitive title sorting
                return queryset.order_by(models.functions.Lower("item__title"))
            # Default sorting for other Item fields
            return queryset.order_by(
                f"-item__{sort_filter}",
                models.functions.Lower("item__title"),
            )

        # Default sorting by media field
        return queryset.order_by(
            models.F(sort_filter).desc(nulls_last=True),
            models.functions.Lower("item__title"),
        )

    def get_home_status(
        self,
        user,
        status,
        sort_by,
        items_limit,
        specific_media_type=None,
    ):
        """Get a home media list for a specific status grouped by media type."""
        list_by_type = {}
        media_types = self._get_media_types_to_process(user, specific_media_type)

        for media_type in media_types:
            # Get base media list for the requested status
            media_list = self.get_media_list(
                user=user,
                media_type=media_type,
                status_filter=status,
                sort_filter=None,
            )

            if not media_list:
                continue

            # Annotate with max_progress and next_event
            self.annotate_max_progress(media_list, media_type)
            self._annotate_next_event(media_list)

            # Sort the media list
            sorted_list = self._sort_home_media(media_list, sort_by)

            # Apply pagination
            total_count = len(sorted_list)
            if specific_media_type:
                paginated_list = sorted_list[items_limit:]
            else:
                paginated_list = sorted_list[:items_limit]

            list_by_type[media_type] = {
                "items": paginated_list,
                "total": total_count,
            }

        return list_by_type

    def _get_media_types_to_process(self, user, specific_media_type):
        """Determine which media types to process based on user settings."""
        if specific_media_type:
            return [specific_media_type]

        # Get active types excluding TV (TV shows are tracked at season level)
        return [
            media_type
            for media_type in user.get_active_media_types()
            if media_type != MediaTypes.TV.value
        ]

    def _annotate_next_event(self, media_list):
        """Annotate next_event for media items."""
        current_time = timezone.now()

        for media in media_list:
            # Get future events sorted by datetime
            future_events = sorted(
                [
                    event
                    for event in getattr(media.item, "prefetched_events", [])
                    if event.datetime > current_time
                ],
                key=lambda e: e.datetime,
            )

            media.next_event = future_events[0] if future_events else None

    def _sort_home_media(self, media_list, sort_by):
        """Sort home media based on the selected sort criteria."""
        # Define primary sort functions based on sort_by
        primary_sort_functions = {
            users.models.HomeSortChoices.UPCOMING: lambda x: (
                x.next_event is None,
                x.next_event.datetime if x.next_event else None,
            ),
            users.models.HomeSortChoices.RECENT: lambda x: (
                -timezone.datetime.timestamp(
                    x.progressed_at if x.progressed_at is not None else x.created_at,
                )
            ),
            users.models.HomeSortChoices.COMPLETION: lambda x: (
                x.max_progress is None,
                -(
                    x.progress / x.max_progress * 100
                    if x.max_progress and x.max_progress > 0
                    else 0
                ),
            ),
            users.models.HomeSortChoices.EPISODES_LEFT: lambda x: (
                x.max_progress is None,
                (x.max_progress - x.progress if x.max_progress else 0),
            ),
            users.models.HomeSortChoices.TITLE: lambda x: x.item.title.lower(),
        }

        primary_sort_function = primary_sort_functions[sort_by]

        return sorted(
            media_list,
            key=lambda x: (
                primary_sort_function(x),
                -timezone.datetime.timestamp(
                    x.progressed_at if x.progressed_at is not None else x.created_at,
                ),
                x.item.title.lower(),
            ),
        )

    def annotate_max_progress(self, media_list, media_type):
        """Annotate max_progress for all media items."""
        current_datetime = timezone.now()

        if media_type == MediaTypes.MOVIE.value:
            for media in media_list:
                media.max_progress = 1
            return

        if media_type == MediaTypes.TV.value:
            self._annotate_tv_released_episodes(media_list, current_datetime)
            return

        if media_type == MediaTypes.BOOK.value:
            for media in media_list:
                media.max_progress = media.item.total_pages
            return

        # For other media types, calculate max_progress from events
        # Create a dictionary mapping item_id to max content_number
        max_progress_dict = {}

        item_ids = [media.item.id for media in media_list]

        # Fetch all relevant events in a single query
        events_data = events.models.Event.objects.filter(
            item_id__in=item_ids,
            datetime__lte=current_datetime,
        ).values("item_id", "content_number")

        # Process events to find max content number per item
        for event in events_data:
            item_id = event["item_id"]
            content_number = event["content_number"]
            if content_number is not None:
                current_max = max_progress_dict.get(item_id, 0)
                max_progress_dict[item_id] = max(current_max, content_number)

        for media in media_list:
            media.max_progress = max_progress_dict.get(media.item.id)

    def _annotate_tv_released_episodes(self, tv_list, current_datetime):
        """Annotate TV shows with the number of released episodes."""
        # Prefetch all relevant events in one query
        released_events = events.models.Event.objects.filter(
            item__media_id__in=[tv.item.media_id for tv in tv_list],
            item__source=tv_list[0].item.source if tv_list else None,
            item__media_type=MediaTypes.SEASON.value,
            item__season_number__gt=0,
            datetime__lte=current_datetime,
            content_number__isnull=False,
        ).select_related("item")

        # Create a dictionary to store max episode numbers per season per show
        released_episodes = {}

        for event in released_events:
            media_id = event.item.media_id
            season_number = event.item.season_number
            episode_number = event.content_number

            if media_id not in released_episodes:
                released_episodes[media_id] = {}

            if (
                season_number not in released_episodes[media_id]
                or episode_number > released_episodes[media_id][season_number]
            ):
                released_episodes[media_id][season_number] = episode_number

        # Calculate total released episodes per TV show
        for tv in tv_list:
            tv_episodes = released_episodes.get(tv.item.media_id, {})
            tv.max_progress = sum(tv_episodes.values()) if tv_episodes else 0

    def fetch_media_for_items(self, media_types, item_ids, user, status_filter=None):
        """Fetch media objects for given items, optionally filtering by status.

        Args:
            media_types: Iterable of media type strings to query
            item_ids: QuerySet or list of item IDs to fetch media for
            user: User to filter media by
            status_filter: Optional status value to filter by

        Returns:
            dict mapping item_id to media object
        """
        media_by_item_id = {}

        for media_type in media_types:
            model = apps.get_model("app", media_type)

            if media_type == MediaTypes.EPISODE.value:
                filter_kwargs = {
                    "item__in": item_ids,
                    "related_season__user": user,
                }
                if status_filter:
                    filter_kwargs["related_season__status"] = status_filter
            else:
                filter_kwargs = {
                    "item__in": item_ids,
                    "user": user,
                }
                if status_filter:
                    filter_kwargs["status"] = status_filter

            queryset = model.objects.filter(**filter_kwargs).select_related("item")
            queryset = self._apply_prefetch_related(queryset, media_type)
            self.annotate_max_progress(queryset, media_type)

            for entry in queryset:
                media_by_item_id.setdefault(entry.item_id, entry)

        return media_by_item_id

    def get_media(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get user media object given the media type and item."""
        model = apps.get_model(app_label="app", model_name=media_type)
        params = self._get_media_params(
            user,
            media_type,
            instance_id,
        )

        return model.objects.get(**params)

    def get_media_prefetch(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get user media object with prefetch_related applied."""
        model = apps.get_model(app_label="app", model_name=media_type)
        params = self._get_media_params(
            user,
            media_type,
            instance_id,
        )

        queryset = model.objects.filter(**params)

        queryset = self._apply_prefetch_related(queryset, media_type)
        self.annotate_max_progress(queryset, media_type)

        return queryset.get()

    def _get_media_params(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get the common filter parameters for media queries."""
        params = {"id": instance_id}

        if media_type == MediaTypes.EPISODE.value:
            params["related_season__user"] = user
        else:
            params["user"] = user

        return params

    def filter_media(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
    ):
        """Filter media objects based on parameters."""
        model = apps.get_model(app_label="app", model_name=media_type)
        params = self._filter_media_params(
            media_type,
            media_id,
            source,
            user,
            season_number,
            episode_number,
        )

        return model.objects.filter(**params)

    def filter_media_prefetch(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
    ):
        """Filter user media object with prefetch_related applied."""
        queryset = self.filter_media(
            user,
            media_id,
            media_type,
            source,
            season_number,
            episode_number,
        ).select_related("item")
        queryset = self._apply_prefetch_related(queryset, media_type)
        self.annotate_max_progress(queryset, media_type)

        return queryset

    def _filter_media_params(
        self,
        media_type,
        media_id,
        source,
        user,
        season_number=None,
        episode_number=None,
    ):
        """Get the common filter parameters for media queries."""
        params = {
            "item__media_type": media_type,
            "item__source": source,
            "item__media_id": media_id,
        }

        if media_type == MediaTypes.SEASON.value:
            params["item__season_number"] = season_number
            params["user"] = user
        elif media_type == MediaTypes.EPISODE.value:
            params["item__season_number"] = season_number
            params["item__episode_number"] = episode_number
            params["related_season__user"] = user
        else:
            params["user"] = user

        return params


class Status(models.TextChoices):
    """Choices for item status."""

    COMPLETED = "Completed", "Completed"
    IN_PROGRESS = "In progress", "In Progress"
    PLANNING = "Planning", "Planning"
    PAUSED = "Paused", "Paused"
    DROPPED = "Dropped", "Dropped"


class UserMessageLevel(models.TextChoices):
    """Choices for persistent user messages."""

    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"
    INFO = "info", "Info"


class UserMessage(models.Model):
    """Persistent user notification shown in the toast UI."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    level = models.CharField(
        max_length=20,
        choices=UserMessageLevel,
        default=UserMessageLevel.INFO,
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    shown_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Meta options for the model."""

        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["user", "shown_at"],
                name="app_umsg_user_shown_idx",
            ),
        ]

    def __str__(self):
        """Return the message text."""
        return self.message

    @property
    def tags(self):
        """Return a Django-messages-compatible level tag."""
        return self.level


class Media(models.Model):
    """Abstract model for all media types."""

    history = HistoricalRecords(
        cascade_delete_history=True,
        inherit=True,
        excluded_fields=[
            "item",
            "progressed_at",
            "user",
            "related_tv",
            "created_at",
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    score = models.DecimalField(
        null=True,
        blank=True,
        max_digits=3,
        decimal_places=1,
        validators=[
            DecimalValidator(3, 1),
            MinValueValidator(0),
            MaxValueValidator(10),
        ],
    )
    progress = models.PositiveIntegerField(default=0)
    progressed_at = MonitorField(monitor="progress")
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.COMPLETED.value,
    )
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        """Meta options for the model."""

        abstract = True
        ordering = ["user", "item", "-created_at"]

    def __str__(self):
        """Return the title of the media."""
        return self.item.__str__()

    def save(self, *args, **kwargs):
        """Save the media instance."""
        if self.tracker.has_changed("progress"):
            self.process_progress()

        if self.tracker.has_changed("status"):
            self.process_status()

        super().save(*args, **kwargs)

    def create_user_message(self, message, level):
        """Create a persistent user notification."""
        message_context = str(self)
        if message_context and not message.startswith(message_context):
            message = f"{message_context} {message}"

        logger.info("Creating user message for %s: %s", self.user, message)

        UserMessage.objects.create(
            user=self.user,
            level=level,
            message=message,
        )

    def process_progress(self):
        """Update fields depending on the progress of the media."""
        if self.progress < 0:
            self.progress = 0
        elif self.status == Status.IN_PROGRESS.value:
            max_progress = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )["max_progress"]

            if max_progress:
                self.progress = min(self.progress, max_progress)

                if self.progress == max_progress:
                    self.status = Status.COMPLETED.value

                    now = timezone.now().replace(second=0, microsecond=0)
                    self.end_date = now

    def process_status(self):
        """Update fields depending on the status of the media."""
        if self.status == Status.COMPLETED.value:
            max_progress = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )["max_progress"]

            if max_progress:
                self.progress = max_progress

        self.item.fetch_releases(delay=True)

    @property
    def formatted_score(self):
        """Return as int if score is 10.0 or 0.0, otherwise show decimal."""
        if self.score is not None:
            max_score = 10
            min_score = 0
            if self.score in (max_score, min_score):
                return int(self.score)
            return self.score
        return None

    @property
    def formatted_progress(self):
        """Return the progress of the media in a formatted string."""
        return str(self.progress)

    def increase_progress(self):
        """Increase the progress of the media by one."""
        self.progress += 1
        self.save()
        logger.info("Incresed progress of %s to %s", self, self.progress)

    def decrease_progress(self):
        """Decrease the progress of the media by one."""
        self.progress -= 1
        self.save()
        logger.info("Decreased progress of %s to %s", self, self.progress)

    def mark_consumed(self):
        """Mark media as consumed, setting appropriate fields."""
        self.status = Status.COMPLETED.value
        
        # Only set end_date if not already set
        if not self.end_date:
            self.end_date = timezone.now()
        
        # Set progress to max if determinable
        if hasattr(self, 'get_max_progress'):
            self.progress = self.get_max_progress()
            
        self.save()
        return self


class BasicMedia(Media):
    """Model for basic media types."""

    objects = MediaManager()


class TV(Media):
    """Model for TV shows."""

    tracker = FieldTracker()

    class Meta:
        """Meta options for the model."""

        ordering = ["user", "item"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "item"],
                name="%(app_label)s_%(class)s_unique_item_user",
            ),
        ]

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == Status.COMPLETED.value:
                self._completed()

            elif self.status == Status.DROPPED.value:
                self._mark_in_progress_seasons_as_dropped()

            elif (
                self.status == Status.IN_PROGRESS.value
                and not self.seasons.filter(status=Status.IN_PROGRESS.value).exists()
            ):
                self._start_next_available_season()

            self.item.fetch_releases(delay=True)

    @property
    def progress(self):
        """Return the total episodes watched for the TV show."""
        return sum(
            season.progress
            for season in self.seasons.all()
            if season.item.season_number != 0
        )

    @property
    def last_watched(self):
        """Return the latest watched episode in SxxExx format."""
        watched_episodes = [
            {
                "season": season.item.season_number,
                "episode": episode.item.episode_number,
                "end_date": episode.end_date,
            }
            for season in self.seasons.all()
            if hasattr(season, "episodes") and season.item.season_number != 0
            for episode in season.episodes.all()
            if episode.end_date is not None
        ]

        if not watched_episodes:
            return ""

        latest_episode = max(
            watched_episodes,
            key=lambda x: (x["end_date"], x["season"], x["episode"]),
        )

        return f"S{latest_episode['season']:02d}E{latest_episode['episode']:02d}"

    @property
    def progressed_at(self):
        """Return the date when the last episode was watched."""
        dates = [
            season.progressed_at
            for season in self.seasons.all()
            if season.progressed_at and season.item.season_number != 0
        ]
        return max(dates) if dates else None

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            season.start_date
            for season in self.seasons.all()
            if season.start_date and season.item.season_number != 0
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [
            season.end_date
            for season in self.seasons.all()
            if season.end_date and season.item.season_number != 0
        ]
        return max(dates) if dates else None

    def _completed(self):
        """Create remaining seasons and episodes for a TV show."""
        from datetime import datetime
        import logging
        logger = logging.getLogger(__name__)
        
        tv_metadata = providers.services.get_media_metadata(
            self.item.media_type,
            self.item.media_id,
            self.item.source,
        )
        max_progress = tv_metadata["max_progress"]

        if not max_progress or self.progress > max_progress:
            return

        seasons_to_create = []
        seasons_to_update = []
        episodes_to_create = []
        current_date = timezone.localdate()
        tv_completed = True

        season_numbers = [
            season["season_number"]
            for season in tv_metadata["related"]["seasons"]
            if season["season_number"] != 0
        ]
        tv_with_seasons_metadata = providers.services.get_media_metadata(
            "tv_with_seasons",
            self.item.media_id,
            self.item.source,
            season_numbers,
        )
        
        now = timezone.now()
        
        for season_number in season_numbers:
            season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]
            
            # Debug logging for season metadata structure
            logger.info(f"Season {season_number} metadata keys: {list(season_metadata.keys())}")
            logger.info(f"Season {season_number} details keys: {list(season_metadata.get('details', {}).keys())}")
            
            # Check if season has episodes (skip seasons with 0 episodes)
            episode_count = season_metadata.get("details", {}).get("episodes", 0)
            logger.info(f"Season {season_number} episode_count: {episode_count}")
            
            if episode_count == 0:
                logger.info(f"Skipping season {season_number} with 0 episodes")
                continue
            else:
                logger.info(f"Season {season_number} has {episode_count} episodes, processing")

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=self.item.source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                defaults={
                    "title": self.item.title,
                    "image": season_metadata["image"],
                },
            )
            try:
                season_instance = Season.objects.get(
                    item=item,
                    user=self.user,
                )
                target_status = season_instance.get_completion_status(
                    season_metadata,
                    unreleased_only_status=Status.PLANNING.value,
                    current_date=current_date,
                )

                if season_instance.status != target_status:
                    season_instance.status = target_status
                    seasons_to_update.append(season_instance)

            except Season.DoesNotExist:
                season_instance = Season(
                    item=item,
                    score=None,
                    notes="",
                    related_tv=self,
                    user=self.user,
                )
                target_status = season_instance.get_completion_status(
                    season_metadata,
                    unreleased_only_status=Status.PLANNING.value,
                    current_date=current_date,
                )
                season_instance.status = target_status
                seasons_to_create.append(
                    season_instance,
                )

            if target_status != Status.COMPLETED.value:
                tv_completed = False

        bulk_create_with_history(seasons_to_create, Season)
        bulk_update_with_history(seasons_to_update, Season, ["status"])

        for season_instance in seasons_to_create + seasons_to_update:
            season_metadata = tv_with_seasons_metadata[
                f"season/{season_instance.item.season_number}"
            ]
            episodes_to_create.extend(
                season_instance.get_remaining_eps(
                    season_metadata,
                    current_date=current_date,
                ),
            )
        bulk_create_with_history(episodes_to_create, Episode)

        if episodes_to_create:
            created_episodes_count = len(episodes_to_create)
            episode_label = "episode" if created_episodes_count == 1 else "episodes"
            self.create_user_message(
                f"had {created_episodes_count} released {episode_label} marked "
                "as watched automatically.",
                level=UserMessageLevel.INFO,
            )

        if not tv_completed:
            self.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [self],
                TV,
                fields=["status"],
            )
            self.create_user_message(
                "was left in progress because unreleased episodes or seasons remain.",
                level=UserMessageLevel.WARNING,
            )

    def _mark_in_progress_seasons_as_dropped(self):
        """Mark all in-progress seasons as dropped."""
        in_progress_seasons = list(
            self.seasons.filter(status=Status.IN_PROGRESS.value),
        )

        for season in in_progress_seasons:
            season.status = Status.DROPPED.value

        if in_progress_seasons:
            bulk_update_with_history(
                in_progress_seasons,
                Season,
                fields=["status"],
            )

    def _start_next_available_season(
        self,
        min_season_number=0,
    ):
        """Find the next available season to watch and set it to in-progress."""
        min_season_number = int(min_season_number or 0)
        current_date = timezone.localdate()
        existing_seasons = {
            season.item.season_number: season
            for season in self.seasons.filter(
                item__season_number__gt=min_season_number,
            ).order_by("item__season_number")
        }
        tv_metadata = providers.services.get_media_metadata(
            self.item.media_type,
            self.item.media_id,
            self.item.source,
        )
        related_seasons = tv_metadata.get("related", {}).get("seasons", [])

        season_started = False
        started_season_number = None

        for season_data in related_seasons:
            season_number = season_data["season_number"]
            if season_number <= min_season_number:
                continue

            next_unwatched_season = existing_seasons.get(season_number)
            if (
                next_unwatched_season
                and next_unwatched_season.status == Status.COMPLETED.value
            ):
                continue

            if not app.helpers.is_released_date(
                season_data.get("first_air_date"),
                current_date,
            ):
                continue

            if next_unwatched_season is None:
                item, _ = Item.objects.get_or_create(
                    media_id=self.item.media_id,
                    source=self.item.source,
                    media_type=MediaTypes.SEASON.value,
                    season_number=season_number,
                    defaults={
                        "title": self.item.title,
                        "image": season_data["image"],
                    },
                )

                next_unwatched_season = Season(
                    item=item,
                    user=self.user,
                    related_tv=self,
                    status=Status.IN_PROGRESS.value,
                )
                bulk_create_with_history([next_unwatched_season], Season)
                season_started = True
                started_season_number = season_number
                break

            if next_unwatched_season.status != Status.IN_PROGRESS.value:
                next_unwatched_season.status = Status.IN_PROGRESS.value
                bulk_update_with_history(
                    [next_unwatched_season],
                    Season,
                    fields=["status"],
                )
                season_started = True
                started_season_number = season_number
            else:
                season_started = True
            break

        if season_started and self.status != Status.IN_PROGRESS.value:
            self.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [self],
                TV,
                fields=["status"],
            )

        if started_season_number is not None:
            self.create_user_message(
                f"Season {started_season_number} was marked as in progress "
                "automatically.",
                level=UserMessageLevel.INFO,
            )

        return season_started

    def _handle_completed_season(
        self,
        completed_season_number,
    ):
        """Start the next season, or complete the TV show if no seasons remain."""
        if self._start_next_available_season(
            completed_season_number,
        ):
            return

        incomplete_seasons_exist = (
            self.seasons.filter(
                item__season_number__gt=0,
            )
            .exclude(
                status=Status.COMPLETED.value,
            )
            .exists()
        )

        if incomplete_seasons_exist and self.status != Status.IN_PROGRESS.value:
            self.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [self],
                TV,
                fields=["status"],
            )
            self.create_user_message(
                "remains in progress because another season is still "
                "pending or has not aired yet.",
                level=UserMessageLevel.INFO,
            )

        elif not incomplete_seasons_exist and self.status != Status.COMPLETED.value:
            self.status = Status.COMPLETED.value
            bulk_update_with_history(
                [self],
                TV,
                fields=["status"],
            )
            self.create_user_message(
                "was marked as completed automatically.",
                level=UserMessageLevel.SUCCESS,
            )


class Season(Media):
    """Model for seasons of TV shows."""

    related_tv = models.ForeignKey(
        TV,
        on_delete=models.CASCADE,
        related_name="seasons",
    )

    tracker = FieldTracker()

    class Meta:
        """Limit the uniqueness of seasons.

        Only one season per media can have the same season number.
        """

        constraints = [
            models.UniqueConstraint(
                fields=["related_tv", "item"],
                name="%(app_label)s_season_unique_tv_item",
            ),
        ]

    def __str__(self):
        """Return the title of the media and season number."""
        return f"{self.item.title} S{self.item.season_number}"

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        # if related_tv is not set
        if self.related_tv_id is None:
            self.related_tv = self.get_tv()

        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == Status.COMPLETED.value:
                season_metadata = providers.services.get_media_metadata(
                    MediaTypes.SEASON.value,
                    self.item.media_id,
                    self.item.source,
                    [self.item.season_number],
                )
                current_date = timezone.localdate()
                target_status = self.get_completion_status(
                    season_metadata,
                    unreleased_only_status=Status.IN_PROGRESS.value,
                    current_date=current_date,
                )
                episodes_to_create = self.get_remaining_eps(
                    season_metadata,
                    current_date=current_date,
                )
                if episodes_to_create:
                    bulk_create_with_history(
                        episodes_to_create,
                        Episode,
                    )
                    created_episodes_count = len(episodes_to_create)
                    episode_label = (
                        "episode" if created_episodes_count == 1 else "episodes"
                    )
                    self.create_user_message(
                        f"had {created_episodes_count} released {episode_label} "
                        "marked as watched automatically.",
                        level=UserMessageLevel.INFO,
                    )

                if target_status == Status.COMPLETED.value:
                    self.related_tv._handle_completed_season(
                        self.item.season_number,
                    )
                else:
                    self.status = target_status
                    bulk_update_with_history(
                        [self],
                        Season,
                        fields=["status"],
                    )
                    self.create_user_message(
                        "was left in progress because unreleased episodes remain.",
                        level=UserMessageLevel.WARNING,
                    )

                    if self.related_tv.status != Status.IN_PROGRESS.value:
                        self.related_tv.status = Status.IN_PROGRESS.value
                        bulk_update_with_history(
                            [self.related_tv],
                            TV,
                            fields=["status"],
                        )

            elif (
                self.status == Status.DROPPED.value
                and self.related_tv.status != Status.DROPPED.value
            ):
                self.related_tv.status = Status.DROPPED.value
                bulk_update_with_history(
                    [self.related_tv],
                    TV,
                    fields=["status"],
                )

            elif (
                self.status == Status.IN_PROGRESS.value
                and self.related_tv.status != Status.IN_PROGRESS.value
            ):
                self.related_tv.status = Status.IN_PROGRESS.value
                bulk_update_with_history(
                    [self.related_tv],
                    TV,
                    fields=["status"],
                )

            self.item.fetch_releases(delay=True)

    def _get_latest_watched_episode_number(self):
        """Return the highest watched episode number for the season."""
        if self.pk is None:
            return 0

        latest_watched_ep_num = Episode.objects.filter(related_season=self).aggregate(
            latest_watched_ep_num=Max("item__episode_number"),
        )["latest_watched_ep_num"]

        return latest_watched_ep_num or 0

    def get_completion_status(
        self,
        season_metadata,
        unreleased_only_status,
        current_date,
    ):
        """Return the season status after completing all already released episodes."""
        latest_watched_ep_num = self._get_latest_watched_episode_number()
        released_remaining_exists = False
        unreleased_remaining_exists = False

        for episode in season_metadata["episodes"]:
            if episode["episode_number"] <= latest_watched_ep_num:
                continue

            if app.helpers.is_released_date(episode.get("air_date"), current_date):
                released_remaining_exists = True
            else:
                unreleased_remaining_exists = True

        if not unreleased_remaining_exists:
            return Status.COMPLETED.value

        if latest_watched_ep_num > 0 or released_remaining_exists:
            return Status.IN_PROGRESS.value

        return unreleased_only_status

    @property
    def progress(self):
        """Return the current episode number of the season."""
        episodes = self.episodes.all()
        if not episodes:
            return 0

        if self.status == Status.IN_PROGRESS.value:
            # Sort by most recently watched, then by episode number
            sorted_episodes = sorted(
                episodes,
                key=lambda e: (
                    e.end_date is not None,
                    e.end_date.timestamp() if e.end_date else 0,
                    e.item.episode_number,
                ),
                reverse=True,
            )
        else:
            # Default sorting by episode_number
            sorted_episodes = sorted(
                episodes,
                key=lambda e: -e.item.episode_number,
            )

        return sorted_episodes[0].item.episode_number

    @property
    def progressed_at(self):
        """Return the date when the last episode was watched."""
        dates = [
            episode.end_date
            for episode in self.episodes.all()
            if episode.end_date is not None
        ]
        return max(dates) if dates else None

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            episode.end_date
            for episode in self.episodes.all()
            if episode.end_date is not None
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [
            episode.end_date
            for episode in self.episodes.all()
            if episode.end_date is not None
        ]
        return max(dates) if dates else None

    def increase_progress(self):
        """Watch the next episode of the season."""
        season_metadata = providers.services.get_media_metadata(
            MediaTypes.SEASON.value,
            self.item.media_id,
            self.item.source,
            [self.item.season_number],
        )
        episodes = season_metadata["episodes"]

        if self.progress == 0:
            # start watching from the first episode
            next_episode_number = episodes[0]["episode_number"]
        else:
            next_episode_number = providers.tmdb.find_next_episode(
                self.progress,
                episodes,
            )

        now = timezone.now().replace(second=0, microsecond=0)

        if next_episode_number:
            self.watch(next_episode_number, now)
        else:
            logger.info("No more episodes to watch.")

    def watch(self, episode_number, end_date):
        """Create or add a repeat to an episode of the season."""
        item = self.get_episode_item(episode_number)

        episode = Episode.objects.create(
            related_season=self,
            item=item,
            end_date=end_date,
        )
        logger.info(
            "%s created successfully.",
            episode,
        )

    def decrease_progress(self):
        """Unwatch the current episode of the season."""
        self.unwatch(self.progress)

    def unwatch(self, episode_number):
        """Unwatch the episode instance."""
        item = self.get_episode_item(episode_number)

        episodes = Episode.objects.filter(
            related_season=self,
            item=item,
        ).order_by("-end_date")

        episode = episodes.first()

        if episode is None:
            logger.warning(
                "Episode %s does not exist.",
                self.item,
            )
            return

        # Get count before deletion for logging
        remaining_count = episodes.count() - 1

        episode.delete()
        logger.info(
            "Deleted %s S%02dE%02d (%d remaining instances)",
            self.item.title,
            self.item.season_number,
            episode_number,
            remaining_count,
        )

    def get_tv(self):
        """Get related TV instance for a season and create it if it doesn't exist."""
        try:
            tv = TV.objects.get(
                item__media_id=self.item.media_id,
                item__media_type=MediaTypes.TV.value,
                item__season_number=None,
                item__source=self.item.source,
                user=self.user,
            )
        except TV.DoesNotExist:
            tv_metadata = providers.services.get_media_metadata(
                MediaTypes.TV.value,
                self.item.media_id,
                self.item.source,
            )

            # creating tv with multiple seasons from a completed season
            if (
                self.status == Status.COMPLETED.value
                and tv_metadata["details"]["seasons"] > 1
            ):
                status = Status.IN_PROGRESS.value
            else:
                status = self.status

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                defaults={
                    "title": tv_metadata["title"],
                    "image": tv_metadata["image"],
                },
            )

            tv = TV(
                item=item,
                score=None,
                status=status,
                notes="",
                user=self.user,
            )

            # save_base to avoid custom save method
            TV.save_base(tv)

            logger.info("%s did not exist, it was created successfully.", tv)

        return tv

    def get_remaining_eps(
        self,
        season_metadata,
        current_date,
    ):
        """Return episodes needed to complete a season."""
        episodes_list = season_metadata.get("episodes")
        if episodes_list is None:
            return []
        
        if not isinstance(episodes_list, (list, tuple)):
            return []
        
        latest_watched_ep_num = self._get_latest_watched_episode_number()
        episodes_to_create = []

        # Calculate current time once before the loop
        now = timezone.now().replace(second=0, microsecond=0)

        # Create Episode objects for the remaining episodes
        for i, episode in enumerate(reversed(episodes_list)):
            if not isinstance(episode, dict):
                continue
                
            episode_number = episode.get("episode_number")
            if episode_number is None:
                continue
                
            if episode_number <= latest_watched_ep_num:
                break

            if not app.helpers.is_released_date(
                episode.get("air_date"),
                current_date,
            ):
                continue

            item = self.get_episode_item(episode_number, season_metadata)

            # Resolve end_date based on user preference
            end_date = self.user.resolve_watch_date(now, episode.get("air_date"))

            episode_db = Episode(
                related_season=self,
                item=item,
                end_date=end_date,
            )
            episodes_to_create.append(episode_db)

        return episodes_to_create

    def get_episode_item(self, episode_number, season_metadata=None):
        """Get the episode item instance, create it if it doesn't exist."""
        if not season_metadata:
            season_metadata = providers.services.get_media_metadata(
                MediaTypes.SEASON.value,
                self.item.media_id,
                self.item.source,
                [self.item.season_number],
            )

        image = settings.IMG_NONE
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == int(episode_number):
                if episode.get("still_path"):
                    image = (
                        f"https://image.tmdb.org/t/p/original{episode['still_path']}"
                    )
                elif "image" in episode:
                    # for manual seasons
                    image = episode["image"]
                else:
                    image = settings.IMG_NONE
                break

        item, _ = Item.objects.get_or_create(
            media_id=self.item.media_id,
            source=self.item.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=self.item.season_number,
            episode_number=episode_number,
            defaults={
                "title": self.item.title,
                "image": image,
            },
        )

        return item


class Episode(models.Model):
    """Model for episodes of a season."""

    history = HistoricalRecords(
        cascade_delete_history=True,
        excluded_fields=["item", "related_season", "created_at"],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    related_season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    end_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Meta options for the model."""

        ordering = [
            "related_season",
            "item__episode_number",
            "-end_date",
            "-created_at",
        ]

    def __str__(self):
        """Return the season and episode number."""
        return self.item.__str__()

    def save(self, *args, **kwargs):
        """Save the episode instance."""
        super().save(*args, **kwargs)

        season_number = self.item.season_number
        tv_with_seasons_metadata = providers.services.get_media_metadata(
            "tv_with_seasons",
            self.item.media_id,
            self.item.source,
            [season_number],
        )
        season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]
        max_progress = len(season_metadata["episodes"])

        # clear prefetch cache to get the updated episodes
        self.related_season.refresh_from_db()

        is_finale = self.item.episode_number == max_progress
        season_just_completed = False
        if is_finale:
            if self.related_season.status != Status.COMPLETED.value:
                self.related_season.status = Status.COMPLETED.value
                bulk_update_with_history(
                    [self.related_season],
                    Season,
                    fields=["status"],
                )
                season_just_completed = True
                self.related_season.create_user_message(
                    "was marked as completed automatically.",
                    level=UserMessageLevel.SUCCESS,
                )

        elif self.related_season.status != Status.IN_PROGRESS.value:
            self.related_season.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [self.related_season],
                Season,
                fields=["status"],
            )

        if season_just_completed:
            self.related_season.related_tv._handle_completed_season(season_number)
        elif (
            not is_finale
            and self.related_season.related_tv.status != Status.IN_PROGRESS.value
        ):
            self.related_season.related_tv.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [self.related_season.related_tv],
                TV,
                fields=["status"],
            )

    def delete(self, *args, **kwargs):
        """Delete the episode instance and update parent statuses if needed."""
        season = self.related_season
        tv = season.related_tv
        deleted_episode_number = self.item.episode_number

        super().delete(*args, **kwargs)

        self._update_parent_statuses_after_delete(season, tv, deleted_episode_number)

    def _update_parent_statuses_after_delete(self, season, tv, deleted_episode_number):
        """Move completed parents back to in progress after unwatching progress."""
        season.refresh_from_db()
        tv.refresh_from_db()

        if (
            season.status == Status.COMPLETED.value
            and season.progress < deleted_episode_number
        ):
            season.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [season],
                Season,
                fields=["status"],
                default_user=season.user,
            )

        if (
            season.status != Status.COMPLETED.value
            and tv.status == Status.COMPLETED.value
        ):
            tv.status = Status.IN_PROGRESS.value
            bulk_update_with_history(
                [tv],
                TV,
                fields=["status"],
                default_user=season.user,
            )


class Manga(Media):
    """Model for manga."""

    tracker = FieldTracker()


class Anime(Media):
    """Model for anime."""

    tracker = FieldTracker()


class Movie(Media):
    """Model for movies."""

    liked = models.BooleanField(default=False)

    tracker = FieldTracker()


class Game(Media):
    """Model for games."""

    tracker = FieldTracker()

    @property
    def formatted_progress(self):
        """Return progress in hours:minutes format."""
        return app.helpers.minutes_to_hhmm(self.progress)

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == Status.COMPLETED.value:
                # Ensure end_date is set when completing
                if not self.end_date:
                    self.end_date = timezone.now()
                    self.save(update_fields=["end_date"])

            elif self.status == Status.IN_PROGRESS.value:
                # Set start_date if not already set
                if not self.start_date:
                    self.start_date = timezone.now()
                    self.save(update_fields=["start_date"])

            elif self.status == Status.DROPPED.value:
                # Clear end_date if set (game was dropped, not completed)
                if self.end_date:
                    self.end_date = None
                    self.save(update_fields=["end_date"])

            self.item.fetch_releases(delay=True)


class Book(Media):
    """Model for books."""

    tracker = FieldTracker()
    completion_diary_entry = models.OneToOneField(
        'DiaryEntry',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='completed_book',
    )
    completed_manually = models.BooleanField(default=False)

    class Meta:
        """Meta options for the model."""

        ordering = ["user", "item"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "item"],
                name="%(app_label)s_%(class)s_unique_item_user",
            ),
        ]

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == Status.COMPLETED.value:
                self._completed()

            elif self.status == Status.DROPPED.value:
                self._mark_in_progress_sessions_as_dropped()

            elif (
                self.status == Status.IN_PROGRESS.value
                and not self.reading_sessions.filter(status=Status.IN_PROGRESS.value).exists()
            ):
                self._start_reading()

            self.item.fetch_releases(delay=True)

    def _invalidate_progress_cache(self):
        """Clear any cached progress snapshot."""
        if hasattr(self, "_progress_snapshot"):
            delattr(self, "_progress_snapshot")

    def _completed(self):
        """Mark the book as completed and create a final reading session."""
        total_pages = self.get_max_progress()
        if not self.reading_sessions.filter(status=Status.COMPLETED.value).exists():
            session_kwargs = {
                "related_book": self,
                "status": Status.COMPLETED.value,
                "end_date": timezone.now(),
            }
            if total_pages:
                session_kwargs.update(
                    {
                        "pages_read": total_pages,
                        "percentage_read": 100,
                    }
                )
            # Create a final reading session if none exists
            BookSession.objects.create(**session_kwargs)
        elif total_pages:
            self.reading_sessions.filter(
                status=Status.COMPLETED.value,
                pages_read__isnull=True,
            ).update(pages_read=total_pages, percentage_read=100)
        if total_pages:
            self.__class__.objects.filter(pk=self.pk).update(progress=total_pages)
            self.progress = total_pages
        self._invalidate_progress_cache()

    def _mark_in_progress_sessions_as_dropped(self):
        """Mark all in-progress reading sessions as dropped."""
        self.reading_sessions.filter(status=Status.IN_PROGRESS.value).update(
            status=Status.DROPPED.value
        )
        self._invalidate_progress_cache()

    def _start_reading(self):
        """Start reading the book by creating a reading session."""
        if not self.reading_sessions.filter(status=Status.IN_PROGRESS.value).exists():
            BookSession.objects.create(
                related_book=self,
                status=Status.IN_PROGRESS.value,
            )
        self._invalidate_progress_cache()

    def log_reading_session(self, progress_type, progress_value, notes=""):
        """Log a reading session with progress."""
        total_pages = getattr(self.item, "total_pages", None)
        pages_read = None
        percentage = None

        if progress_type == "percentage":
            # Convert percentage to pages if total pages available
            percentage = float(progress_value)
            if total_pages:
                pages_read = int(round((percentage / 100) * total_pages))
        else:  # pages
            pages_read = progress_value
            if total_pages:
                percentage = (progress_value / total_pages) * 100

        if percentage is not None:
            percentage = max(0.0, min(float(percentage), 100.0))
        if pages_read is not None and total_pages:
            pages_read = min(pages_read, total_pages)

        # Create or update reading session
        session, created = BookSession.objects.get_or_create(
            related_book=self,
            status=Status.IN_PROGRESS.value,
            defaults={
                'pages_read': pages_read,
                'percentage_read': percentage,
                'notes': notes,
            }
        )

        if not created:
            session.pages_read = pages_read
            session.percentage_read = percentage
            session.notes = notes
            session.save()

        if pages_read is not None and self.progress != pages_read:
            self.progress = pages_read
            self.save(update_fields=["progress"])

        self._invalidate_progress_cache()
        return session

    def get_max_progress(self):
        """Return total pages for the book."""
        return self.item.total_pages

    def _get_progress_snapshot(self):
        """Return the latest reading progress snapshot."""
        if hasattr(self, "_progress_snapshot"):
            return self._progress_snapshot

        session = (
            self.reading_sessions.filter(status=Status.IN_PROGRESS.value)
            .order_by("-created_at")
            .first()
        )

        if session is None:
            session = (
                self.reading_sessions.filter(status=Status.COMPLETED.value)
                .order_by("-created_at")
                .first()
            )

        if session is None:
            session = self.reading_sessions.order_by("-created_at").first()

        if session is None:
            snapshot = None
        else:
            total_pages = self.item.total_pages or None
            pages = session.pages_read
            percentage = session.percentage_read

            if percentage is not None:
                percentage = float(percentage)
                percentage = max(0.0, min(percentage, 100.0))

            if pages is None and percentage is not None and total_pages:
                pages = int(round((percentage / 100) * total_pages))

            stored_progress = self.progress or 0
            if stored_progress and (pages is None or stored_progress > pages):
                pages = stored_progress
                if total_pages:
                    percentage = (pages / total_pages) * 100
                    percentage = max(0.0, min(percentage, 100.0))

            if percentage is None and pages is not None and total_pages:
                percentage = (pages / total_pages) * 100
                percentage = max(0.0, min(percentage, 100.0))

            snapshot = SimpleNamespace(
                session=session,
                pages=pages,
                total_pages=total_pages,
                percentage=percentage,
                has_pages=pages is not None,
                has_percentage=percentage is not None,
            )

        self._progress_snapshot = snapshot
        return snapshot

    @property
    def progress_snapshot(self):
        """Cached snapshot of the latest reading progress."""
        return self._get_progress_snapshot()

    @property
    def has_progress(self):
        """Return whether any progress data is available."""
        snapshot = self.progress_snapshot
        return bool(snapshot and (snapshot.has_pages or snapshot.has_percentage))


class BookSession(models.Model):
    """Model for reading sessions of a book."""

    history = HistoricalRecords(
        cascade_delete_history=True,
        excluded_fields=["related_book", "created_at"],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    related_book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="reading_sessions",
    )
    pages_read = models.PositiveIntegerField(null=True, blank=True)
    percentage_read = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(100),
        ],
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IN_PROGRESS.value,
    )
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        """Meta options for the model."""

        ordering = [
            "related_book",
            "-end_date",
            "-created_at",
        ]

    def __str__(self):
        """Return the book title and session info."""
        return f"{self.related_book.item.title} - Session {self.id}"

    def save(self, *args, **kwargs):
        """Save the reading session instance."""
        self.related_book._invalidate_progress_cache()
        super().save(*args, **kwargs)

        # Update book status based on session status
        if self.status == Status.COMPLETED.value:
            self.related_book.status = Status.COMPLETED.value
            self.related_book.end_date = self.end_date or timezone.now()
            self.related_book.save()
        elif self.status == Status.IN_PROGRESS.value:
            if self.related_book.status != Status.IN_PROGRESS.value:
                self.related_book.status = Status.IN_PROGRESS.value
                if not self.related_book.start_date:
                    self.related_book.start_date = self.start_date or timezone.now()
                self.related_book.save()
        self.related_book._invalidate_progress_cache()


class Comic(Media):
    """Model for comics."""

    tracker = FieldTracker()


class CustomPosterPreference(models.Model):
    """Model to store user's custom poster preferences for media items."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    custom_image_url = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for the model."""
        constraints = [
            UniqueConstraint(
                fields=["user", "item"],
                name="unique_user_item_poster"
            )
        ]
        ordering = ["-updated_at"]

    def __str__(self):
        """Return string representation."""
        return f"{self.user.username}'s custom poster for {self.item.title}"


class CustomBackdropPreference(models.Model):
    """Model to store user's custom backdrop preferences for media items."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    custom_image_url = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for the model."""
        constraints = [
            UniqueConstraint(
                fields=["user", "item"],
                name="unique_user_item_backdrop",
            )
        ]
        ordering = ["-updated_at"]

    def __str__(self):
        """Return string representation."""
        return f"{self.user.username}'s custom backdrop for {self.item.title}"


class DiaryEntry(models.Model):
    """Model to store diary entries for movie consumption."""

    history = HistoricalRecords(
        cascade_delete_history=True,
        excluded_fields=[
            "item",
            "user",
            "created_at",
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        limit_choices_to={
            "media_type__in": [
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.SEASON.value,
                MediaTypes.EPISODE.value,
                MediaTypes.ANIME.value,
                MediaTypes.MANGA.value,
                MediaTypes.BOOK.value,
                MediaTypes.GAME.value,
                MediaTypes.COMIC.value,
            ]
        },
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    consumed_at = models.DateTimeField()
    rating = models.DecimalField(
        null=True,
        blank=True,
        max_digits=3,
        decimal_places=1,
        validators=[
            DecimalValidator(3, 1),
            MinValueValidator(0),
            MaxValueValidator(10),
        ],
    )
    review = models.TextField(blank=True, default="")
    visibility = models.CharField(
        max_length=20,
        choices=[
            ("public", "Public"),
            ("followers", "Followers"),
            ("private", "Private"),
        ],
        default="public",
    )
    contains_spoilers = models.BooleanField(default=False)
    review_title = models.CharField(max_length=255, blank=True, default="")
    progress_snapshot = models.JSONField(null=True, blank=True)
    liked = models.BooleanField(default=False)
    is_rewatch = models.BooleanField(default=False)
    tags = models.ManyToManyField('Tag', through='DiaryEntryTag', blank=True, related_name='diary_entries')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for the model."""
        indexes = [
            models.Index(fields=["user", "-consumed_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        """Return string representation of the diary entry."""
        return f"{self.user.username}'s entry for {self.item} on {self.consumed_at}"

    def clean(self):
        """Validate that the item is a tracked media type."""
        if self.item.media_type not in [
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.EPISODE.value,
            MediaTypes.ANIME.value,
            MediaTypes.MANGA.value,
            MediaTypes.BOOK.value,
            MediaTypes.GAME.value,
            MediaTypes.COMIC.value,
        ]:
            raise ValidationError(
                "Diary entries can only be created for tracked media types."
            )
        super().clean()

    def save(self, *args, **kwargs):
        """Save the diary entry."""
        self.clean()
        super().save(*args, **kwargs)

    @property
    def rewatch_count(self):
        """Return number of times this movie has been logged before this entry."""
        return DiaryEntry.objects.filter(
            user=self.user,
            item=self.item,
            consumed_at__lt=self.consumed_at
        ).count()


class Tag(models.Model):
    """Model to store tags that can be attached to diary entries."""
    
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    usage_count = models.PositiveIntegerField(default=0)
    
    class Meta:
        """Meta options for the model."""
        ordering = ["-usage_count", "name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["-usage_count"]),
        ]
    
    def __str__(self):
        """Return string representation of the tag."""
        return self.name
    
    def save(self, *args, **kwargs):
        """Save the tag with normalized name."""
        self.name = self.name.lower().strip()
        super().save(*args, **kwargs)
    
    def increment_usage(self):
        """Increment the usage count for this tag."""
        self.usage_count += 1
        self.save(update_fields=['usage_count'])
    
    def decrement_usage(self):
        """Decrement the usage count for this tag."""
        if self.usage_count > 0:
            self.usage_count -= 1
            self.save(update_fields=['usage_count'])


class DiaryEntryTag(models.Model):
    """Model to store the many-to-many relationship between diary entries and tags."""
    
    diary_entry = models.ForeignKey(DiaryEntry, on_delete=models.CASCADE, related_name='diary_entry_tags')
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name='diary_entry_tags')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        """Meta options for the model."""
        constraints = [
            UniqueConstraint(
                fields=["diary_entry", "tag"],
                name="unique_diary_entry_tag"
            )
        ]
        indexes = [
            models.Index(fields=["diary_entry"]),
            models.Index(fields=["tag"]),
        ]
        ordering = ["created_at"]
    
    def __str__(self):
        """Return string representation of the diary entry tag."""
        return f"{self.diary_entry} - {self.tag}"
    
    def save(self, *args, **kwargs):
        """Save the diary entry tag and update tag usage count."""
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self.tag.increment_usage()
    
    def delete(self, *args, **kwargs):
        """Delete the diary entry tag and update tag usage count."""
        tag = self.tag
        super().delete(*args, **kwargs)
        tag.decrement_usage()
