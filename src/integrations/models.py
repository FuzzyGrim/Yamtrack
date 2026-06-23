from django.db import models
from django.utils import timezone

from app.models import MediaTypes


class LetterboxdUriCache(models.Model):
    """Resolved Letterboxd URI to TMDB movie ID."""

    class Confidence(models.TextChoices):
        """Resolution method."""

        SCRAPE = "scrape", "Scrape"
        IMDB = "imdb", "IMDb"
        SEARCH = "search", "Search"

    uri = models.URLField(unique=True)
    tmdb_id = models.CharField(max_length=36)
    media_type = models.CharField(
        max_length=10,
        choices=MediaTypes,
        default=MediaTypes.MOVIE.value,
    )
    imdb_id = models.CharField(max_length=20, blank=True, null=True)
    resolved_at = models.DateTimeField(default=timezone.now)
    confidence = models.CharField(
        max_length=10,
        choices=Confidence.choices,
        blank=True,
        default="",
    )

    class Meta:
        """Meta options."""

        ordering = ["uri"]

    def __str__(self):
        """Return the cached URI."""
        return self.uri
