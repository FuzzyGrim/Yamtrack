import datetime
import logging
import re

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache
from django.db.models import Sum

import app
import app.models

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"


def importer(username, user):
    """Import the user's data from Trakt."""
    user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
    mal_shows_map = get_mal_mappings(is_show=True)
    mal_movies_map = get_mal_mappings(is_show=False)

    # check if the user exists
    try:
        watched_shows = get_response(f"{user_base_url}/watched/shows")
    except requests.exceptions.HTTPError as error:
        if error.response.status_code == requests.codes.not_found:
            msg = (
                f"User slug {username} not found. "
                "User slug can be found in the URL when viewing your Trakt profile."
            )
            raise ValueError(msg) from error
        raise  # re-raise for other errors
    shows_msg, shows_num = process_watched_shows(
        watched_shows,
        mal_shows_map,
        user,
    )

    watched_movies = get_response(f"{user_base_url}/watched/movies")
    movies_msg, movies_num = process_watched_movies(
        watched_movies,
        mal_movies_map,
        user,
    )

    watchlist = get_response(f"{user_base_url}/watchlist")
    watchlist_msg, watchlist_num = process_list(
        watchlist,
        mal_shows_map,
        mal_movies_map,
        user,
        "watchlist",
    )

    ratings = get_response(f"{user_base_url}/ratings")
    ratings_msg, ratings_num = process_list(
        ratings,
        mal_shows_map,
        mal_movies_map,
        user,
        "ratings",
    )

    warning_messages = shows_msg + movies_msg + watchlist_msg + ratings_msg
    return (
        shows_num,
        movies_num,
        watchlist_num,
        ratings_num,
        "\n".join(warning_messages),
    )


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
    num_imported = 0

    for entry in watched:
        mal_id = None
        trakt_id = entry["show"]["ids"]["trakt"]

        # only track number of tv shows imported
        show_added = False

        for season in entry["seasons"]:
            mal_id = mal_mapping.get((trakt_id, season["number"]))
            try:
                if mal_id:
                    defaults = get_anime_default_fields(season)
                    add_mal_anime(entry, mal_id, user, defaults)
                else:
                    add_tmdb_episodes(entry, season, user)
            except ValueError as e:
                warning_messages.append(str(e))
                break

            if not show_added:
                num_imported += 1
                show_added = True
    logger.info("Finished processing watched shows")
    return warning_messages, num_imported


def get_anime_default_fields(season):
    """Get the defaults tracking fields for watched anime."""
    start_date = season["episodes"][0]["last_watched_at"]
    end_date = season["episodes"][-1]["last_watched_at"]
    repeats = 0
    for episode in season["episodes"]:
        current_watch = episode["last_watched_at"]
        start_date = min(start_date, current_watch)
        end_date = max(end_date, current_watch)
        repeats = max(repeats, episode["plays"] - 1)

    return {
        "progress": season["episodes"][-1]["number"],
        "status": app.models.STATUS_IN_PROGRESS,
        "repeats": repeats,
        "start_date": get_date(start_date),
        "end_date": get_date(end_date),
    }


def process_watched_movies(watched, mal_mapping, user):
    """Process the watched movies from Trakt."""
    logger.info("Processing watched movies")
    warning_messages = []
    num_imported = 0

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
        else:
            num_imported += 1

    logger.info("Finished processing watched movies")
    return warning_messages, num_imported


def process_list(entries, mal_shows_map, mal_movies_map, user, list_type):
    """Process the default lists from Trakt, either watchlist or ratings."""
    logger.info("Processing %s", list_type)
    warning_messages = []
    num_imported = 0

    type_processors = {
        "show": lambda: add_show(entry, user, defaults, list_type, mal_shows_map),
        "season": lambda: add_season(entry, user, defaults, list_type, mal_shows_map),
        "movie": lambda: add_movie(entry, user, defaults, list_type, mal_movies_map),
    }

    for entry in entries:
        if list_type == "watchlist":
            defaults = {"status": app.models.STATUS_PLANNING}
        elif list_type == "ratings":
            defaults = {"score": entry["rating"]}
        trakt_type = entry["type"]

        entry_processor = type_processors.get(trakt_type)
        # skip if the type is not supported, like episode
        if not entry_processor:
            continue

        try:
            entry_processor()
        except ValueError as e:
            warning_messages.append(str(e))
        else:
            num_imported += 1

    logger.info("Finished processing %s", list_type)
    return warning_messages, num_imported


def add_show(entry, user, defaults, list_type, mal_shows_map):
    """Add a show to the user's library."""
    trakt_id = entry["show"]["ids"]["trakt"]
    mal_id = mal_shows_map.get((trakt_id, 1))

    if mal_id:
        add_mal_anime(entry, mal_id, user, defaults)
    else:
        add_tmdb_show(entry, user, defaults, list_type)


