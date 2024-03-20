from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://api.themoviedb.org/3"


def search(media_type, query):
    """Search for media on TMDB."""
    data = cache.get(f"search_{media_type}_{query}")

    if not data:
        url = f"{base_url}/search/{media_type}"
        params = {
            "api_key": settings.TMDB_API,
            "query": query,
        }

        if settings.TMDB_NSFW:
            params["include_adult"] = "true"

        response = services.api_request("GET", url, params=params)

        response = response["results"]
        data = [
            {
                "media_id": media["id"],
                "media_type": media_type,
                "format": get_format(media_type),
                "title": get_title(media),
                "image": get_image_url(media["poster_path"]),
            }
            for media in response
        ]

        cache.set(f"search_{media_type}_{query}", data)

    return data


def movie(media_id):
    """Return the metadata for the selected movie from The Movie Database."""
    data = cache.get(f"movie_{media_id}")

    if not data:
        url = f"{base_url}/movie/{media_id}"
        params = {
            "api_key": settings.TMDB_API,
            "append_to_response": "recommendations",
        }
        response = services.api_request("GET", url, params=params)
        data = {
            "media_id": media_id,
            "media_type": "movie",
            "title": response["title"],
            "image": get_image_url(response["poster_path"]),
            "synopsis": get_synopsis(response["overview"]),
            "details": {
                "format": "Movie",
                "start_date": get_start_date(response["release_date"]),
                "status": response["status"],
                "number_of_episodes": 1,
                "runtime": get_readable_duration(response["runtime"]),
                "genres": get_genres(response["genres"]),
                "studios": get_companies(response["production_companies"]),
                "country": get_country(response["production_countries"]),
                "languages": get_languages(response["spoken_languages"]),
            },
            "related": {
                "recommendations": get_related(
                    response["recommendations"]["results"][:15],
                ),
            },
        }

        cache.set(f"movie_{media_id}", data)

    return data


def tv_with_seasons(media_id, season_numbers):
    """Return the metadata for the tv show with a season appended to the response."""
    url = f"{base_url}/tv/{media_id}"
    append_text = ",".join([f"season/{season}" for season in season_numbers])
    params = {
        "api_key": settings.TMDB_API,
        "append_to_response": f"recommendations,{append_text}",
    }

    requested = False

    data = cache.get(f"tv_{media_id}")
    if not data:
        response = services.api_request("GET", url, params=params)
        requested = True

        data = process_tv(response)
        cache.set(f"tv_{media_id}", data)

    # add seasons metadata to the response
    for season_number in season_numbers:
        season_data = cache.get(f"season_{media_id}_{season_number}")

        if not season_data:
            if not requested:
                response = services.api_request("GET", url, params=params)
                requested = True

            season_data = process_season(
                response[f"season/{season_number}"],
            )
            cache.set(f"season_{media_id}_{season_number}", season_data)

        data[f"season/{season_number}"] = season_data

    return data


def tv(media_id):
    """Return the metadata for the selected tv show from The Movie Database."""
    data = cache.get(f"tv_{media_id}")

    if not data:
        url = f"{base_url}/tv/{media_id}"
        params = {
            "api_key": settings.TMDB_API,
            "append_to_response": "recommendations",
        }
        response = services.api_request("GET", url, params=params)
        data = process_tv(response)
        cache.set(f"tv_{media_id}", data)

    return data


def process_tv(response):
    """Process the metadata for the selected tv show from The Movie Database."""
    return {
        "media_id": response["id"],
        "media_type": "tv",
        "title": response["name"],
        "image": get_image_url(response["poster_path"]),
        "synopsis": get_synopsis(response["overview"]),
        "details": {
            "format": "TV",
            "start_date": get_start_date(response["first_air_date"]),
            "end_date": get_end_date(response["last_air_date"]),
            "status": response["status"],
            "number_of_seasons": response["number_of_seasons"],
            "number_of_episodes": response["number_of_episodes"],
            "runtime": get_runtime_tv(response["episode_run_time"]),
            "genres": get_genres(response["genres"]),
            "studios": get_companies(response["production_companies"]),
            "country": get_country(response["production_countries"]),
            "languages": get_languages(response["spoken_languages"]),
        },
        "related": {
            "seasons": get_related(response["seasons"], response["id"]),
            "recommendations": get_related(
                response["recommendations"]["results"][:15],
            ),
        },
    }


