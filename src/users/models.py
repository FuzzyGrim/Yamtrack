from django.contrib.auth.models import AbstractUser
from django.db import models

layouts = [
    ("grid", "Grid"),
    ("table", "Table"),
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

    def get_layout_template(self: "User", media_type: str) -> str:
        """Return the layout template for the media type."""
        template = {
            "grid": "app/media_grid.html",
            "table": "app/media_table.html",
        }
        return template[self.get_layout(media_type)]

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