def add_tmdb_show(entry, user, defaults, list_type):
    """Add a show from TMDB to the user's library."""
    tmdb_id = entry["show"]["ids"]["tmdb"]
    trakt_title = entry["show"]["title"]

    if not tmdb_id:
        msg = f"No TMDB ID found for {trakt_title} in {list_type}"
        raise ValueError(msg)
    metadata = get_metadata(app.providers.tmdb.tv, "TMDB", trakt_title, tmdb_id)

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
        add_mal_anime(entry, mal_id, user, defaults)
    else:
        add_tmdb_season(entry, season_number, user, defaults, list_type)


def add_tmdb_season(entry, season_number, user, defaults, list_type):
    """Add a season from TMDB to the user's library."""
    tmdb_id = entry["show"]["ids"]["tmdb"]
    trakt_title = entry["show"]["title"]

    if not tmdb_id:
        msg = f"No TMDB ID found for {trakt_title} S{season_number} in {list_type}"
        raise ValueError(msg)

    metadata = get_metadata(
        app.providers.tmdb.tv_with_seasons,
        "TMDB",
        trakt_title,
        tmdb_id,
        [season_number],
    )

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
    trakt_title = entry["show"]["title"]

    if not tmdb_id:
        msg = f"No TMDB ID found for {trakt_title} in watch history"
        raise ValueError(msg)

    # collect all seasons metadata at once
    season_numbers = [season["number"] for season in entry["seasons"]]
    metadata = get_metadata(
        app.providers.tmdb.tv_with_seasons,
        "TMDB",
        trakt_title,
        tmdb_id,
        season_numbers,
    )

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
        ep, _ = app.models.Episode.objects.get_or_create(
            item=episode_item,
            related_season=season_obj,
            defaults={
                "watch_date": get_date(episode["last_watched_at"]),
                "repeats": episode["plays"] - 1,
            },
        )

        consider_repeating(ep, metadata, season_number)


def consider_repeating(episode, metadata, season_number):
    """Set the season and show to repeating if necessary."""
    max_progress = len(metadata[f"season/{season_number}"]["episodes"])
    total_repeats = episode.related_season.episodes.aggregate(
        total_repeats=Sum("repeats"),
    )["total_repeats"]

    total_watches = episode.related_season.progress + total_repeats
    if (
        total_watches > max_progress
        and episode.related_season.status == app.models.STATUS_IN_PROGRESS
    ):
        episode.related_season.status = app.models.STATUS_REPEATING
        episode.related_season.save_base(update_fields=["status"])

        if (
            episode.related_season.related_tv.progress
            >= metadata["details"]["number_of_episodes"]
        ):
            episode.related_season.related_tv.status = app.models.STATUS_COMPLETED
            episode.related_season.related_tv.save_base(update_fields=["status"])


def add_movie(entry, user, defaults, list_type, mal_movies_map):
    """Add a movie to the user's library."""
    trakt_id = entry["movie"]["ids"]["trakt"]
    mal_id = mal_movies_map.get((trakt_id, 1))

    if mal_id:
        add_mal_anime(entry, mal_id, user, defaults)
    else:
        add_tmdb_movie(entry, user, defaults, list_type)


def add_tmdb_movie(entry, user, defaults, list_type):
    """Add a movie from TMDB to the user's library."""
    tmdb_id = entry["movie"]["ids"]["tmdb"]
    trakt_title = entry["movie"]["title"]

    if not tmdb_id:
        msg = f"No TMDB ID found for {trakt_title} in {list_type}"
        raise ValueError(msg)

    metadata = get_metadata(app.providers.tmdb.movie, "TMDB", trakt_title, tmdb_id)

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


def add_mal_anime(entry, mal_id, user, defaults):
    """Add an anime from MAL to the user's library."""
    try:
        title = entry["show"]["title"]
    except KeyError:
        title = entry["movie"]["title"]
    metadata = get_metadata(app.providers.mal.anime, "MAL", title, mal_id)

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


def get_metadata(fetch_func, source, title, *args, **kwargs):
    """Fetch metadata from various sources and handle errors."""
    try:
        return fetch_func(*args, **kwargs)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == requests.codes.not_found:
            msg = f"{title}: Couldn't fetch metadata from {source} ({args[0]})"
            logger.warning(msg)
            raise ValueError(msg) from e
        raise  # Re-raise other HTTP errors
    except KeyError as e:
        msg = f"{title}: Couldn't parse incomplete metadata from {source} ({args[0]})"
        logger.warning(msg)
        raise ValueError(msg) from e


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
