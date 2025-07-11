import secrets

from django.contrib.auth.models import AbstractUser
from django.db import models
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult

from app.models import Item, MediaTypes, Status
from users import helpers

EXCLUDED_SEARCH_TYPES = [MediaTypes.SEASON.value, MediaTypes.EPISODE.value]

VALID_SEARCH_TYPES = [
    value for value in MediaTypes.values if value not in EXCLUDED_SEARCH_TYPES
]


def generate_token():
    """Generate a user token."""
    return secrets.token_urlsafe(24)


class HomeSortChoices(models.TextChoices):
    """Choices for home page sort options."""

    UPCOMING = "upcoming", "Upcoming"
    COMPLETION = "completion", "Completion"
    EPISODES_LEFT = "episodes_left", "Episodes Left"
    TITLE = "title", "Title"


class MediaSortChoices(models.TextChoices):
    """Choices for media list sort options."""

    SCORE = "score", "Rating"
    TITLE = "title", "Title"
    PROGRESS = "progress", "Progress"
    START_DATE = "start_date", "Start Date"
    END_DATE = "end_date", "End Date"


class MediaStatusChoices(models.TextChoices):
    """Choices for media list status options."""

    ALL = "All", "All"
    COMPLETED = Status.COMPLETED.value, Status.COMPLETED.label
    IN_PROGRESS = Status.IN_PROGRESS.value, Status.IN_PROGRESS.label
    PLANNING = Status.PLANNING.value, Status.PLANNING.label
    PAUSED = Status.PAUSED.value, Status.PAUSED.label
    DROPPED = Status.DROPPED.value, Status.DROPPED.label


class LayoutChoices(models.TextChoices):
    """Choices for media list layout options."""

    GRID = "grid", "Grid"
    TABLE = "table", "Table"


class CalendarLayoutChoices(models.TextChoices):
    """Choices for calendar layout options."""

    GRID = "grid", "Grid"
    LIST = "list", "List"


class ListSortChoices(models.TextChoices):
    """Choices for list sort options."""

    LAST_ITEM_ADDED = "last_item_added", "Last Item Added"
    NAME = "name", "Name"
    ITEMS_COUNT = "items_count", "Items Count"
    NEWEST_FIRST = "newest_first", "Newest First"


class ListDetailSortChoices(models.TextChoices):
    """Choices for list detail sort options."""

    DATE_ADDED = "date_added", "Date Added"
    TITLE = "title", "Title"
    MEDIA_TYPE = "media_type", "Media Type"


