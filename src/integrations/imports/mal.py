import logging

from app.models import Anime, Manga
from app.providers import services
from django.apps import apps
from django.conf import settings
from users.models import User

logger = logging.getLogger(__name__)


def importer(username: str, user: User) -> None:
    """Import anime and manga from MyAnimeList."""

    anime_url = f"https://api.myanimelist.net/v2/users/{username}/animelist?fields=list_status{{comments}}&nsfw=true&limit=1000"
    animes = get_whole_response(anime_url)

    bulk_add_anime = add_media_list(animes, "anime", user)

    manga_url = f"https://api.myanimelist.net/v2/users/{username}/mangalist?fields=list_status{{comments}}&nsfw=true&limit=1000"
    mangas = get_whole_response(manga_url)

    bulk_add_manga = add_media_list(mangas, "manga", user)

    Anime.objects.bulk_create(bulk_add_anime)
    Manga.objects.bulk_create(bulk_add_manga)


def get_whole_response(url: str) -> dict:
    """Fetch whole data from user.

    Continues to fetch data from the next URL until there is no more data to fetch.
    """

    header = {"X-MAL-CLIENT-ID": settings.MAL_API}

    data = services.api_request(url, "GET", header)

    while "next" in data["paging"]:
        next_url = data["paging"]["next"]
        # Fetch the data from the next URL
        next_data = services.api_request(next_url, "GET", header)
        # Append the new data to the existing data in the data
        data["data"].extend(next_data["data"])
        # Update the "paging" key with the new "next" URL (if any)
        data["paging"] = next_data["paging"]

    return data


def add_media_list(response: dict, media_type: str, user: User) -> list:
    """Add media to list for bulk creation."""

    logger.info("Importing %ss from MyAnimeList", media_type)

    bulk_media = []

    for content in response["data"]:
        status = get_status(content["list_status"]["status"])

        if media_type == "anime":
            progress = content["list_status"]["num_episodes_watched"]
        else:
            progress = content["list_status"]["num_chapters_read"]

        try:
            image_url = content["node"]["main_picture"]["large"]
        except KeyError:
            image_url = settings.IMG_NONE

        model = apps.get_model(app_label="app", model_name=media_type)

        instance = model(
            media_id=content["node"]["id"],
            title=content["node"]["title"],
            image=image_url,
            score=content["list_status"]["score"],
            progress=progress,
            status=status,
            start_date=content["list_status"].get("start_date", None),
            end_date=content["list_status"].get("finish_date", None),
            user=user,
            notes=content["list_status"]["comments"],
        )

        bulk_media.append(instance)

    return bulk_media


def get_status(status: str) -> str:
    """Convert the status from MyAnimeList to the status used in the app."""

    switcher = {
        "plan_to_watch": "Planning",
        "on_hold": "Paused",
        "reading": "In progress",
        "watching": "In progress",
    }
    return switcher.get(status, status.capitalize())
