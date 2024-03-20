import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://api.myanimelist.net/v2"
base_fields = "title,main_picture,media_type,start_date,end_date,synopsis,status,genres,recommendations"  # noqa: E501


def search(media_type, query):
    """Search for media on MyAnimeList."""
    data = cache.get(f"search_{media_type}_{query}")

    if not data:
        url = f"{base_url}/{media_type}"
        params = {
            "q": query,
            "fields": "media_type",
        }
        if settings.MAL_NSFW:
            params["nsfw"] = "true"

        try:
            response = services.api_request(
                "GET",
                url,
                params=params,
                headers={"X-MAL-CLIENT-ID": settings.MAL_API},
            )
        except requests.exceptions.HTTPError as error:
            # if the query is invalid, return an empty list
            if error.response.json().get("message") == "invalid q":
                return []

        response = response["data"]
        data = [
            {
                "media_id": media["node"]["id"],
                "media_type": media_type,
                "format": get_format(media["node"]),
                "title": media["node"]["title"],
                "image": get_image_url(media["node"]),
            }
            for media in response
        ]

        cache.set(f"search_{media_type}_{query}", data)

    return data


def anime(media_id):
    """Return the metadata for the selected anime or manga from MyAnimeList."""
    data = cache.get(f"anime_{media_id}")

    if not data:
        url = f"{base_url}/anime/{media_id}"
        params = {
            "fields": f"{base_fields},num_episodes,average_episode_duration,studios,start_season,source,related_anime",  # noqa: E501
        }
        response = services.api_request(
            "GET",
            url,
            params=params,
            headers={"X-MAL-CLIENT-ID": settings.MAL_API},
        )

        data = {
            "media_id": media_id,
            "media_type": "anime",
            "title": response["title"],
            "image": get_image_url(response),
            "synopsis": get_synopsis(response),
            "details": {
                "format": get_format(response),
                "start_date": response.get("start_date", "Unknown"),
                "end_date": response.get("end_date", "Unknown"),
                "status": get_readable_status(response),
                "number_of_episodes": get_number_of_episodes(response),
                "runtime": get_runtime(response),
                "studios": get_studios(response),
                "season": get_season(response),
                "source": get_source(response),
                "genres": get_genres(response),
            },
            "related": {
                "related_animes": get_related(response.get("related_anime")),
                "recommendations": get_related(response.get("recommendations")),
            },
        }

        cache.set(f"anime_{media_id}", data)

    return data


def manga(media_id):
    """Return the metadata for the selected anime or manga from MyAnimeList."""
    data = cache.get(f"manga_{media_id}")

    if not data:
        url = f"{base_url}/manga/{media_id}"
        params = {
            "fields": f"{base_fields},num_chapters,related_manga,recommendations",
        }
        response = services.api_request(
            "GET",
            url,
            params=params,
            headers={"X-MAL-CLIENT-ID": settings.MAL_API},
        )

        data = {
            "media_id": media_id,
            "media_type": "manga",
            "title": response["title"],
            "image": get_image_url(response),
            "synopsis": get_synopsis(response),
            "details": {
                "format": get_format(response),
                "start_date": response.get("start_date", "Unknown"),
                "end_date": response.get("end_date", "Unknown"),
                "status": get_readable_status(response),
                "number_of_episodes": get_number_of_episodes(response),
                "genres": get_genres(response),
            },
            "related": {
                "related_mangas": get_related(response.get("related_manga")),
                "recommendations": get_related(response.get("recommendations")),
            },
        }

        cache.set(f"manga_{media_id}", data)

    return data


def get_format(response):
    """Return the original type of the media."""
    media_format = response["media_type"]

    # MAL return tv in metadata for anime
    if media_format == "tv":
        return "Anime"
    if media_format in ("ova", "ona"):
        return media_format.upper()
    return media_format.replace("_", " ").title()


def get_image_url(response):
    """Return the image URL for the media."""
    # when no picture, main_picture is not present in the response
    # e.g anime: 38869
    if "main_picture" in response:
        return response["main_picture"]["large"]
    return settings.IMG_NONE


def get_readable_status(response):
    """Return the status in human-readable format."""
    # Map status to human-readable values
    status_map = {
        "finished_airing": "Finished",
        "currently_airing": "Airing",
        "not_yet_aired": "Upcoming",
        "finished": "Finished",
        "currently_publishing": "Publishing",
        "not_yet_published": "Upcoming",
        "on_hiatus": "On Hiatus",
    }
    return status_map[(response["status"])]


def get_synopsis(response):
    """Add the synopsis to the response."""
    # when no synopsis, value from response is empty string
    # e.g manga: 160219
    if response["synopsis"] == "":
        return "No synopsis available."

    return response["synopsis"]


def get_number_of_episodes(response):
    """Return the number of episodes for the media."""
    # when unknown episodes, value from response is 0
    # e.g manga: 160219

    # anime
    if "num_episodes" in response:
        episodes = response["num_episodes"]
    # manga
    elif "num_chapters" in response:
        episodes = response["num_chapters"]

    if episodes == 0:
        return "Unknown"
    return episodes


def get_runtime(response):
    """Return the average episode duration."""
    # when unknown duration, value from response is 0
    # e.g anime: 43333
    duration = response["average_episode_duration"]

    # Convert average_episode_duration to hours and minutes
    if duration:
        # duration are in seconds
        hours, minutes = divmod(int(duration / 60), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return "Unknown"


def get_genres(response):
    """Return the genres for the media."""
    return ", ".join(genre["name"] for genre in response["genres"])


def get_studios(response):
    """Return the studios for the media."""
    # when unknown studio, studios is an empty list
    # e.g anime: 43333

    if response["studios"]:
        return ", ".join(studio["name"] for studio in response["studios"])
    return "Unknown"


def get_season(response):
    """Return the season for the media."""
    # when unknown start season, no start_season key in response
    # e.g anime: 43333

    season = response.get("start_season")

    if season:
        return f"{season['season'].title()} {season['year']}"

    return "Unknown"


def get_source(response):
    """Return the source for the media."""
    return response["source"].replace("_", " ").title()


def get_related(related_medias):
    """Return list of related media for the selected media."""
    if related_medias:
        return [
            {
                "media_id": media["node"]["id"],
                "title": media["node"]["title"],
                "image": get_image_url(media["node"]),
            }
            for media in related_medias
        ]
    return []
