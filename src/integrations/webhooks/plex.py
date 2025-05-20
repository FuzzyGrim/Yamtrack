import logging
import json

from django.core.cache import cache
from django.utils import timezone

import app
from app.models import Media, MediaTypes, Sources
import app.providers

logger = logging.getLogger(__name__)

def get_guids(guids):
    """Extracts services and IDs from the payload's Metadata.Guid."""
    if not guids:
        logger.error("Couldn't find Guids")
        return

    guids = [
        {
            "service": guid.get("id", "")[:4],
            "id": guid.get("id", "")[7:]
        }
        for guid in guids
    ]

    logger.debug(f"services and ids: {json.dumps(guids)}")
    return guids

def process_payload(payload, user):
    """Process a Plex webhook payload."""
    event_type = payload["event"]


    logger.info("User: %s", user.username)
    logger.info(event_type)
    # logger.info(payload)


    if payload["Metadata"]["Guid"] is not None:
        episode_guids = get_guids(payload)
        logger.info("Episode Guids: %s", episode_guids)
    else:
        logger.info("No Guids found in payload.")
        return


    if event_type not in ("media.resume", "media.pause", "media.scrobble"):
        logger.info("Ignoring Plex webhook event: %s", event_type)
        return

    if payload["Metadata"]["type"] == "episode":
        media_type = MediaTypes.TV.value
        tmdb_id = next((guid["id"].replace("tmdb://", "") for guid in payload["Metadata"]["Guid"] if guid["id"].startswith("tmdb://")), None)
        logger.info("TMDB Episode ID: %s", tmdb_id)
    elif payload["Metadata"]["type"] == "movie":
        media_type = MediaTypes.MOVIE.value
        tmdb_id = next((guid["id"].replace("tmdb://", "") for guid in payload["Metadata"]["Guid"] if guid["id"].startswith("tmdb://")), None)
    else:
        logger.info("Ignoring Plex webhook event: %s", payload["Metadata"]["type"])
        return

    if tmdb_id is None:
        logger.info(
            "Ignoring Plex webhook call because no TMDB ID was found.",
        )
        return

    tmdb_id = int(tmdb_id)
    mapping_data = fetch_mapping_data()

    if media_type == MediaTypes.TV.value:
        season_number = payload["Metadata"]["parentIndex"]
        episode_number = payload["Metadata"]["index"]
        tvdb_id = next((guid["id"].replace("tvdb://", "") for guid in payload["Metadata"]["Guid"] if guid["id"].startswith("tvdb://")), None)
        imdb_id = next((guid["id"].replace("imdb://", "") for guid in payload["Metadata"]["Guid"] if guid["id"].startswith("imdb://")), None)

        title = payload["Metadata"]["grandparentTitle"]

        response = app.providers.tmdb.find(imdb_id, "imdb_id")
        if response:
            media_id = response["show_id"]
            logger.info("TMDB Show ID: %s", media_id)

        if tvdb_id and user.anime_enabled:
            tvdb_id = int(tvdb_id)
            mal_id, episode_offset = get_mal_id_from_tvdb(
                mapping_data,
                tvdb_id,
                season_number,
                episode_number,
            )
            if mal_id:
                logger.info("Detected anime: %s", title)
                add_anime(mal_id, episode_offset, payload, user)
                return

        logger.info("Detected TV show: %s", title)
        add_tv(media_id, payload, user)

    elif media_type == MediaTypes.MOVIE.value:
        title = payload["Item"]["Name"]
        mal_id = get_mal_id_from_tmdb_movie(mapping_data, tmdb_id)
        if mal_id and user.anime_enabled:
            logger.info("Detected anime movie: %s", title)
            # add_anime(mal_id, 1, payload, user)
        else:
            logger.info("Detected movie: %s", title)
            # add_movie(tmdb_id, payload, user)


def add_anime(media_id, episode_number, payload, user):
    """Add an anime episode as watched."""
    anime_metadata = app.providers.mal.anime(media_id)
    episode_played = payload["Item"]["UserData"]["Played"]

    anime_item, _ = app.models.Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.MAL.value,
        media_type=MediaTypes.ANIME.value,
        defaults={
            "title": anime_metadata["title"],
            "image": anime_metadata["image"],
        },
    )

    if not episode_played:
        episode_number = episode_number - 1

    try:
        anime_instance = app.models.Anime.objects.get(
            item=anime_item,
            user=user,
        )
        anime_instance.progress = episode_number

        if (
            anime_instance.status == Media.Status.COMPLETED.value and episode_played
        ) or anime_instance.status == Media.Status.REPEATING.value:
            anime_instance.status = Media.Status.REPEATING.value
        else:
            anime_instance.status = Media.Status.IN_PROGRESS.value
        anime_instance.save()
    except app.models.Anime.DoesNotExist:
        app.models.Anime.objects.create(
            item=anime_item,
            user=user,
            progress=episode_number,
            status=Media.Status.IN_PROGRESS.value,
        )


