import datetime
import re

import app
import app.models
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

TRAKT_API_BASE_URL = "https://api.trakt.tv"


def importer(username, user):
    """Import the user's data from Trakt."""
    user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
    mal_shows_map = get_mal_mappings(is_show=True)
    mal_movies_map = get_mal_mappings(is_show=False)

    watched_shows = get_response(f"{user_base_url}/watched/shows")
    shows_msg = process_shows(watched_shows, mal_shows_map, user)

    watched_movies = get_response(f"{user_base_url}/watched/movies")
    movies_msg = process_movies(watched_movies, mal_movies_map, user)

    watchlist = get_response(f"{user_base_url}/watchlist")
    watchlist_msg = process_watchlist(watchlist, mal_shows_map, mal_movies_map, user)

    ratings = get_response(f"{user_base_url}/ratings")
    ratings_msg = process_ratings(ratings, mal_shows_map, mal_movies_map, user)

    return shows_msg + movies_msg + watchlist_msg + ratings_msg


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


def process_shows(watched, mal_mapping, user):
    """Process the watched shows from Trakt."""
    warning_message = ""

    for entry in watched:
        mal_id = None
        trakt_id = entry["show"]["ids"]["trakt"]

        for season in entry["seasons"]:
            season_number = season["number"]
            mal_id = mal_mapping.get((trakt_id, season_number))

            if mal_id:
                metadata = app.providers.mal.anime(mal_id)
                anime_item, _ = app.models.Item.objects.get_or_create(
                    media_id=mal_id,
                    media_type="anime",
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )

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

                app.models.Anime.objects.get_or_create(
                    item=anime_item,
                    user=user,
                    defaults={
                        "progress": season["episodes"][-1]["number"],
                        "status": app.models.STATUS_IN_PROGRESS,
                        "repeats": repeats,
                        "start_date": get_date(start_date),
                        "end_date": get_date(end_date),
                    },
                )
            else:
                tmdb_id = entry["show"]["ids"]["tmdb"]

                if tmdb_id:
                    season_numbers = [season["number"] for season in entry["seasons"]]
                    metadata = app.providers.tmdb.tv_with_seasons(
                        tmdb_id,
                        season_numbers,
                    )
                    tv_item, _ = app.models.Item.objects.get_or_create(
                        media_id=tmdb_id,
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

                    season_item, _ = app.models.Item.objects.get_or_create(
                        media_id=tmdb_id,
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
                        for episode_metadata in metadata[f"season/{season_number}"][
                            "episodes"
                        ]:
                            if episode_metadata["episode_number"] == episode["number"]:
                                ep_img = episode_metadata["still_path"]
                                break

                        if not ep_img:
                            ep_img = settings.IMG_NONE

                        episode_item, _ = app.models.Item.objects.get_or_create(
                            media_id=tmdb_id,
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

                else:
                    warning_message += (
                        f"Could not import history of {entry["show"]['title']}"
                    )
    return warning_message


def process_movies(watched, mal_mapping, user):
    """Process the watched movies from Trakt."""
    warning_message = ""

    for entry in watched:
        trakt_id = entry["movie"]["ids"]["trakt"]
        mal_id = mal_mapping.get((trakt_id, 1))

        if mal_id:
            metadata = app.providers.mal.anime(mal_id)
            item, _ = app.models.Item.objects.get_or_create(
                media_id=mal_id,
                media_type="anime",
                defaults={
                    "title": metadata["title"],
                    "image": metadata["image"],
                },
            )
            app.models.Anime.objects.get_or_create(
                item=item,
                user=user,
                defaults={
                    "progress": 1,
                    "status": app.models.STATUS_COMPLETED,
                    "repeats": entry["plays"] - 1,
                    "start_date": get_date(entry["last_watched_at"]),
                    "end_date": get_date(entry["last_watched_at"]),
                },
            )
        else:
            tmdb_id = entry["movie"]["ids"]["tmdb"]

            if tmdb_id:
                metadata = app.providers.tmdb.movie(tmdb_id)
                item, _ = app.models.Item.objects.get_or_create(
                    media_id=tmdb_id,
                    media_type="movie",
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )
                app.models.Movie.objects.get_or_create(
                    item=item,
                    user=user,
                    defaults={
                        "progress": 1,
                        "status": app.models.STATUS_COMPLETED,
                        "repeats": entry["plays"] - 1,
                        "start_date": get_date(entry["last_watched_at"]),
                        "end_date": get_date(entry["last_watched_at"]),
                    },
                )
            else:
                warning_message += (
                    f"Could not import history of {entry["movie"]['title']}"
                )
    return warning_message


def process_watchlist(watchlist, mal_shows_map, mal_movies_map, user):
    """Process the watchlist from Trakt."""
    warning_message = ""

    for entry in watchlist:
        trakt_type = entry["type"]
        if trakt_type in ("show", "season"):
            # always get show id
            trakt_id = entry["show"]["ids"]["trakt"]
            season_number = entry["season"]["number"] if trakt_type == "season" else 1
            mal_id = mal_shows_map.get((trakt_id, season_number))

            if mal_id:
                metadata = app.providers.mal.anime(mal_id)
                item, _ = app.models.Item.objects.get_or_create(
                    media_id=mal_id,
                    media_type="anime",
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )
                app.models.Anime.objects.get_or_create(
                    item=item,
                    user=user,
                    defaults={
                        "status": app.models.STATUS_PLANNING,
                    },
                )
            else:
                tmdb_id = entry["show"]["ids"]["tmdb"]

                if tmdb_id:
                    metadata = app.providers.tmdb.tv(tmdb_id)
                    item, _ = app.models.Item.objects.get_or_create(
                        media_id=tmdb_id,
                        media_type="tv",
                        defaults={
                            "title": metadata["title"],
                            "image": metadata["image"],
                        },
                    )
                    tv_obj, _ = app.models.TV.objects.get_or_create(
                        item=item,
                        user=user,
                        defaults={
                            "status": app.models.STATUS_PLANNING,
                        },
                    )
                    if trakt_type == "season":
                        season_metadata = app.providers.tmdb.season(
                            tmdb_id,
                            season_number,
                        )
                        season_item, _ = app.models.Item.objects.get_or_create(
                            media_id=tmdb_id,
                            media_type="season",
                            season_number=season_number,
                            defaults={
                                "title": season_metadata["title"],
                                "image": season_metadata["image"],
                            },
                        )
                        app.models.Season.objects.get_or_create(
                            item=season_item,
                            user=user,
                            related_tv=tv_obj,
                            defaults={
                                "status": app.models.STATUS_PLANNING,
                            },
                        )
                else:
                    warning_message += (
                        f"Could not import watchlist of {entry["show"]['title']}"
                    )
        elif trakt_type == "movie":
            trakt_id = entry["movie"]["ids"]["trakt"]
            mal_id = mal_movies_map.get((trakt_id, 1))

            if mal_id:
                metadata = app.providers.mal.anime(mal_id)
                item, _ = app.models.Item.objects.get_or_create(
                    media_id=mal_id,
                    media_type="anime",
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )
                app.models.Anime.objects.get_or_create(
                    item=item,
                    user=user,
                    defaults={
                        "status": app.models.STATUS_PLANNING,
                    },
                )
            else:
                tmdb_id = entry["movie"]["ids"]["tmdb"]

                if tmdb_id:
                    metadata = app.providers.tmdb.movie(tmdb_id)
                    item, _ = app.models.Item.objects.get_or_create(
                        media_id=tmdb_id,
                        media_type="movie",
                        defaults={
                            "title": metadata["title"],
                            "image": metadata["image"],
                        },
                    )
                    app.models.Movie.objects.get_or_create(
                        item=item,
                        user=user,
                        defaults={
                            "status": app.models.STATUS_PLANNING,
                        },
                    )
                else:
                    warning_message += (
                        f"Could not import watchlist of {entry["movie"]['title']}"
                    )
    return warning_message


def process_ratings(ratings, mal_shows_map, mal_movies_map, user):
    """Process the ratings from Trakt."""
    warning_message = ""

    for entry in ratings:
        trakt_type = entry["type"]
        if trakt_type in ("show", "season"):
            # always get show id
            trakt_id = entry["show"]["ids"]["trakt"]
            season_number = entry["season"]["number"] if trakt_type == "season" else 1
            mal_id = mal_shows_map.get((trakt_id, season_number))

            if mal_id:
                metadata = app.providers.mal.anime(mal_id)
                item, _ = app.models.Item.objects.get_or_create(
                    media_id=mal_id,
                    media_type="anime",
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )
                app.models.Anime.objects.update_or_create(
                    item=item,
                    user=user,
                    defaults={
                        "score": entry["rating"],
                    },
                )
            else:
                tmdb_id = entry["show"]["ids"]["tmdb"]

                if tmdb_id:
                    metadata = app.providers.tmdb.tv(tmdb_id)
                    item, _ = app.models.Item.objects.get_or_create(
                        media_id=tmdb_id,
                        media_type="tv",
                        defaults={
                            "title": metadata["title"],
                            "image": metadata["image"],
                        },
                    )
                    if trakt_type == "show":
                        app.models.TV.objects.update_or_create(
                            item=item,
                            user=user,
                            defaults={
                                "score": entry["rating"],
                            },
                        )

                    if trakt_type == "season":
                        # dont update score if it already exists
                        tv_obj, _ = app.models.TV.objects.get_or_create(
                            item=item,
                            user=user,
                            defaults={
                                "score": entry["rating"],
                            },
                        )
                        season_metadata = app.providers.tmdb.season(
                            tmdb_id,
                            season_number,
                        )
                        season_item, _ = app.models.Item.objects.get_or_create(
                            media_id=tmdb_id,
                            media_type="season",
                            season_number=season_number,
                            defaults={
                                "title": season_metadata["title"],
                                "image": season_metadata["image"],
                            },
                        )
                        app.models.Season.objects.update_or_create(
                            item=season_item,
                            user=user,
                            related_tv=tv_obj,
                            defaults={
                                "score": entry["rating"],
                            },
                        )
                else:
                    warning_message += (
                        f"Could not import watchlist of {entry["show"]['title']}"
                    )
        elif trakt_type == "movie":
            trakt_id = entry["movie"]["ids"]["trakt"]
            mal_id = mal_movies_map.get((trakt_id, 1))

            if mal_id:
                metadata = app.providers.mal.anime(mal_id)
                item, _ = app.models.Item.objects.get_or_create(
                    media_id=mal_id,
                    media_type="anime",
                    defaults={
                        "title": metadata["title"],
                        "image": metadata["image"],
                    },
                )
                app.models.Anime.objects.update_or_create(
                    item=item,
                    user=user,
                    defaults={
                        "score": entry["rating"],
                    },
                )
            else:
                tmdb_id = entry["movie"]["ids"]["tmdb"]

                if tmdb_id:
                    metadata = app.providers.tmdb.movie(tmdb_id)
                    item, _ = app.models.Item.objects.get_or_create(
                        media_id=tmdb_id,
                        media_type="movie",
                        defaults={
                            "title": metadata["title"],
                            "image": metadata["image"],
                        },
                    )
                    app.models.Movie.objects.update_or_create(
                        item=item,
                        user=user,
                        defaults={
                            "score": entry["rating"],
                        },
                    )
                else:
                    warning_message += (
                        f"Could not import watchlist of {entry["movie"]['title']}"
                    )
    return warning_message


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
