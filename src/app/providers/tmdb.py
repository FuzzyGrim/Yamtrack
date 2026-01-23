import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils.text import slugify

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://api.themoviedb.org/3"
base_params = {
    "api_key": settings.TMDB_API,
    "language": settings.TMDB_LANG,
}


def handle_error(error):
    """Handle TMDB API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.TMDB.value, error) from json_error

    # Handle authentication errors
    if status_code == requests.codes.unauthorized:
        details = error_json.get("status_message")
        if details:
            # Remove trailing period if present
            details = details.rstrip(".")
            raise services.ProviderAPIError(Sources.TMDB.value, error, details)

    raise services.ProviderAPIError(
        Sources.TMDB.value,
        error,
    )


def get_external_links(external_ids):
    """Build external links dictionary from TMDB external_ids response."""
    links = {}

    if external_ids.get("imdb_id"):
        links["IMDb"] = f"https://www.imdb.com/title/{external_ids['imdb_id']}/"

    if external_ids.get("tvdb_id"):
        links["TVDB"] = (
            f"https://www.thetvdb.com/dereferrer/series/{external_ids['tvdb_id']}"
        )

    if external_ids.get("wikidata_id"):
        links["Wikidata"] = (
            f"https://www.wikidata.org/wiki/{external_ids['wikidata_id']}"
        )

    return links


def search(media_type, query, page):
    """Search for media on TMDB."""
    cache_key = f"search_{Sources.TMDB.value}_{media_type}_{query}_{page}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/search/{media_type}"

        params = {
            **base_params,
            "query": query,
            "page": page,
        }

        if settings.TMDB_NSFW:
            params["include_adult"] = "true"

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        results = [
            {
                "media_id": media["id"],
                "source": Sources.TMDB.value,
                "media_type": media_type,
                "title": get_title(media),
                "image": get_image_url(media["poster_path"]),
            }
            for media in response["results"]
        ]

        total_results = response["total_results"]
        per_page = 20  # TMDB always returns 20 results per page
        data = helpers.format_search_response(
            page,
            per_page,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def find(external_id, external_source):
    """Search for media on TMDB."""
    cache_key = f"find_{Sources.TMDB.value}_{external_id}_{external_source}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/find/{external_id}"

        params = {
            **base_params,
            "external_source": external_source,
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        cache.set(cache_key, response)
        return response

    return data


def get_cast(credits, limit=15):
    """Return the top cast members with their images."""
    if not credits or "cast" not in credits:
        return None

    cast = []
    for actor in sorted(credits["cast"], key=lambda x: x.get("order", 999))[:limit]:
        cast.append({
            "name": actor["name"],
            "character": actor.get("character"),
            "image": get_image_url(actor.get("profile_path")),
            "person_id": actor.get("id"),
        })
    return cast


def get_crew(credits, limit=15):
    """Return curated crew list prioritising director, producer, writer, cinematography."""
    if not credits or "crew" not in credits:
        return None

    priority = {
        "director": 0,
        "executive producer": 1,
        "producer": 1,
        "co-producer": 1,
        "associate producer": 1,
        "writer": 2,
        "screenplay": 2,
        "story": 2,
        "teleplay": 2,
        "cinematography": 3,
        "director of photography": 3,
        "photography": 3,
    }

    crew_map: dict[str, dict[str, object]] = {}

    for member in credits["crew"]:
        name = (member.get("name") or "").strip()
        if not name:
            continue

        job = (member.get("job") or "").strip()
        priority_key = job.lower()
        rank = priority.get(priority_key, 4)
        order = member.get("order", 999)

        entry = crew_map.setdefault(
            name,
            {
                "name": name,
                "roles": [],
                "rank": rank,
                "order": order,
                "person_id": member.get("id"),
            },
        )

        entry["rank"] = min(entry["rank"], rank)
        entry["order"] = min(entry["order"], order)

        if job and job not in entry["roles"]:
            entry["roles"].append(job)

    grouped = sorted(
        crew_map.values(),
        key=lambda item: (item["rank"], item["order"]),
    )

    trimmed = []
    for entry in grouped[:limit]:
        roles = entry.get("roles", [])
        entry["roles"] = roles
        trimmed.append(
            {
                "name": entry["name"],
                "roles": roles,
                "person_id": entry.get("person_id"),
                "job": (roles or [""])[0],
            }
        )

    return trimmed


def get_director(credits):
    """Return the director's name from crew."""
    if not credits or "crew" not in credits:
        return None

    directors = [
        crew["name"]
        for crew in credits["crew"]
        if (crew.get("job") or "").lower() == "director"
    ]
    return directors[0] if directors else None


