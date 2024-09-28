from django.contrib.auth.models import AbstractUser
from django.db import models

from app.models import MEDIA_TYPES

layouts = [
    ("grid", "Grid"),
    ("table", "Table"),
]


class User(AbstractUser):
    """Custom user model that saves the last media search type."""

    is_demo = models.BooleanField(default=False)

    last_search_type = models.CharField(
        max_length=10,
        default="tv",
        choices=[(media_type, media_type) for media_type in MEDIA_TYPES],
    )

    tv_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    season_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    movie_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    anime_layout = models.CharField(
        max_length=20,
        default="table",
        choices=layouts,
    )

    manga_layout = models.CharField(
        max_length=20,
        default="table",
        choices=layouts,
    )

    game_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    class Meta:
        """Meta options for the model."""

        ordering = ["username"]

    def get_layout(self, media_type):
        """Return the layout for the media type."""
        return getattr(self, f"{media_type}_layout")

    def get_layout_template(self, media_type):
        """Return the layout template for the media type."""
        template = {
            "grid": "app/media_grid.html",
            "table": "app/media_table.html",
        }
        return template[self.get_layout(media_type)]

    def set_layout(self, media_type, layout):
        """Set the layout for the media type."""
        setattr(self, f"{media_type}_layout", layout)
        self.save(update_fields=[f"{media_type}_layout"])

    def set_last_search_type(self, media_type):
        """Set the last search type, used for default search type."""
        self.last_search_type = media_type
        self.save(update_fields=["last_search_type"])
