import datetime

from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import Max, Min
from model_utils import FieldTracker

from app.utils import metadata


class Media(models.Model):
    """Abstract model for all media types."""

    media_id = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    image = models.URLField()
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
    status = models.CharField(
        max_length=12,
        default="Completed",
        choices=[
            ("Completed", "Completed"),
            ("In progress", "In progress"),
            ("Paused", "Paused"),
            ("Dropped", "Dropped"),
            ("Planning", "Planning"),
        ],
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    notes = models.TextField(blank=True)

    class Meta:
        """Meta options for the model."""

        abstract = True
        ordering = ["-score"]
        unique_together = ["media_id", "user"]

    def __str__(self: "Media") -> str:
        """Return the title of the media."""

        return f"{self.title}"

    def save(self: "Media", *args: dict, **kwargs: dict) -> None:
        """Update some fields before saving the instance."""

        media_type = self.__class__.__name__.lower()

        if media_type != "season":
            if "status" in self.tracker.changed() or self._state.adding:
                if self.status == "Completed":
                    if not self.end_date:
                        self.end_date = datetime.datetime.now(tz=settings.TZ).date()

                    self.progress = metadata.get_media_metadata(
                        media_type,
                        self.media_id,
                    )["num_episodes"]

                elif self.status == "In progress" and not self.start_date:
                    self.start_date = datetime.datetime.now(tz=settings.TZ).date()

            if "progress" in self.tracker.changed():
                max_episodes = metadata.get_media_metadata(
                    media_type,
                    self.media_id,
                )["num_episodes"]

                if self.progress > max_episodes:
                    self.progress = max_episodes

                if self.progress == max_episodes:
                    self.status = "Completed"
                    self.end_date = datetime.datetime.now(
                        tz=settings.TZ,
                    ).date()

        super().save(*args, **kwargs)


class TV(Media):
    """Model for TV shows."""

    tracker = FieldTracker()


class Season(Media):
    """Model for seasons of TV shows."""

    season_number = models.PositiveIntegerField()
    tracker = FieldTracker()

    @property
    def progress(self: "Season") -> int:
        """Return the user's episodes watched for the season."""
        return self.episodes.count()

    @property
    def start_date(self: "Season") -> str:
        """Return the date of the first episode watched."""
        return self.episodes.aggregate(start_date=Min("watch_date"))["start_date"]

    @property
    def end_date(self: "Season") -> str:
        """Return the date of the last episode watched."""
        return self.episodes.aggregate(end_date=Max("watch_date"))["end_date"]

    class Meta:
        """Limit the uniqueness of seasons.

        Only one season per media can have the same season number.
        """

        unique_together = ["media_id", "season_number", "user"]

    def __str__(self: "Season") -> str:
        """Return the title of the media and season number."""
        return f"{self.title} S{self.season_number}"


class Episode(models.Model):
    """Model for episodes of a season."""

    related_season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    episode_number = models.PositiveIntegerField()
    watch_date = models.DateField(null=True, blank=True)
    tracker = FieldTracker()

    class Meta:
        """Limit the uniqueness of episodes.

        Only one episode per season can have the same episode number.
        """

        unique_together = ["related_season", "episode_number"]

    def __str__(self: "Episode") -> str:
        """Return the season and episode number."""
        return f"{self.related_season}E{self.episode_number}"


class Manga(Media):
    """Model for manga."""

    tracker = FieldTracker()


class Anime(Media):
    """Model for anime."""

    tracker = FieldTracker()


class Movie(Media):
    """Model for movies."""

    tracker = FieldTracker()
