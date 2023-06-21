from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db.models import Min, Max
from django.core.validators import MinValueValidator, MaxValueValidator, DecimalValidator


class Media(models.Model):
    media_id = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    image = models.ImageField()
    score = models.DecimalField(null=True, blank=True, max_digits=3, decimal_places=1, validators=[DecimalValidator(3, 1), MinValueValidator(0), MaxValueValidator(10)])
    progress = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, default="Completed", choices=[
        ("Completed", "Completed"),
        ("Watching", "Watching"),
        ("Paused", "Paused"),
        ("Dropped", "Dropped"),
        ("Planning", "Planning"),
    ])
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.title} ({self.media_id})"

    class Meta:
        abstract = True
        ordering = ["-score"]


class TV(Media):
    @property
    def progress(self):
        return sum(season.progress for season in self.seasons.all())

    status = None
    start_date = None
    end_date = None


class Season(Media):
    season_number = models.PositiveIntegerField()

    @property
    def progress(self):
        return sum(episode.watched for episode in self.episodes.all())

    @property
    def start_date(self):
        return self.episode_set.aggregate(start_date=Min('watched_date'))['start_date']

    @property
    def end_date(self):
        return self.episode_set.aggregate(end_date=Max('watched_date'))['end_date']

    def __str__(self):
        return f"{self.title} - S{self.season_number}"


class Episode(models.Model):
    tv_season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="episodes")
    episode_number = models.PositiveIntegerField()
    watched = models.BooleanField(default=False)
    watched_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.tv_season} - {self.title}"


class Manga(Media):
    pass


class Anime(Media):
    pass


class Movie(Media):
    progress = None


class User(AbstractUser):
    last_search_type = models.CharField(max_length=10, default="tv")