def season(tv_id, season_number):
    """Return the metadata for the selected season from The Movie Database."""
    data = cache.get(f"season_{tv_id}_{season_number}")

    if not data:
        url = f"{base_url}/tv/{tv_id}/season/{season_number}"
        params = {
            "api_key": settings.TMDB_API,
        }
        response = services.api_request("GET", url, params=params)
        data = process_season(response)
        cache.set(f"season_{tv_id}_{season_number}", data)

    return data


def process_season(response):
    """Process the metadata for the selected season from The Movie Database."""
    return {
        "title": response["name"],
        "image": get_image_url(response["poster_path"]),
        "season_number": response["season_number"],
        "synopsis": get_synopsis(response["overview"]),
        "details": {
            "start_date": get_start_date(response["air_date"]),
            "number_of_episodes": len(response["episodes"]),
        },
        "episodes": response["episodes"],
    }


def get_format(media_type):
    """Return media_type capitalized."""
    if media_type == "tv":
        return "TV"
    return "Movie"


def get_image_url(path):
    """Return the image URL for the media."""
    # when no image, value from response is null
    # e.g movie: 445290
    if path:
        return f"https://image.tmdb.org/t/p/w500{path}"
    return settings.IMG_NONE


def get_title(response):
    """Return the title for the media."""
    # tv shows have name instead of title
    if "title" in response:
        return response["title"]
    return response["name"]


def get_start_date(date):
    """Return the start date for the media."""
    # when unknown date, value from response is empty string
    # e.g movie: 445290
    if date == "":
        return "Unknown"
    return date


def get_end_date(date):
    """Return the last date for the media."""
    # when unknown date, value from response is null
    # e.g tv: 87818

    if date:
        return date
    return "Unknown"


def get_synopsis(text):
    """Return the synopsis for the media."""
    # when unknown synopsis, value from response is empty string
    # e.g movie: 445290
    if text == "":
        return "No synopsis available."
    return text


def get_runtime_tv(runtime):
    """Return the runtime for the tv show."""
    # when unknown runtime, value from response is empty list
    # e.g: tv:66672
    if runtime:
        return get_readable_duration(runtime[0])
    return "Unknown"


def get_readable_duration(duration):
    """Convert duration in minutes to a readable format."""
    # if unknown movie runtime, value from response is 0
    # e.g movie: 274613
    if duration:
        hours, minutes = divmod(int(duration), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return "Unknown"


def get_genres(genres):
    """Return the genres for the media."""
    # when unknown genres, value from response is empty list
    # e.g tv: 24795
    if genres:
        return ", ".join(genre["name"] for genre in genres)

    return "Unknown"


def get_country(countries):
    """Return the production country for the media."""
    # when unknown production country, value from response is empty list
    # e.g tv: 24795

    if countries:
        return countries[0]["name"]

    return "Unknown"


def get_languages(languages):
    """Return the languages for the media."""
    # when unknown spoken languages, value from response is empty list
    # e.g tv: 24795

    if languages:
        return ", ".join(language["english_name"] for language in languages)

    return "Unknown"


def get_companies(companies):
    """Return the production companies for the media."""
    # when unknown production companies, value from response is empty list
    # e.g tv: 24795

    if companies:
        return ", ".join(company["name"] for company in companies)

    return "Unknown"


def get_related(related_medias, media_id=None):
    """Return list of related media for the selected media."""
    return [
        {  # seasons from tv passes media_id
            "media_id": media_id if media_id else media["id"],
            "title": get_title(media),
            "image": get_image_url(media["poster_path"]),
            "season_number": (media.get("season_number", None)),
        }
        for media in related_medias
    ]


def process_episodes(season_metadata, watched_episodes):
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


def get_episode_air_date(date):
    """Return the air date for the episode."""
    # when unknown air date, value from response is null
    # e.g tv: 1668, season 0, episode 3
    if not date:
        return "Unknown air date"
    return date
