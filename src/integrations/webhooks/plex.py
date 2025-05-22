import logging
import json

from django.core.cache import cache
from django.utils import timezone

import app
from app.models import Media, MediaTypes, Sources
import app.providers

logger = logging.getLogger(__name__)

def process_payload(payload, user):
    """Process a Plex webhook payload."""
    logger.debug("Received Plex webhook payload: %s", json.dumps(payload, indent=2))
    logger.debug("User: %s", user)
    event_type = payload["event"]

    if event_type not in ("media.scrobble", "media.play"):
        logger.info("Ignoring Plex webhook event: %s", event_type)
        return

    # Case-insensitive, trimmed user check (handle User object or string)
    if str(user).strip().lower() not in payload["Account"]["title"].strip().lower():
        logger.info("Ignoring Plex webhook event for user: %s", user)
        return

    tmdb_id = next((guid["id"].replace("tmdb://", "") for guid in payload["Metadata"]["Guid"] if guid["id"].startswith("tmdb://")), None)

    if payload["Metadata"]["type"] == "episode":
        media_type = MediaTypes.TV.value
        logger.info("TMDB Episode ID: %s", tmdb_id)
    elif payload["Metadata"]["type"] == "movie":
        media_type = MediaTypes.MOVIE.value
    else:
        logger.info("Ignoring Plex webhook type: %s", payload["Metadata"]["type"])
        return

    if tmdb_id is None:
        logger.info(
            "Ignoring Plex webhook call because no TMDB ID was found.",
        )
        return

    tmdb_id = int(tmdb_id)

    if media_type == MediaTypes.TV.value:
        imdb_id = next((guid["id"].replace("imdb://", "") for guid in payload["Metadata"]["Guid"] if guid["id"].startswith("imdb://")), None)

        title = payload["Metadata"]["grandparentTitle"]

        response = app.providers.tmdb.find(imdb_id, "imdb_id")
        if response:
            media_id = response["show_id"]
            logger.info("TMDB Show ID: %s", media_id)

        logger.info("Detected TV show: %s", title)
        handle_tv_episode(media_id, payload, user)

    elif media_type == MediaTypes.MOVIE.value:
        title = payload["Metadata"]["title"]
        logger.info("Detected movie: %s", title)
        handle_movie(tmdb_id, payload, user)


def handle_movie(media_id, payload, user):
    """Add a movie as watched."""
    movie_metadata = app.providers.tmdb.movie(media_id)
    movie_played = payload["event"] == "media.scrobble"
    movie_started = payload["event"] == "media.play"
    progress = 1 if movie_played else 0

    movie_item, _ = app.models.Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.MOVIE.value,
        defaults={
            "title": movie_metadata["title"],
            "image": movie_metadata["image"],
        },
    )

    # Check if the movie is already in the database  
    movie, created = app.models.Movie.objects.get_or_create(
        item=movie_item,
        user=user,
        defaults={
            "start_date": timezone.now().replace(second=0, microsecond=0),
            "status": Media.Status.IN_PROGRESS.value,
        },
    )
    
    if created:
        logger.debug("Movie created: %s", movie)
        # Movie DOES NOT exists in the database
        movie.progress = progress
        movie.status = Media.Status.IN_PROGRESS.value
        if movie_played:
            # Movie has been scrobbled / played
            # Set end date and status to completed
            movie.end_date = timezone.now().replace(second=0, microsecond=0)
            movie.status = Media.Status.COMPLETED.value
        movie.save()
    else:
        # Movie already exists in the database
        if movie_started:
            # Movie has started playing AND in the database
            # Set start date and status to in progress
            movie.start_date = timezone.now().replace(second=0, microsecond=0)
            movie.status = Media.Status.IN_PROGRESS.value
        elif movie_played:
            # Movie has been scrobbled / played AND in the database
            # Set end date and status to completed the first time
            # and status to repeating next time
            movie.end_date = timezone.now().replace(second=0, microsecond=0)
            if movie.repeats > 0:
                movie.status = Media.Status.REPEATING.value
            else:
                movie.status = Media.Status.COMPLETED.value
            movie.repeats += 1
        movie.save()

def handle_tv_episode(media_id, payload, user):
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