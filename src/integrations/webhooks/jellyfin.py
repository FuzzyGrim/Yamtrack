import logging
import json

from django.core.cache import cache
from django.utils import timezone

import app
from app.models import Media, MediaTypes, Sources

logger = logging.getLogger(__name__)


def process_payload(payload, user):
    """Process a Jellyfin webhook payload."""
    logger.debug("Processing Jellyfin webhook payload: %s", json.dumps(payload, indent=2))

    event_type = payload["Event"]

    if event_type not in ("Play", "Stop", "MarkPlayed", "MarkUnplayed"):
        logger.info("Ignoring Jellyfin webhook event: %s", event_type)
        return

    if payload["Item"]["Type"] == "Episode":
        media_type = MediaTypes.TV.value
        tmdb_id = payload["Series"]["ProviderIds"].get("Tmdb")
    elif payload["Item"]["Type"] == "Movie":
        media_type = MediaTypes.MOVIE.value
        tmdb_id = payload["Item"]["ProviderIds"].get("Tmdb")
    else:
        logger.info("Ignoring Jellyfin webhook event: %s", payload["Item"]["Type"])
        return

    if tmdb_id is None:
        logger.info(
            "Ignoring Jellyfin webhook call because no TMDB ID was found.",
        )
        return

    tmdb_id = int(tmdb_id)
    mapping_data = fetch_mapping_data()

    if media_type == MediaTypes.TV.value:
        season_number = payload["Item"]["ParentIndexNumber"]
        episode_number = payload["Item"]["IndexNumber"]
        tvdb_id = payload["Series"]["ProviderIds"].get("Tvdb")
        title = payload["Series"]["Name"]

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
                handle_anime(mal_id, episode_offset, payload, user)
                return

        logger.info("Detected TV show: %s", title)
        handle_tv_episode(tmdb_id, payload, user)

    elif media_type == MediaTypes.MOVIE.value:
        title = payload["Item"]["Name"]
        mal_id = get_mal_id_from_tmdb_movie(mapping_data, tmdb_id)
        if mal_id and user.anime_enabled:
            logger.info("Detected anime movie: %s", title)
            handle_anime(mal_id, 1, payload, user)
        else:
            logger.info("Detected movie: %s", title)
            handle_movie(tmdb_id, payload, user)


def handle_anime(media_id, episode_number, payload, user):
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
        episode_number = max(0, episode_number - 1)

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


def handle_movie(media_id, payload, user):
    """Handle movie object from payload."""
    movie_metadata = app.providers.tmdb.movie(media_id)
    movie_played = payload["Item"]["UserData"]["Played"]
    progress = 1 if movie_played else 0
    now = timezone.now().replace(second=0, microsecond=0)

    movie_item, _ = app.models.Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.MOVIE.value,
        defaults={
            "title": movie_metadata["title"],
            "image": movie_metadata["image"],
        },
    )

    movie_instance, created = app.models.Movie.objects.get_or_create(
        item=movie_item,
        user=user,
        defaults={
            "progress": progress,
            "status": Media.Status.COMPLETED.value
            if movie_played
            else Media.Status.IN_PROGRESS.value,
            "start_date": now if not movie_played else None,
            "end_date": now if movie_played else None,
        },
    )

    if not created:
        movie_instance.progress = progress

        if movie_played:
            movie_instance.end_date = now

            if movie_instance.status == Media.Status.COMPLETED.value:
                movie_instance.repeats += 1
            elif movie_instance.status == Media.Status.REPEATING.value:
                movie_instance.repeats += 1
                movie_instance.status = Media.Status.COMPLETED.value
            else:
                movie_instance.status = Media.Status.COMPLETED.value

        elif movie_instance.status == Media.Status.COMPLETED.value:
            movie_instance.status = Media.Status.REPEATING.value
            movie_instance.start_date = now
            movie_instance.end_date = None

        elif movie_instance.status not in (
            Media.Status.REPEATING.value,
            Media.Status.IN_PROGRESS.value,
        ):
            movie_instance.status = Media.Status.IN_PROGRESS.value
            if not movie_instance.start_date:
                movie_instance.start_date = now

        movie_instance.save()


def handle_tv_episode(media_id, payload, user):
    """Add a TV show episode as watched."""
    season_number = payload["Item"]["ParentIndexNumber"]
    episode_number = payload["Item"]["IndexNumber"]

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

    tv_instance, created = app.models.TV.objects.get_or_create(
        item=tv_item,
        user=user,
        defaults={
            "status": Media.Status.IN_PROGRESS.value,
        },
    )

    if not created and tv_instance.status not in (
        Media.Status.COMPLETED.value,
        Media.Status.REPEATING.value,
        Media.Status.IN_PROGRESS.value,
    ):
        tv_instance.status = Media.Status.IN_PROGRESS.value
        tv_instance.save()

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

    season_instance, created = app.models.Season.objects.get_or_create(
        item=season_item,
        user=user,
        related_tv=tv_instance,
        defaults={
            "status": Media.Status.IN_PROGRESS.value,
        },
    )

    if not created and season_instance.status not in (
        Media.Status.COMPLETED.value,
        Media.Status.REPEATING.value,
        Media.Status.IN_PROGRESS.value,
    ):
        season_instance.status = Media.Status.IN_PROGRESS.value
        season_instance.save()

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

    if payload["Item"]["UserData"]["Played"]:
        now = timezone.now().replace(second=0, microsecond=0)
        episode, created = app.models.Episode.objects.get_or_create(
            item=episode_item,
            related_season=season_instance,
            defaults={
                "end_date": now,
            },
        )

        if not created:
            episode.end_date = now
            episode.repeats += 1
            episode.save()

    elif payload["Event"] == "MarkUnplayed":
        app.models.Episode.objects.filter(
            item=episode_item,
            related_season=season_instance,
        ).delete()


def fetch_mapping_data():
    """Fetch the anime mapping data from GitHub."""
    data = cache.get("jellyfin_anime_mapping")

    if data is None:
        url = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/refs/heads/master/anime_ids.json"
        data = app.providers.services.api_request("GITHUB", "GET", url)
        cache.set("jellyfin_anime_mapping", data)

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