class User(AbstractUser):
    """Custom user model."""

    is_demo = models.BooleanField(default=False)

    last_search_type = models.CharField(
        max_length=10,
        default=MediaTypes.TV.value,
        choices=MediaTypes.choices,
    )

    home_sort = models.CharField(
        max_length=20,
        default=HomeSortChoices.UPCOMING,
        choices=HomeSortChoices.choices,
    )

    tv_enabled = models.BooleanField(default=True)
    tv_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )
    tv_sort = models.CharField(
        max_length=20,
        default=MediaSortChoices.SCORE,
        choices=MediaSortChoices.choices,
    )
    tv_status = models.CharField(
        max_length=20,
        default=MediaStatusChoices.ALL,
        choices=MediaStatusChoices.choices,
    )

    season_enabled = models.BooleanField(default=True)
    season_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )
    season_sort = models.CharField(
        max_length=20,
        default=MediaSortChoices.SCORE,
        choices=MediaSortChoices.choices,
    )
    season_status = models.CharField(
        max_length=20,
        default=MediaStatusChoices.ALL,
        choices=MediaStatusChoices.choices,
    )

    movie_enabled = models.BooleanField(default=True)
    movie_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )
    movie_sort = models.CharField(
        max_length=20,
        default=MediaSortChoices.SCORE,
        choices=MediaSortChoices.choices,
    )
    movie_status = models.CharField(
        max_length=20,
        default=MediaStatusChoices.ALL,
        choices=MediaStatusChoices.choices,
    )

    anime_enabled = models.BooleanField(default=True)
    anime_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.TABLE,
        choices=LayoutChoices.choices,
    )
    anime_sort = models.CharField(
        max_length=20,
        default=MediaSortChoices.SCORE,
        choices=MediaSortChoices.choices,
    )
    anime_status = models.CharField(
        max_length=20,
        default=MediaStatusChoices.ALL,
        choices=MediaStatusChoices.choices,
    )

    manga_enabled = models.BooleanField(default=True)
    manga_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.TABLE,
        choices=LayoutChoices.choices,
    )
    manga_sort = models.CharField(
        max_length=20,
        default=MediaSortChoices.SCORE,
        choices=MediaSortChoices.choices,
    )
    manga_status = models.CharField(
        max_length=20,
        default=MediaStatusChoices.ALL,
        choices=MediaStatusChoices.choices,
    )

    game_enabled = models.BooleanField(default=True)
    game_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )
    game_sort = models.CharField(
        max_length=20,
        default=MediaSortChoices.SCORE,
        choices=MediaSortChoices.choices,
    )
    game_status = models.CharField(
        max_length=20,
        default=MediaStatusChoices.ALL,
        choices=MediaStatusChoices.choices,
    )

    book_enabled = models.BooleanField(default=True)
    book_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )
    book_sort = models.CharField(
        max_length=20,
        default=MediaSortChoices.SCORE,
        choices=MediaSortChoices.choices,
    )
    book_status = models.CharField(
        max_length=20,
        default=MediaStatusChoices.ALL,
        choices=MediaStatusChoices.choices,
    )

    comic_enabled = models.BooleanField(default=True)
    comic_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )
    comic_sort = models.CharField(
        max_length=20,
        default=MediaSortChoices.SCORE,
        choices=MediaSortChoices.choices,
    )
    comic_status = models.CharField(
        max_length=20,
        default=MediaStatusChoices.ALL,
        choices=MediaStatusChoices.choices,
    )

    hide_from_search = models.BooleanField(default=True)

    calendar_layout = models.CharField(
        max_length=20,
        default=CalendarLayoutChoices.GRID,
        choices=CalendarLayoutChoices.choices,
    )

    lists_sort = models.CharField(
        max_length=20,
        default=ListSortChoices.LAST_ITEM_ADDED,
        choices=ListSortChoices.choices,
    )

    list_detail_sort = models.CharField(
        max_length=20,
        default=ListDetailSortChoices.DATE_ADDED,
        choices=ListDetailSortChoices.choices,
    )

    notification_urls = models.TextField(
        blank=True,
        help_text="Apprise URLs for notifications",
    )

    notification_excluded_items = models.ManyToManyField(
        Item,
        related_name="excluded_by_users",
        blank=True,
        help_text="Items excluded from notifications",
    )

    release_notifications_enabled = models.BooleanField(
        default=True,
        help_text="Receive notifications for recently released media",
    )

    daily_digest_enabled = models.BooleanField(
        default=True,
        help_text="Receive a daily digest of upcoming releases",
    )

    token = models.CharField(
        max_length=32,
        unique=True,
        default=generate_token,
        help_text="Token for external integrations",
    )

    plex_usernames = models.TextField(
        blank=True,
        help_text="Comma-separated list of Plex usernames for webhook matching",
    )

    class Meta:
        """Meta options for the model."""

        ordering = ["username"]
        constraints = [
            models.CheckConstraint(
                name="last_search_type_valid",
                condition=models.Q(last_search_type__in=VALID_SEARCH_TYPES),
            ),
            models.CheckConstraint(
                name="home_sort_valid",
                condition=models.Q(home_sort__in=HomeSortChoices.values),
            ),
            models.CheckConstraint(
                name="tv_layout_valid",
                condition=models.Q(tv_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="season_layout_valid",
                condition=models.Q(season_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="movie_layout_valid",
                condition=models.Q(movie_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="anime_layout_valid",
                condition=models.Q(anime_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="manga_layout_valid",
                condition=models.Q(manga_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="game_layout_valid",
                condition=models.Q(game_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="book_layout_valid",
                condition=models.Q(book_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="tv_sort_valid",
                condition=models.Q(tv_sort__in=MediaSortChoices.values),
            ),
            models.CheckConstraint(
                name="season_sort_valid",
                condition=models.Q(season_sort__in=MediaSortChoices.values),
            ),
            models.CheckConstraint(
                name="movie_sort_valid",
                condition=models.Q(movie_sort__in=MediaSortChoices.values),
            ),
            models.CheckConstraint(
                name="anime_sort_valid",
                condition=models.Q(anime_sort__in=MediaSortChoices.values),
            ),
            models.CheckConstraint(
                name="manga_sort_valid",
                condition=models.Q(manga_sort__in=MediaSortChoices.values),
            ),
            models.CheckConstraint(
                name="game_sort_valid",
                condition=models.Q(game_sort__in=MediaSortChoices.values),
            ),
            models.CheckConstraint(
                name="book_sort_valid",
                condition=models.Q(book_sort__in=MediaSortChoices.values),
            ),
            models.CheckConstraint(
                name="calendar_layout_valid",
                condition=models.Q(calendar_layout__in=CalendarLayoutChoices.values),
            ),
            models.CheckConstraint(
                name="lists_sort_valid",
                condition=models.Q(lists_sort__in=ListSortChoices.values),
            ),
            models.CheckConstraint(
                name="list_detail_sort_valid",
                condition=models.Q(list_detail_sort__in=ListDetailSortChoices.values),
            ),
            models.CheckConstraint(
                name="tv_status_valid",
                condition=models.Q(tv_status__in=MediaStatusChoices.values),
            ),
            models.CheckConstraint(
                name="season_status_valid",
                condition=models.Q(season_status__in=MediaStatusChoices.values),
            ),
            models.CheckConstraint(
                name="movie_status_valid",
                condition=models.Q(movie_status__in=MediaStatusChoices.values),
            ),
            models.CheckConstraint(
                name="anime_status_valid",
                condition=models.Q(anime_status__in=MediaStatusChoices.values),
            ),
            models.CheckConstraint(
                name="manga_status_valid",
                condition=models.Q(manga_status__in=MediaStatusChoices.values),
            ),
            models.CheckConstraint(
                name="game_status_valid",
                condition=models.Q(game_status__in=MediaStatusChoices.values),
            ),
            models.CheckConstraint(
                name="book_status_valid",
                condition=models.Q(book_status__in=MediaStatusChoices.values),
            ),
        ]

    def update_preference(self, field_name, new_value):
        """
        Update user preference if the new value is valid and different from current.

        Args:
            field_name: The name of the field to update
            new_value: The new value to set

        Returns:
            The value that was set (or the original value if invalid)
        """
        # If no new value provided, return current value
        if new_value is None:
            return getattr(self, field_name)

        # Special case for last_search_type
        if field_name == "last_search_type" and new_value not in VALID_SEARCH_TYPES:
            return getattr(self, field_name)

        field = self._meta.get_field(field_name)
        # Check if the field has choices
        if hasattr(field, "choices") and field.choices:
            # Get valid values from field choices
            valid_values = [choice[0] for choice in field.choices]

            # If the new value is not valid, return current value
            if new_value not in valid_values:
                return getattr(self, field_name)

        # Get current value
        current_value = getattr(self, field_name)

        # Update if different
        if new_value != current_value:
            setattr(self, field_name, new_value)
            self.save(update_fields=[field_name])

        return new_value

    def get_enabled_media_types(self):
        """Return a list of enabled media type values based on user preferences."""
        enabled_types = []

        for media_type in MediaTypes.values:
            if media_type == MediaTypes.EPISODE.value:
                continue

            enabled_field = f"{media_type}_enabled"
            if getattr(self, enabled_field, False):
                enabled_types.append(media_type)

        return enabled_types

    def get_active_media_types(self):
        """Return a list of active media type values based on user preferences."""
        enabled_types = self.get_enabled_media_types()

        # Add season if TV is enabled (and season isn't already in the list)
        if (
            MediaTypes.TV.value in enabled_types
            and MediaTypes.SEASON.value not in enabled_types
        ):
            enabled_types.insert(0, MediaTypes.SEASON.value)

        return enabled_types

    def get_import_tasks(self):
        """Return import tasks history and schedules for the user."""
        import_tasks = {
            "trakt": "Import from Trakt",
            "simkl": "Import from SIMKL",
            "myanimelist": "Import from MyAnimeList",
            "anilist": "Import from AniList",
            "kitsu": "Import from Kitsu",
            "yamtrack": "Import from Yamtrack",
            "hltb": "Import from HowLongToBeat",
            "imdb": "Import from IMDB",
        }

        # Reverse mapping to get source from task name
        task_to_source = {v: k for k, v in import_tasks.items()}

        task_result_filter_text = f"'user_id': {self.id},"

        # Get all task results for this user
        task_results = TaskResult.objects.filter(
            task_kwargs__contains=task_result_filter_text,
            task_name__in=import_tasks.values(),
        ).order_by("-date_done")  # Most recent first

        # Build results list
        results = []
        for task in task_results:
            source = task_to_source[task.task_name]
            processed_task = helpers.process_task_result(task)
            results.append(
                {
                    "task": processed_task,
                    "source": source,
                    "date": task.date_done,
                    "status": task.status,
                    "summary": processed_task.summary,
                    "errors": processed_task.errors,
                },
            )

        # Get periodic tasks with their crontab schedules
        periodic_tasks_filter_text = f'"user_id": {self.id},'
        periodic_tasks = PeriodicTask.objects.filter(
            task__in=import_tasks.values(),
            kwargs__contains=periodic_tasks_filter_text,
            enabled=True,
        ).select_related("crontab")

        # Build schedules list
        schedules = []
        for periodic_task in periodic_tasks:
            source = task_to_source.get(periodic_task.task, "unknown")

            # Extract username from task name if available
            username = ""
            if " for " in periodic_task.name:
                username = periodic_task.name.split(" for ")[1].split(" at ")[0]

            schedule_info = helpers.get_next_run_info(periodic_task)
            if schedule_info:
                schedules.append(
                    {
                        "task": periodic_task,
                        "source": source,
                        "username": username,
                        "last_run": periodic_task.last_run_at,
                        "next_run": schedule_info["next_run"],
                        "schedule": schedule_info["frequency"],
                        "mode": schedule_info["mode"],
                    },
                )

        return {
            "results": results,
            "schedules": schedules,
        }

    def regenerate_token(self):
        """Regenerate the user's token."""
        self.token = generate_token()
        self.save(update_fields=["token"])
