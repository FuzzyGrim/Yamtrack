import datetime
import logging
import re

import app
import app.models
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"


def importer(username, user):
    """Import the user's data from Trakt."""
    user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
    mal_shows_map = get_mal_mappings(is_show=True)
    mal_movies_map = get_mal_mappings(is_show=False)

    watched_shows = get_response(f"{user_base_url}/watched/shows")
    shows_msg = process_watched_shows(watched_shows, mal_shows_map, user)

    watched_movies = get_response(f"{user_base_url}/watched/movies")
    movies_msg = process_watched_movies(watched_movies, mal_movies_map, user)

    watchlist = get_response(f"{user_base_url}/watchlist")
    watchlist_msg = process_list(
        watchlist,
        mal_shows_map,
        mal_movies_map,
        user,
        "watchlist",
    )

    ratings = get_response(f"{user_base_url}/ratings")
    ratings_msg = process_list(ratings, mal_shows_map, mal_movies_map, user, "ratings")

    msgs = shows_msg + movies_msg + watchlist_msg + ratings_msg
    return "\n".join(msgs)


def get_response(url):
    """Get the response from the Trakt API."""
    trakt_api = "b4d9702b11cfaddf5e863001f68ce9d4394b678926e8a3f64d47bf69a55dd0fe"
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": trakt_api,
    }
    return app.providers.services.api_request(
        "KITSU",
        "GET",
        url,
        headers=headers,
    )


def process_watched_shows(watched, mal_mapping, user):
    """Process the watched shows from Trakt."""
    logger.info("Processing watched shows")
    warning_messages = []

    for entry in watched:
        mal_id = None
        trakt_id = entry["show"]["ids"]["trakt"]

        for season in entry["seasons"]:
            mal_id = mal_mapping.get((trakt_id, season["number"]))

            if mal_id:
                start_date = season["episodes"][0]["last_watched_at"]
                end_date = season["episodes"][-1]["last_watched_at"]
                repeats = 0
                for episode in season["episodes"]:
                    current_watch = episode["last_watched_at"]
                    if current_watch < start_date:
                        start_date = current_watch
                    if current_watch > end_date:
                        end_date = current_watch
                    if episode["plays"] - 1 > repeats:
                        repeats = episode["plays"] - 1

                defaults = {
                    "progress": season["episodes"][-1]["number"],
                    "status": app.models.STATUS_IN_PROGRESS,
                    "repeats": repeats,
                    "start_date": get_date(start_date),
                    "end_date": get_date(end_date),
                }

                add_mal_anime(mal_id, user, defaults)
            else:
                try:
                    add_tmdb_episodes(entry, season, user)
                except ValueError as e:
                    warning_messages.append(str(e))

    logger.info("Finished processing watched shows")
    return warning_messages


def process_watched_movies(watched, mal_mapping, user):
    """Process the watched movies from Trakt."""
    logger.info("Processing watched movies")
    warning_messages = []

    for entry in watched:
        defaults = {
            "progress": 1,
            "status": app.models.STATUS_COMPLETED,
            "repeats": entry["plays"] - 1,
            "start_date": get_date(entry["last_watched_at"]),
            "end_date": get_date(entry["last_watched_at"]),
        }
        try:
            add_movie(entry, user, defaults, "history", mal_mapping)
        except ValueError as e:
            warning_messages.append(str(e))
    logger.info("Finished processing watched movies")
    return warning_messages


def process_list(entries, mal_shows_map, mal_movies_map, user, list_type):
    """Process the default lists from Trakt, either watchlist or ratings."""
    logger.info("Processing %s", list_type)
    warning_messages = []

    for entry in entries:
        if list_type == "watchlist":
            defaults = {"status": app.models.STATUS_PLANNING}
        elif list_type == "ratings":
            defaults = {"score": entry["rating"]}

        trakt_type = entry["type"]
        if trakt_type == "show":
            try:
                add_show(entry, user, defaults, list_type, mal_shows_map)
            except ValueError as e:
                warning_messages.append(str(e))
        elif trakt_type == "season":
            try:
                add_season(entry, user, defaults, list_type, mal_shows_map)
            except ValueError as e:
                warning_messages.append(str(e))
        elif trakt_type == "movie":
            try:
                add_movie(entry, user, defaults, list_type, mal_movies_map)
            except ValueError as e:
                warning_messages.append(str(e))
    logger.info("Finished processing %s", list_type)
    return warning_messages