def add_movie(media_id, payload, user):
    """Add a movie as watched."""
    movie_metadata = app.providers.tmdb.movie(media_id)
    movie_played = payload["Item"]["UserData"]["Played"]
    progress = 1 if movie_played else 0

    item, _ = app.models.Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.MOVIE.value,
        defaults={
            "title": movie_metadata["title"],
            "image": movie_metadata["image"],
        },
    )

    try:
        movie_instance = app.models.Movie.objects.get(
            item=item,
            user=user,
        )
        movie_instance.progress = progress

        if (
            movie_instance.status == Media.Status.COMPLETED.value and movie_played
        ) or movie_instance.status == Media.Status.REPEATING.value:
            if movie_played:
                movie_instance.repeats += 1
            else:
                movie_instance.status = Media.Status.REPEATING.value
            movie_instance.status = Media.Status.REPEATING.value
        else:
            movie_instance.status = Media.Status.IN_PROGRESS.value
        movie_instance.save()

    except app.models.Movie.DoesNotExist:
        app.models.Movie.objects.create(
            item=item,
            user=user,
            progress=progress,
            status=Media.Status.IN_PROGRESS.value,
        )


def add_tv(media_id, payload, user):
    """Add a TV show episode as watched."""
    season_number = payload["Metadata"]["parentIndex"]
    episode_number = payload["Metadata"]["index"]

    tv_metadata = app.providers.tmdb.tv_with_seasons(
        media_id,
        [season_number],
    )
    season_metadata = tv_metadata[f"season/{season_number}"]

    tv_item, _ = app.models.Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.TV.value,
        defaults={
            "title": tv_metadata["title"],
            "image": tv_metadata["image"],
        },
    )

    tv_instance, _ = app.models.TV.objects.update_or_create(
        item=tv_item,
        user=user,
        defaults={
            "status": Media.Status.IN_PROGRESS.value,
        },
    )

    season_item, _ = app.models.Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
        defaults={
            "title": tv_metadata["title"],
            "image": season_metadata["image"],
        },
    )

    season_instance, _ = app.models.Season.objects.update_or_create(
        item=season_item,
        user=user,
        related_tv=tv_instance,
        defaults={
            "status": Media.Status.IN_PROGRESS.value,
        },
    )

    episode_item, _ = app.models.Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.EPISODE.value,
        season_number=season_number,
        episode_number=episode_number,
        defaults={
            "title": tv_metadata["title"],
            "image": season_metadata["image"],
        },
    )
    
    app.models.Episode.objects.get_or_create(
        item=episode_item,
        related_season=season_instance,
        defaults={
            "end_date": timezone.now().replace(second=0, microsecond=0),
        },
    )



def fetch_mapping_data():
    """Fetch the anime mapping data from GitHub."""
    data = cache.get("Plex_anime_mapping")

    if data is None:
        url = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/refs/heads/master/anime_ids.json"
        data = app.providers.services.api_request("GITHUB", "GET", url)
        cache.set("Plex_anime_mapping", data)

    return data


def get_mal_id_from_tvdb(mapping_data, tvdb_id, season_number, episode_number):
    """Find the appropriate MAL ID based on TVDB id."""
    matching_entries = [
        entry
        for entry in mapping_data.values()
        if entry.get("tvdb_id") == tvdb_id
        and entry.get("tvdb_season") == season_number
        and "mal_id" in entry
    ]

    if not matching_entries:
        return None, None

    # Sort entries by epoffset
    matching_entries.sort(key=lambda x: x.get("tvdb_epoffset", 0))

    # Find the appropriate entry based on episode number
    for i, entry in enumerate(matching_entries):
        current_offset = entry.get("tvdb_epoffset", 0)
        next_offset = float("inf")

        if i < len(matching_entries) - 1:
            next_offset = matching_entries[i + 1].get("tvdb_epoffset", float("inf"))

        if episode_number > current_offset and (
            episode_number <= next_offset or next_offset == float("inf")
        ):
            return entry["mal_id"], episode_number - current_offset

    return None, None


def get_mal_id_from_tmdb_movie(mapping_data, tmdb_movie_id):
    """Find the MAL ID for a given TMDB movie ID."""
    for entry in mapping_data.values():
        if entry.get("tmdb_movie_id") == tmdb_movie_id and "mal_id" in entry:
            return entry["mal_id"]
    return None
