from django.contrib.auth.models import AbstractUser
from django.db import models


def get_default_layout() -> dict:
    """Return the default layout for each media type."""
    return {
        "tv": "app/media_grid.html",
        "season": "app/media_grid.html",
        "movie": "app/media_grid.html",
        "anime": "app/media_table.html",
        "manga": "app/media_table.html",
    }


class User(AbstractUser):
    """Custom user model that saves the last media search type."""

    last_search_type = models.CharField(
        max_length=10,
        default="tv",
        choices=[
            ("tv", "tv"),
            ("movie", "movie"),
            ("anime", "anime"),
            ("manga", "manga"),
        ],
    )

    default_layout = models.JSONField(default=get_default_layout)