def add_show(entry, user, defaults, list_type, mal_shows_map):
    """Add a show to the user's library."""
    trakt_id = entry["show"]["ids"]["trakt"]
    mal_id = mal_shows_map.get((trakt_id, 1))

    if mal_id:
        add_mal_anime(mal_id, user, defaults)
    else:
        tmdb_id = entry["show"]["ids"]["tmdb"]

        if not tmdb_id:
            show_title = entry["show"]["title"]
            msg = f"Could not import {show_title} from {list_type}"
            raise ValueError(msg)
        add_tmdb_show(tmdb_id, user, defaults)


def add_tmdb_show(tmdb_id, user, defaults):
    """Add a show from TMDB to the user's library."""
    metadata = app.providers.tmdb.tv(tmdb_id)
    item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="tv",
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )
    app.models.TV.objects.update_or_create(
        item=item,
        user=user,
        defaults=defaults,
    )


def add_season(entry, user, defaults, list_type, mal_shows_map):
    """Add a season to the user's library."""
    trakt_id = entry["show"]["ids"]["trakt"]
    season_number = entry["season"]["number"]
    mal_id = mal_shows_map.get((trakt_id, season_number))

    if mal_id:
        add_mal_anime(mal_id, user, defaults)
    else:
        tmdb_id = entry["show"]["ids"]["tmdb"]

        if not tmdb_id:
            show_title = entry["show"]["title"]
            msg = f"Could not import {show_title} S{season_number} from {list_type}"
            raise ValueError(msg)
        add_tmdb_season(tmdb_id, season_number, user, defaults)


def add_tmdb_season(tmdb_id, season_number, user, defaults):
    """Add a season from TMDB to the user's library."""
    metadata = app.providers.tmdb.tv_with_seasons(tmdb_id, [season_number])
    tv_item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="tv",
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )
    tv_obj, _ = app.models.TV.objects.get_or_create(
        item=tv_item,
        user=user,
        defaults=defaults,
    )

    season_metadata = metadata[f"season/{season_number}"]
    season_item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="season",
        season_number=season_number,
        defaults={
            "title": metadata["title"],
            "image": season_metadata["image"],
        },
    )
    app.models.Season.objects.update_or_create(
        item=season_item,
        user=user,
        related_tv=tv_obj,
        defaults=defaults,
    )


def add_tmdb_episodes(entry, season, user):
    """Add episodes from TMDB to the user's library."""
    tmdb_id = entry["show"]["ids"]["tmdb"]

    if not tmdb_id:
        msg = f"Could not import history of {entry['show']['title']}"
        raise ValueError(msg)

    # collect all seasons metadata at once
    season_numbers = [season["number"] for season in entry["seasons"]]
    metadata = app.providers.tmdb.tv_with_seasons(tmdb_id, season_numbers)

    tv_item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="tv",
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )
    tv_obj, _ = app.models.TV.objects.get_or_create(
        item=tv_item,
        user=user,
        defaults={
            "status": app.models.STATUS_IN_PROGRESS,
        },
    )

    season_number = season["number"]
    season_item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="season",
        season_number=season_number,
        defaults={
            "title": metadata["title"],
            "image": metadata[f"season/{season_number}"]["image"],
        },
    )
    season_obj, _ = app.models.Season.objects.get_or_create(
        item=season_item,
        user=user,
        related_tv=tv_obj,
        defaults={
            "status": app.models.STATUS_IN_PROGRESS,
        },
    )

    for episode in season["episodes"]:
        ep_img = None
        for episode_metadata in metadata[f"season/{season_number}"]["episodes"]:
            if episode_metadata["episode_number"] == episode["number"]:
                ep_img = episode_metadata["still_path"]
                break

        if not ep_img:
            ep_img = settings.IMG_NONE

        episode_item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source="tmdb",
            media_type="episode",
            season_number=season_number,
            episode_number=episode["number"],
            defaults={
                "title": metadata["title"],
                "image": ep_img,
            },
        )
        app.models.Episode.objects.get_or_create(
            item=episode_item,
            related_season=season_obj,
            defaults={
                "watch_date": get_date(episode["last_watched_at"]),
                "repeats": episode["plays"] - 1,
            },
        )


