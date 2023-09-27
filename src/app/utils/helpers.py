import logging

from django.http import HttpRequest

from app.forms import AnimeForm, MangaForm, MovieForm, SeasonForm, TVForm
from app.models import TV, Anime, Manga, Movie, Season

logger = logging.getLogger(__name__)


def get_client_ip(request: HttpRequest) -> str:
    """Return the client's IP address.

    Used when logging for user registration and login.
    """

    # get the user's IP address
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR")

    # if the IP address is not available in HTTP_X_FORWARDED_FOR
    if not ip_address:
        ip_address = request.META.get("REMOTE_ADDR")

    return ip_address


def media_type_mapper(media_type: str) -> dict:
    """Map the media type to its corresponding model, form and other properties."""

    media_mapping = {
        "manga": {
            "model": Manga,
            "form": MangaForm,
            "list_layout": "app/media_table.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
                ("-progress", "Progress"),
                ("start_date", "Start Date"),
                ("end_date", "End Date"),
            ],
        },
        "anime": {
            "model": Anime,
            "form": AnimeForm,
            "list_layout": "app/media_table.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
                ("-progress", "Progress"),
                ("start_date", "Start Date"),
                ("end_date", "End Date"),
            ],
        },
        "movie": {
            "model": Movie,
            "form": MovieForm,
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
                ("end_date", "End Date"),
            ],
        },
        "tv": {
            "model": TV,
            "form": TVForm,
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
            ],
        },
        "season": {
            "model": Season,
            "form": SeasonForm,
            "list_layout": "app/media_grid.html",
            "sort_choices": [
                ("-score", "Score"),
                ("title", "Title"),
            ],
        },
    }
    return media_mapping[media_type]
