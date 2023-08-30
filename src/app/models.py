from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import Max, Min


class Media(models.Model):
    media_id = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    image = models.ImageField()
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
        max_length=10,
        default="Completed",
        choices=[
            ("Completed", "Completed"),
            ("Watching", "Watching"),
            ("Paused", "Paused"),
            ("Dropped", "Dropped"),
            ("Planning", "Planning"),
        ],
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.title}"

    class Meta:
        abstract = True
        ordering = ["-score"]
        unique_together = ["media_id", "user"]


class TV(Media):
    progress = None
    status = None
    start_date = None
    end_date = None


class Season(Media):
    season_number = models.PositiveIntegerField()

    @property
    def progress(self):
        return self.episodes.count()

    @property
    def start_date(self):
        return self.episodes.aggregate(start_date=Min("watch_date"))["start_date"]

    @property
    def end_date(self):
        return self.episodes.aggregate(end_date=Max("watch_date"))["end_date"]

    def __str__(self):
        return f"{self.title} S{self.season_number}"

    class Meta:
        unique_together = ["media_id", "season_number", "user"]


class Episode(models.Model):
    related_season = models.ForeignKey(
        Season, on_delete=models.CASCADE, related_name="episodes"
    )
    episode_number = models.PositiveIntegerField()
    watch_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.related_season}E{self.episode_number}"

    class Meta:
        unique_together = ["related_season", "episode_number"]


class Manga(Media):
    pass


class Anime(Media):
    pass


class Movie(Media):
    start_date = None

    @property
    def progress(self):
        if self.status == "Completed":
            return 1
        else:
            return 0