def add_movie(entry, user, defaults, list_type, mal_movies_map):
    """Add a movie to the user's library."""
    trakt_id = entry["movie"]["ids"]["trakt"]
    mal_id = mal_movies_map.get((trakt_id, 1))

    if mal_id:
        add_mal_anime(mal_id, user, defaults)
    else:
        tmdb_id = entry["movie"]["ids"]["tmdb"]

        if not tmdb_id:
            movie_title = entry["movie"]["title"]
            msg = f"Could not import {movie_title} from {list_type}"
            raise ValueError(msg)
        add_tmdb_movie(tmdb_id, user, defaults)


def add_tmdb_movie(tmdb_id, user, defaults):
    """Add a movie from TMDB to the user's library."""
    metadata = app.providers.tmdb.movie(tmdb_id)
    item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="movie",
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )
    app.models.Movie.objects.get_or_create(
        item=item,
        user=user,
        defaults=defaults,
    )


def add_mal_anime(mal_id, user, defaults):
    """Add an anime from MAL to the user's library."""
    metadata = app.providers.mal.anime(mal_id)
    item, _ = app.models.Item.objects.get_or_create(
        media_id=mal_id,
        source="mal",
        media_type="anime",
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )
    app.models.Anime.objects.update_or_create(
        item=item,
        user=user,
        defaults=defaults,
    )


def download_and_parse_anitrakt_db(url):
    """Download and parse the AniTrakt database."""
    response = requests.get(url, timeout=settings.REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    trakt_to_mal = {}

    # Find all table rows
    rows = soup.select("tbody tr")

    for row in rows:
        trakt_cell = row.find("td", id="trakt")
        trakt_link = trakt_cell.find("a")

        try:
            mal_cell = row.find_all("td")[1]
        # skip if there is no MAL cell
        except IndexError:
            continue

        trakt_url = trakt_link.get("href")
        trakt_id = int(
            re.search(r"/(?:shows|movies)/(\d+)", trakt_url).group(1),
        )

        # Extract all MAL links for different seasons
        mal_links = mal_cell.find_all("a")
        for i, mal_link in enumerate(mal_links, start=1):
            mal_url = mal_link.get("href")
            mal_id = re.search(r"/anime/(\d+)", mal_url).group(1)

            # Store as (trakt_id, season)
            trakt_to_mal[(trakt_id, i)] = mal_id

    return trakt_to_mal


def get_mal_mappings(is_show):
    """Get or update the mapping from AniTrakt to MAL."""
    if is_show:
        cache_key = "anitrakt_shows_mapping"
        url = "https://anitrakt.huere.net/db/db_index_shows.php"
    else:
        cache_key = "anitrakt_movies_mapping"
        url = "https://anitrakt.huere.net/db/db_index_movies.php"

    mapping = cache.get(cache_key)
    if mapping is None:
        mapping = download_and_parse_anitrakt_db(url)
        cache.set(cache_key, mapping, 60 * 60 * 24)  # 24 hours
    return mapping


def get_date(date):
    """Convert the date from Trakt to a date object."""
    if date:
        return (
            datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%fZ")
            .replace(tzinfo=datetime.UTC)
            .astimezone(settings.TZ)
            .date()
        )
    return None