def get_director_id(credits):
    """Return the director's person ID from crew."""
    if not credits or "crew" not in credits:
        return None

    directors = [
        crew for crew in credits["crew"]
        if (crew.get("job") or "").lower() == "director"
    ]
    return directors[0].get("id") if directors else None


def get_creator_id(created_by):
    """Return the creator's person ID from created_by."""
    if not created_by:
        return None
    first = created_by[0] if isinstance(created_by, list) else None
    return first.get("id") if first else None


def movie(media_id):
    """Return the metadata for the selected movie from The Movie Database."""
    cache_key = f"{Sources.TMDB.value}_{MediaTypes.MOVIE.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/movie/{media_id}"
        params = {
            **base_params,
            "append_to_response": "recommendations,credits,release_dates,external_ids",
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        data = {
            "media_id": media_id,
            "source": Sources.TMDB.value,
            "source_url": f"https://www.themoviedb.org/movie/{media_id}",
            "media_type": MediaTypes.MOVIE.value,
            "title": response["title"],
            "max_progress": 1,
            "image": get_image_url(response["poster_path"]),
            "synopsis": get_synopsis(response["overview"]),
            "genres": get_genres(response["genres"]),
            "score": get_score(response["vote_average"]),
            "score_count": response["vote_count"],
            "revenue": response.get("revenue"),
            "popularity": response.get("popularity"),
            "details": {
                "format": "Movie",
                "release_date": get_start_date(response["release_date"]),
                "status": response["status"],
                "runtime": get_readable_duration(response["runtime"]),
                "rating": get_movie_rating(response.get("release_dates")),
                "studios": get_companies(response["production_companies"]),
                "country": get_country(response["production_countries"]),
                "languages": get_languages(response["spoken_languages"]),
                "director": get_director(response.get("credits")),
                "director_id": get_director_id(response.get("credits")),
            },
            "cast": get_cast(response.get("credits")),
            "crew": get_crew(response.get("credits")),
            "related": {
                "recommendations": get_related(
                    response.get("recommendations", {}).get("results", [])[:15],
                    MediaTypes.MOVIE.value,
                ),
            },
            "external_links": get_external_links(response.get("external_ids", {})),
        }

        cache.set(cache_key, data)

    return data


def get_cached_seasons(media_id, season_numbers):
    """Check cache for seasons and return cached data and list of uncached seasons."""
    cached_data = {}
    uncached_seasons = []

    for season_number in season_numbers:
        season_cache_key = (
            f"{Sources.TMDB.value}_{MediaTypes.SEASON.value}_{media_id}_{season_number}"
        )
        season_data = cache.get(season_cache_key)
        if season_data:
            cached_data[f"season/{season_number}"] = season_data
        else:
            uncached_seasons.append(season_number)

    return cached_data, uncached_seasons


def enrich_season_with_tv_data(season_data, tv_data, media_id, season_number):
    """Add TV show metadata to season metadata."""
    season_data["media_id"] = media_id
    season_data["source_url"] = (
        f"https://www.themoviedb.org/tv/{media_id}/season/{season_number}"
    )
    season_data["title"] = tv_data["title"]
    season_data["tvdb_id"] = tv_data["tvdb_id"]
    season_data["external_links"] = tv_data["external_links"]
    season_data["genres"] = tv_data["genres"]
    if season_data["synopsis"] == "No synopsis available.":
        season_data["synopsis"] = tv_data["synopsis"]
    return season_data


def fetch_and_cache_seasons(media_id, season_numbers, tv_data):
    """Fetch uncached seasons from API and cache them."""
    url = f"{base_url}/tv/{media_id}"
    base_append = "recommendations,external_ids"
    max_seasons_per_request = 18
    fetched_tv_data = tv_data
    result_data = {}

    for i in range(0, len(season_numbers), max_seasons_per_request):
        season_subset = season_numbers[i : i + max_seasons_per_request]
        append_text = ",".join([f"season/{season}" for season in season_subset])

        params = {
            **base_params,
            "append_to_response": f"{base_append},{append_text}",
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        # Cache TV metadata if we haven't fetched it yet
        if fetched_tv_data is None:
            fetched_tv_data = process_tv(response)
            tv_cache_key = f"{Sources.TMDB.value}_{MediaTypes.TV.value}_{media_id}"
            cache.set(tv_cache_key, fetched_tv_data)

        # Process and cache each season
        for season_number in season_subset:
            season_key = f"season/{season_number}"
            if season_key not in response:
                msg = (
                    f"Season {season_number} not found in {Sources.TMDB.label} "
                    f"with ID {media_id}"
                )
                not_found_response = requests.Response()
                not_found_response.status_code = 404
                not_found_error = type("Error", (), {"response": not_found_response})
                raise services.ProviderAPIError(msg, error=not_found_error, details=msg)

            season_data = process_season(response[season_key])
            season_data = enrich_season_with_tv_data(
                season_data,
                fetched_tv_data,
                media_id,
                season_number,
            )

            cache.set(
                f"{Sources.TMDB.value}_{MediaTypes.SEASON.value}_{media_id}_{season_number}",
                season_data,
            )
            result_data[season_key] = season_data

    return result_data, fetched_tv_data


def tv_with_seasons(media_id, season_numbers):
    """Return the metadata for the tv show with seasons appended to the response."""
    if not season_numbers:
        return tv(media_id)

    tv_cache_key = f"{Sources.TMDB.value}_{MediaTypes.TV.value}_{media_id}"
    tv_data = cache.get(tv_cache_key)

    cached_seasons, uncached_seasons = get_cached_seasons(media_id, season_numbers)

    if tv_data is None and not uncached_seasons:
        tv_data = tv(media_id)

    if uncached_seasons:
        fetched_seasons, fetched_tv_data = fetch_and_cache_seasons(
            media_id,
            uncached_seasons,
            tv_data,
        )

        if tv_data is None:
            tv_data = fetched_tv_data

        cached_seasons.update(fetched_seasons)

    return tv_data | cached_seasons


def tv(media_id):
    """Return the metadata for the selected tv show from The Movie Database."""
    cache_key = f"{Sources.TMDB.value}_{MediaTypes.TV.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/tv/{media_id}"
        params = {
            **base_params,
            "append_to_response": "recommendations,external_ids,credits,content_ratings",
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        data = process_tv(response)
        cache.set(cache_key, data)

    return data


def process_tv(response):
    """Process the metadata for the selected tv show from The Movie Database."""
    num_episodes = response["number_of_episodes"]
    next_episode = response.get("next_episode_to_air")
    last_episode = response.get("last_episode_to_air")
    return {
        "media_id": response["id"],
        "source": Sources.TMDB.value,
        "source_url": f"https://www.themoviedb.org/tv/{response['id']}",
        "media_type": MediaTypes.TV.value,
        "title": response["name"],
        "max_progress": num_episodes,
        "image": get_image_url(response["poster_path"]),
        "synopsis": get_synopsis(response["overview"]),
        "genres": get_genres(response["genres"]),
        "score": get_score(response["vote_average"]),
        "score_count": response["vote_count"],
        "revenue": None,
        "popularity": response.get("popularity"),
        "details": {
            "format": "TV",
            "first_air_date": get_start_date(response["first_air_date"]),
            "last_air_date": response["last_air_date"],
            "status": response["status"],
            "seasons": response["number_of_seasons"],
            "episodes": num_episodes,
            "runtime": get_runtime_tv(response["episode_run_time"]),
            "rating": get_tv_rating(response.get("content_ratings")),
            "studios": get_companies(response["production_companies"]),
            "country": get_country(response["production_countries"]),
            "languages": get_languages(response["spoken_languages"]),
            "creator": get_creator(response.get("created_by")),
            "creator_id": get_creator_id(response.get("created_by")),
        },
        "cast": get_cast(response.get("credits")),
        "crew": get_crew(response.get("credits")),
        "related": {
            "seasons": get_related(
                response["seasons"],
                MediaTypes.SEASON.value,
                response,
            ),
            "recommendations": get_related(
                response.get("recommendations", {}).get("results", [])[:15],
                MediaTypes.TV.value,
            ),
        },
        "tvdb_id": response.get("external_ids", {}).get("tvdb_id"),
        "external_links": get_external_links(response.get("external_ids", {})),
        "last_episode_season": last_episode["season_number"] if last_episode else None,
        "next_episode_season": next_episode["season_number"] if next_episode else None,
    }


def process_season(response):
    """Process the metadata for the selected season from The Movie Database."""
    episodes = response["episodes"]
    num_episodes = len(episodes)

    runtimes = []
    total_runtime = 0
    score_count = 0

    for episode in episodes:
        if episode["runtime"] is not None:
            runtimes.append(episode["runtime"])
            total_runtime += episode["runtime"]
        score_count += episode["vote_count"]

    avg_runtime = (
        get_readable_duration(sum(runtimes) / len(runtimes)) if runtimes else None
    )
    total_runtime = get_readable_duration(total_runtime) if total_runtime else None

    return {
        "source": Sources.TMDB.value,
        "media_type": MediaTypes.SEASON.value,
        "season_title": response["name"],
        "max_progress": episodes[-1]["episode_number"] if episodes else 0,
        "image": get_image_url(response["poster_path"]),
        "season_number": response["season_number"],
        "synopsis": get_synopsis(response["overview"]),
        "score": get_score(response["vote_average"]),
        "score_count": score_count,
        "details": {
            "first_air_date": get_start_date(response["air_date"]),
            "last_air_date": get_end_date(response),
            "episodes": num_episodes,
            "runtime": avg_runtime,
            "total_runtime": total_runtime,
        },
        "episodes": response["episodes"],
    }


def get_format(media_type):
    """Return media_type capitalized."""
    if media_type == MediaTypes.TV.value:
        return "TV"
    return "Movie"


def get_image_url(path):
    """Return the image URL for the media."""
    # when no image, value from response is null
    # e.g movie: 445290
    if path:
        return f"https://image.tmdb.org/t/p/w500{path}"
    return settings.IMG_NONE


def fetch_image(path, size="original"):
    """Fetch TMDB image bytes for a given poster path.

    Args:
        path: TMDB file path beginning with a leading slash.
        size: Image size key (e.g. ``original``, ``w342``) supported by TMDB CDN.
    """

    if not path:
        raise ValueError("Cannot fetch TMDB image without a path")

    url = f"https://image.tmdb.org/t/p/{size}{path}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.content


def get_title(response):
    """Return the title for the media."""
    # tv shows have name instead of title
    try:
        return response["title"]
    except KeyError:
        return response["name"]


def get_start_date(date):
    """Return the start date for the media."""
    # when unknown date, value from response is empty string
    # e.g movie: 445290
    if date == "":
        return None
    return date


def get_end_date(response):
    """Return the last air date for the season."""
    if response["episodes"]:
        return response["episodes"][-1]["air_date"]

    return None


def get_synopsis(text):
    """Return the synopsis for the media."""
    # when unknown synopsis, value from response is empty string
    # e.g movie: 445290
    if text == "":
        return "No synopsis available."
    return text


def get_readable_duration(duration):
    """Convert duration in minutes to a readable format."""
    # if unknown movie runtime, value from response is 0
    # e.g movie: 274613
    if duration:
        hours, minutes = divmod(int(duration), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return None


def get_runtime_tv(runtime):
    """Return the runtime for the tv show."""
    # when unknown runtime, value from response is empty list
    # e.g: tv:66672
    if runtime:
        return get_readable_duration(runtime[0])
    return None


def season_scores_count(response):
    """Return the scores count for the season."""
    return sum(episode["vote_count"] for episode in response["episodes"])


def get_genres(genres):
    """Return the genres for the media."""
    # when unknown genres, value from response is empty list
    # e.g tv: 24795
    if genres:
        return [genre["name"] for genre in genres]
    return None


def get_country(countries):
    """Return the production country for the media."""
    # when unknown production country, value from response is empty list
    # e.g tv: 24795
    if countries:
        return countries[0]["name"]
    return None


def get_languages(languages):
    """Return the languages for the media."""
    # when unknown spoken languages, value from response is empty list
    # e.g tv: 24795
    if languages:
        return [language["english_name"] for language in languages]
    return None


def get_companies(companies):
    """Return the production companies for the media."""
    # when unknown production companies, value from response is empty list
    # e.g tv: 24795
    if companies:
        return [company["name"] for company in companies[:3]]
    return None


def get_score(score):
    """Return the score for the media with one decimal place."""
    # when unknown score, value from response is 0.0

    return round(score, 1)


def get_related(related_medias, media_type, parent_response=None):
    """Return list of related media for the selected media."""
    related = []
    for media in related_medias:
        data = {
            "source": Sources.TMDB.value,
            "media_type": media_type,
            "image": get_image_url(media["poster_path"]),
        }
        if media_type == MediaTypes.SEASON.value:
            data["media_id"] = parent_response["id"]
            data["title"] = parent_response["name"]
            data["season_number"] = media["season_number"]
            data["season_title"] = media["name"]
            data["first_air_date"] = get_start_date(media["air_date"])
            data["max_progress"] = media["episode_count"]
        else:
            data["media_id"] = media["id"]
            data["title"] = get_title(media)
        related.append(data)
    return related


def process_episodes(season_metadata, episodes_in_db):
    """Process the episodes for the selected season."""
    episodes_metadata = []

    # Convert the queryset to a dictionary for efficient lookups
    tracked_episodes = {}
    for ep in episodes_in_db:
        episode_number = ep.item.episode_number
        if episode_number not in tracked_episodes:
            tracked_episodes[episode_number] = []
        tracked_episodes[episode_number].append(ep)

    for episode in season_metadata["episodes"]:
        episode_number = episode["episode_number"]

        vote_average = episode.get("vote_average")
        vote_average_percent = (
            round(vote_average * 10)
            if isinstance(vote_average, (int, float))
            else None
        )
        vote_average_out_of_5 = (
            round(vote_average / 2, 1)
            if isinstance(vote_average, (int, float))
            else None
        )

        episodes_metadata.append(
            {
                "media_id": season_metadata["media_id"],
                "media_type": MediaTypes.EPISODE.value,
                "source": Sources.TMDB.value,
                "season_number": season_metadata["season_number"],
                "episode_number": episode_number,
                "air_date": episode["air_date"],  # when unknown, response returns null
                "image": get_image_url(episode["still_path"]),
                "title": episode["name"],
                "overview": episode["overview"],
                "history": tracked_episodes.get(episode_number, []),
                "runtime": get_readable_duration(episode["runtime"]),
                "vote_average": vote_average,
                "vote_average_out_of_5": vote_average_out_of_5,
                "vote_average_percent": vote_average_percent,
                "vote_count": episode.get("vote_count"),
            },
        )
    return episodes_metadata


def find_next_episode(episode_number, episodes_metadata):
    """Find the next episode number."""
    # Find the current episode in the sorted list
    current_episode_index = None
    for index, episode in enumerate(episodes_metadata):
        if episode["episode_number"] == episode_number:
            current_episode_index = index
            break

    # If episode not found or it's the last episode, return None
    if current_episode_index is None or current_episode_index + 1 >= len(
        episodes_metadata,
    ):
        return None

    # Return the next episode number
    return episodes_metadata[current_episode_index + 1]["episode_number"]


def episode(media_id, season_number, episode_number):
    """Return the metadata for the selected episode from The Movie Database."""
    tv_metadata = tv_with_seasons(media_id, [season_number])
    season_metadata = tv_metadata[f"season/{season_number}"]

    for episode in season_metadata["episodes"]:
        if episode["episode_number"] == int(episode_number):
            return {
                "title": season_metadata["title"],
                "season_title": season_metadata["season_title"],
                "episode_title": episode["name"],
                "image": get_image_url(episode["still_path"]),
            }

    # Episode not found - throw ProviderAPIError
    msg = (
        f"Episode {episode_number} not found in season {season_number} "
        f"for {Sources.TMDB.label} with ID {media_id}"
    )
    # Create a new response object with 404 status
    not_found_response = requests.Response()
    not_found_response.status_code = 404
    # Set the error attribute to match what ProviderAPIError expects
    not_found_error = type("Error", (), {"response": not_found_response})
    raise services.ProviderAPIError(
        Sources.TMDB.value,
        error=not_found_error,
        details=msg,
    )


def get_creator(creators):
    """Return the creator's name from the list."""
    if creators:
        return creators[0]["name"]
    return None


def get_movie_rating(release_dates):
    """Extract US MPAA rating from movie release dates."""
    if not release_dates or "results" not in release_dates:
        return None
    
    for result in release_dates["results"]:
        if result.get("iso_3166_1") == "US":
            release_dates_list = result.get("release_dates", [])
            if release_dates_list:
                # Get the first US release date with a certification
                for release_date in release_dates_list:
                    certification = release_date.get("certification")
                    if certification:
                        return certification
    return None


def get_tv_rating(content_ratings):
    """Extract US TV rating from content ratings."""
    if not content_ratings or "results" not in content_ratings:
        return None
    
    for result in content_ratings["results"]:
        if result.get("iso_3166_1") == "US":
            rating = result.get("rating")
            if rating:
                return rating
    return None


def person_page(person_id):
    """Return person details and credits for the person page."""
    cache_key = f"{Sources.TMDB.value}_person_{person_id}_v5"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/person/{person_id}"
        params = {
            **base_params,
            "append_to_response": "movie_credits,tv_credits",
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        credits = []

        for item in response.get("movie_credits", {}).get("cast", []) or []:
            title = item.get("title") or ""
            year = (item.get("release_date") or "")[:4] or None
            slug_val = slugify(title) or "movie"
            url_path = reverse(
                "media_details",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, item["id"], slug_val],
            )
            credits.append({
                "media_type": MediaTypes.MOVIE.value,
                "source": Sources.TMDB.value,
                "media_id": str(item["id"]),
                "title": title,
                "image": get_image_url(item.get("poster_path")),
                "role": item.get("character"),
                "year": year,
                "url": url_path,
                "popularity": item.get("popularity"),
                "vote_average": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        for item in response.get("movie_credits", {}).get("crew", []) or []:
            title = item.get("title") or ""
            year = (item.get("release_date") or "")[:4] or None
            slug_val = slugify(title) or "movie"
            url_path = reverse(
                "media_details",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, item["id"], slug_val],
            )
            credits.append({
                "media_type": MediaTypes.MOVIE.value,
                "source": Sources.TMDB.value,
                "media_id": str(item["id"]),
                "title": title,
                "image": get_image_url(item.get("poster_path")),
                "role": item.get("job"),
                "year": year,
                "url": url_path,
                "popularity": item.get("popularity"),
                "vote_average": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        for item in response.get("tv_credits", {}).get("cast", []) or []:
            title = item.get("name") or ""
            year = (item.get("first_air_date") or "")[:4] or None
            slug_val = slugify(title) or "tv"
            url_path = reverse(
                "media_details",
                args=[Sources.TMDB.value, MediaTypes.TV.value, item["id"], slug_val],
            )
            credits.append({
                "media_type": MediaTypes.TV.value,
                "source": Sources.TMDB.value,
                "media_id": str(item["id"]),
                "title": title,
                "image": get_image_url(item.get("poster_path")),
                "role": item.get("character"),
                "year": year,
                "url": url_path,
                "popularity": item.get("popularity"),
                "vote_average": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        for item in response.get("tv_credits", {}).get("crew", []) or []:
            title = item.get("name") or ""
            year = (item.get("first_air_date") or "")[:4] or None
            slug_val = slugify(title) or "tv"
            url_path = reverse(
                "media_details",
                args=[Sources.TMDB.value, MediaTypes.TV.value, item["id"], slug_val],
            )
            credits.append({
                "media_type": MediaTypes.TV.value,
                "source": Sources.TMDB.value,
                "media_id": str(item["id"]),
                "title": title,
                "image": get_image_url(item.get("poster_path")),
                "role": item.get("job"),
                "year": year,
                "url": url_path,
                "popularity": item.get("popularity"),
                "vote_average": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        # Consolidate multiple credits for the same title (e.g. actor + producer) into one card
        by_key = {}
        for c in credits:
            key = (c["source"], c["media_type"], c["media_id"])
            if key not in by_key:
                by_key[key] = {
                    "media_type": c["media_type"],
                    "source": c["source"],
                    "media_id": c["media_id"],
                    "title": c["title"],
                    "image": c["image"],
                    "year": c["year"],
                    "url": c["url"],
                    "popularity": c.get("popularity"),
                    "vote_average": c.get("vote_average"),
                    "vote_count": c.get("vote_count"),
                    "roles": [],
                }
            role = c.get("role")
            if role and role not in by_key[key]["roles"]:
                by_key[key]["roles"].append(role)
        credits = list(by_key.values())

        # Sort by vote_count (desc), then title; uses only TMDB data from person credits
        def _sort_key(x):
            return (-float(x.get("vote_count") or 0), (x.get("title") or ""))

        credits.sort(key=_sort_key)

        data = {
            "source": Sources.TMDB.value,
            "person_id": str(person_id),
            "name": response.get("name") or "",
            "image": get_image_url(response.get("profile_path")),
            "biography": (response.get("biography") or "").strip() or None,
            "credits": credits,
        }

        cache.set(cache_key, data)

    return data


def get_poster_images(media_id, media_type, season_number=None):
    """Get all available poster images for a movie, TV show, or season from TMDB.
    
    Args:
        media_id: TMDB ID of the media
        media_type: Either 'movie', 'tv', or 'season'
        season_number: Season number (required if media_type is 'season')
        
    Returns:
        List of poster image URLs and metadata
    """
    if media_type == 'season' and season_number is None:
        raise ValueError("season_number is required when media_type is 'season'")
    
    # Create cache key based on media type
    if media_type == 'season':
        cache_key = f"{Sources.TMDB.value}_season_posters_{media_id}_{season_number}"
        url = f"{base_url}/tv/{media_id}/season/{season_number}/images"
    else:
        cache_key = f"{Sources.TMDB.value}_{media_type}_posters_{media_id}"
        url = f"{base_url}/{media_type}/{media_id}/images"
    
    data = cache.get(cache_key)
    
    if data is None:
        params = {
            **base_params,
            # Remove language filtering to get ALL posters
        }
        # Remove the language parameter entirely to get all languages
        del params["language"]
        
        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        # Extract and format poster data
        posters = []
        for poster in response.get("posters", []):
            file_path = poster["file_path"]
            posters.append({
                "url": f"https://image.tmdb.org/t/p/original{file_path}",
                "thumbnail_url": f"https://image.tmdb.org/t/p/w342{file_path}",
                "width": poster["width"],
                "height": poster["height"],
                "aspect_ratio": poster["aspect_ratio"],
                "vote_average": poster.get("vote_average", 0),
                "vote_count": poster.get("vote_count", 0),
                "language": poster.get("iso_639_1")
            })
            
        # Sort by vote average (highest first), then by vote count
        data = sorted(
            posters,
            key=lambda x: (x["vote_average"], x["vote_count"]),
            reverse=True
        )
        
        # Cache for 24 hours - posters don't change often
        cache.set(cache_key, data, 86400)
        
    return data
