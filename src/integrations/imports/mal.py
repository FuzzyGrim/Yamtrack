import logging

from app.models import Anime, Manga
from app.providers import services
from django.apps import apps
from django.conf import settings

logger = logging.getLogger(__name__)

base_url = "https://api.myanimelist.net/v2/users"


def importer(username, user):
    """Import anime and manga from MyAnimeList."""
    anime_params = {
        "fields": "list_status{comments,num_times_rewatched}",
        "nsfw": "true",
        "limit": 1000,
    }
    anime_url = f"{base_url}/{username}/animelist"
    animes = get_whole_response(anime_url, anime_params)
    bulk_add_anime = add_media_list(animes, "anime", user)

    manga_params = {
        "fields": "list_status{comments,num_times_reread}",
        "nsfw": "true",
        "limit": 1000,
    }
    manga_url = f"{base_url}/{username}/mangalist"
    mangas = get_whole_response(manga_url, manga_params)
    bulk_add_manga = add_media_list(mangas, "manga", user)

    Anime.objects.bulk_create(bulk_add_anime, ignore_conflicts=True)
    Manga.objects.bulk_create(bulk_add_manga, ignore_conflicts=True)


def get_whole_response(url, params):
    """Fetch whole data from user.

    Continues to fetch data from the next URL until there is no more data to fetch.
    """
    headers = {"X-MAL-CLIENT-ID": settings.MAL_API}

    data = services.api_request("MAL", "GET", url, params=params, headers=headers)

    while "next" in data["paging"]:
        next_url = data["paging"]["next"]
        # Fetch the data from the next URL
        next_data = services.api_request(
            "MAL",
            "GET",
            next_url,
            params=params,
            headers=headers,
        )
        # Append the new data to the existing data in the data
        data["data"].extend(next_data["data"])
        # Update the "paging" key with the new "next" URL (if any)
        data["paging"] = next_data["paging"]

    return data


def add_media_list(response, media_type, user):
    """Add media to list for bulk creation."""
    logger.info("Importing %ss from MyAnimeList", media_type)

    bulk_media = []

    for content in response["data"]:
        status = get_status(content["list_status"]["status"])

        if media_type == "anime":
            progress = content["list_status"]["num_episodes_watched"]
            repeats = content["list_status"]["num_times_rewatched"]
            if content["list_status"]["is_rewatching"]:
                status = "Repeating"
        else:
            progress = content["list_status"]["num_chapters_read"]
            repeats = content["list_status"]["num_times_reread"]
            if content["list_status"]["is_rereading"]:
                status = "Repeating"

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
            repeats=repeats,
            start_date=content["list_status"].get("start_date", None),
            end_date=content["list_status"].get("finish_date", None),
            user=user,
            notes=content["list_status"]["comments"],
        )

        bulk_media.append(instance)

    return bulk_media


def get_status(status):
    """Convert the status from MyAnimeList to the status used in the app."""
    switcher = {
        "completed": "Completed",
        "reading": "In progress",
        "watching": "In progress",
        "plan_to_watch": "Planning",
        "plan_to_read": "Planning",
        "on_hold": "Paused",
        "dropped": "Dropped",
    }
    return switcher[status]
