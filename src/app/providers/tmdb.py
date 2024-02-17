from __future__ import annotations

from django.conf import settings

from app.providers import services


def search(media_type: str, query: str) -> list:
    """Search for media on TMDB."""

    url = f"https://api.themoviedb.org/3/search/{media_type}?api_key={settings.TMDB_API}&query={query}"
    response = services.api_request(url, "GET")

    response = response["results"]
    return [
        {
            "media_id": media["id"],
            "media_type": media_type,
            "original_type": get_type_capitalize(media_type),
            "title": get_title(media),
            "image": get_image_url(media["poster_path"]),
        }
        for media in response
    ]


def movie(media_id: str) -> dict:
    """Return the metadata for the selected movie from The Movie Database."""

    url = f"https://api.themoviedb.org/3/movie/{media_id}?api_key={settings.TMDB_API}&append_to_response=recommendations"
    response = services.api_request(url, "GET")

    return {
        "media_id": media_id,
        "media_type": "movie",
        "title": response["title"],
        "image": get_image_url(response["poster_path"]),
        "details": {
            "original_type": "Movie",
            "start_date": get_start_date(response["release_date"]),
            "status": response["status"],
            "synopsis": get_synopsis(response["overview"]),
            "number_of_episodes": 1,
            "runtime": get_readable_duration(response["runtime"]),
            "genres": get_genres(response["genres"]),
        },
        "related": {
            "recommendations": get_related(
                response["recommendations"]["results"][:15],
            ),
        },
    }


def tv_with_seasons(media_id: str, season_numbers: list[int]) -> dict:
    """Return the metadata for the tv show with a season appended to the response."""

    append_text = ",".join([f"season/{season}" for season in season_numbers])

    url = f"https://api.themoviedb.org/3/tv/{media_id}?api_key={settings.TMDB_API}&append_to_response=recommendations,{append_text}"
    response = services.api_request(url, "GET")
    return process_tv(response, season_numbers)


def tv(media_id: str) -> dict:
    """Return the metadata for the selected tv show from The Movie Database."""

    url = f"https://api.themoviedb.org/3/tv/{media_id}?api_key={settings.TMDB_API}&append_to_response=recommendations"
    response = services.api_request(url, "GET")
    return process_tv(response)


def process_tv(response: dict, season_numbers: list[int] | None = None) -> dict:
    """Process the metadata for the selected tv show from The Movie Database."""

    metadata = {
        "media_id": response["id"],
        "media_type": "tv",
        "title": response["name"],
        "image": get_image_url(response["poster_path"]),
        "details": {
            "original_type": "TV",
            "start_date": get_start_date(response["first_air_date"]),
            "end_date": get_start_date(response["last_air_date"]),
            "status": response.get("status", "Unknown"),
            "synopsis": get_synopsis(response["overview"]),
            "number_of_seasons": response.get("number_of_seasons", 1),
            "number_of_episodes": response.get("number_of_episodes", 1),
            "runtime": get_runtime_tv(response["episode_run_time"]),
            "genres": get_genres(response["genres"]),
        },
        "related": {
            "seasons": get_related(response["seasons"], response["id"]),
            "recommendations": get_related(
                response["recommendations"]["results"][:15],
            ),
        },
    }

    # add processed seasons to metadata
    if season_numbers:
        for season_number in season_numbers:
            metadata[f"season/{season_number}"] = process_season(
                response[f"season/{season_number}"],
            )

    return metadata


def season(tv_id: str, season_number: int) -> dict:
    """Return the metadata for the selected season from The Movie Database."""

    url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season_number}?api_key={settings.TMDB_API}"
    response = services.api_request(url, "GET")

    return process_season(response)


def process_season(response: dict) -> dict:
    """Process the metadata for the selected season from The Movie Database."""

    return {
        "title": response["name"],
        "image": get_image_url(response["poster_path"]),
        "season_number": response["season_number"],
        "details": {
            "start_date": get_start_date(response["air_date"]),
            "synopsis": get_synopsis(response["overview"]),
            "number_of_episodes": len(response["episodes"]),
        },
        "episodes": response["episodes"],
    }


def get_type_capitalize(media_type: str) -> dict:
    """Return media_type capitalized."""

    title = media_type.capitalize()

    if media_type == "tv":
        title = "TV"
    elif media_type == "movie":
        title = "Movie"

    return title


def get_image_url(path: str) -> dict:
    """Return the image URL for the media."""

    # path can be null
    if path:
        return f"https://image.tmdb.org/t/p/w500{path}"
    return settings.IMG_NONE


def get_title(response: dict) -> dict:
    """Return the title for the media."""

    # tv shows have name instead of title
    if "title" in response:
        return response["title"]
    return response["name"]


def get_start_date(date: str) -> str:
    """Return the start date for the media."""

    if date == "":
        return "Unknown"
    return date


def get_synopsis(text: str) -> str:
    """Return the synopsis for the media."""

    if text == "":
        return "No synopsis available."
    return text


def get_runtime_tv(runtime: list) -> str:
    """Return the runtime for the tv show."""

    # runtime can be empty list
    if runtime:
        return get_readable_duration(runtime[0])
    return "Unknown"


def get_readable_duration(duration: int) -> str:
    """Convert duration in minutes to a readable format."""

    # duration can be null
    if duration:
        hours, minutes = divmod(int(duration), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return "Unknown"


def get_genres(genres: list) -> dict:
    """Return the genres for the media."""

    # if genres not empty list
    if genres:
        return ", ".join(genre["name"] for genre in genres)

    return "Unknown"


def get_related(related_medias: list, media_id: int | None = None) -> dict:
    """Return list of related media for the selected media."""

    return [
        {  # seasons from tv passes media_id
            "media_id": media_id if media_id else media["id"],
            "title": get_title(media),
            "image": get_image_url(media["poster_path"]),
            "season_number": (
                media["season_number"] if "season_number" in media else None
            ),
        }
        for media in related_medias
    ]


def process_episodes(season_metadata: dict, watched_episodes: dict) -> list:
    """Process the episodes for the selected season."""

    episodes_metadata = []

    for episode in season_metadata["episodes"]:
        episode_number = episode["episode_number"]
        watched = episode_number in watched_episodes

        episodes_metadata.append(
            {
                "episode_number": episode_number,
                "start_date": get_episode_air_date(episode["air_date"]),
                "image": episode["still_path"],
                "title": episode["name"],
                "overview": episode["overview"],
                "watched": watched,
                "watch_date": (
                    watched_episodes.get(episode_number) if watched else None
                ),
            },
        )

    return episodes_metadata


def get_episode_air_date(date: str) -> str:
    """Return the air date for the episode."""

    # can be null
    if not date:
        return "Unknown air date"
    return date
