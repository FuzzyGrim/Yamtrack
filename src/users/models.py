from django.contrib.auth.models import AbstractUser
from django.db import models

layouts = [
    ("app/media_grid.html", "grid"),
    ("app/media_table.html", "table"),
]


class User(AbstractUser):
    """Custom user model that saves the last media search type."""

    editable = models.BooleanField(default=True)

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

    tv_layout = models.CharField(
        max_length=20,
        default="app/media_grid.html",
        choices=layouts,
    )

    season_layout = models.CharField(
        max_length=20,
        default="app/media_grid.html",
        choices=layouts,
    )

    movie_layout = models.CharField(
        max_length=20,
        default="app/media_grid.html",
        choices=layouts,
    )

    anime_layout = models.CharField(
        max_length=20,
        default="app/media_table.html",
        choices=layouts,
    )

    manga_layout = models.CharField(
        max_length=20,
        default="app/media_grid.html",
        choices=layouts,
    )

    def get_layout(self: "User", media_type: str) -> str:
        """Return the layout for the media type."""
        layout = {
            "tv": self.tv_layout,
            "season": self.season_layout,
            "movie": self.movie_layout,
            "anime": self.anime_layout,
            "manga": self.manga_layout,
        }
        return layout[media_type]

    def set_layout(self: "User", media_type: str, layout: str) -> None:
        """Set the layout for the media type."""
        if media_type == "tv":
            self.tv_layout = layout
        elif media_type == "season":
            self.season_layout = layout
        elif media_type == "movie":
            self.movie_layout = layout
        elif media_type == "anime":
            self.anime_layout = layout
        elif media_type == "manga":
            self.manga_layout = layout
        self.save(update_fields=[f"{media_type}_layout"])
