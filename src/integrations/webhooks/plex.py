import json
import logging

from django.utils import timezone

import app
import app.providers
from app.models import Media, MediaTypes, Sources

logger = logging.getLogger(__name__)


def process_payload(payload, user):
    """Process a Plex webhook payload."""
    logger.debug("Received Plex webhook payload: %s", json.dumps(payload, indent=2))
    event_type = payload.get("event")
    if not _is_supported_event(event_type):
        logger.info("Ignoring Plex webhook event: %s", event_type)
        return

    if not _is_valid_user(payload, user):
        logger.info("Ignoring Plex webhook event for user: %s", user)
        return

    ids = _extract_external_ids(payload)
    logger.debug("TMDB Episode ID: %s", ids["tmdb_id"])
    logger.debug("IMDB Episode ID: %s", ids["imdb_id"])
    logger.debug("TVDB Episode ID: %s", ids["tvdb_id"])

    media_type = _get_media_type(payload)
    if not media_type:
        logger.info("Ignoring Plex webhook type: %s", payload["Metadata"].get("type"))
        return

    if not any(ids.values()):
        logger.info("Ignoring Plex webhook call because no ID was found.")
        return

    if media_type == MediaTypes.TV.value:
        _process_tv(payload, user, ids)
    elif media_type == MediaTypes.MOVIE.value:
        _process_movie(payload, user, ids)


def _is_supported_event(event_type):
    return event_type in ("media.scrobble", "media.play")


def _is_valid_user(payload, user):
    incoming_username = payload["Account"]["title"].strip().lower()
    
    stored_usernames = [
        u.strip().lower() for u in (user.plex_usernames or "").split(",") if u.strip()
    ]

    return incoming_username in stored_usernames


def _extract_external_ids(payload):
    guids = payload["Metadata"].get("Guid", [])

    def get_id(prefix):
        return next(
            (
                guid["id"].replace(f"{prefix}://", "")
                for guid in guids
                if guid["id"].startswith(f"{prefix}://")
            ),
            None,
        )

    return {
        "tmdb_id": get_id("tmdb"),
        "imdb_id": get_id("imdb"),
        "tvdb_id": get_id("tvdb"),
    }


def _get_media_type(payload):
    meta_type = payload["Metadata"].get("type")
    if meta_type == "episode":
        return MediaTypes.TV.value
    if meta_type == "movie":
        return MediaTypes.MOVIE.value
    return None


def _process_tv(payload, user, ids):
    title = payload["Metadata"]["grandparentTitle"]
    media_id = _find_tv_media_id(ids)
    if not media_id:
        logger.info("No TMDB ID found for TV show: %s", title)
        return
    logger.info("Detected TV show: %s", title)
    handle_tv_episode(media_id, payload, user)


def _find_tv_media_id(ids):
    for ext_id, ext_type in [(ids["imdb_id"], "imdb_id"), (ids["tvdb_id"], "tvdb_id")]:
        if ext_id:
            response = app.providers.tmdb.find(ext_id, ext_type)
            logger.debug(
                "%s response: %s",
                ext_type.upper(),
                json.dumps(response, indent=2),
            )
            if (
                response.get("tv_episode_results")
                and len(response["tv_episode_results"]) > 0
            ):
                return response["tv_episode_results"][0]["show_id"]
    return None


def _process_movie(payload, user, ids):
    title = payload["Metadata"]["title"]
    logger.info("Detected movie: %s", title)
    if ids["tmdb_id"]:
        handle_movie(ids["tmdb_id"], payload, user)
    elif ids["imdb_id"]:
        response = app.providers.tmdb.find(ids["imdb_id"], "imdb_id")
        media_id = (
            response["movie_results"][0]["id"]
            if response.get("movie_results")
            else None
        )
        if media_id:
            handle_movie(media_id, payload, user)
        else:
            logger.info("No TMDB ID found for movie: %s", title)
    else:
        logger.info("No TMDB ID or IMDB ID found for movie: %s", title)


def handle_movie(media_id, payload, user):
    """Handle movie object from payload."""
    movie_metadata = app.providers.tmdb.movie(media_id)
    movie_played = payload["event"] == "media.scrobble"
    progress = 1 if movie_played else 0
    now = timezone.now().replace(second=0, microsecond=0)

    # Get or create the movie item
    movie_item, _ = app.models.Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.MOVIE.value,
        defaults={
            "title": movie_metadata["title"],
            "image": movie_metadata["image"],
        },
    )

    # Get or create the movie instance
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
            # Always update end_date when movie is played
            movie_instance.end_date = now

            if movie_instance.status == Media.Status.COMPLETED.value:
                movie_instance.repeats += 1
            elif movie_instance.status == Media.Status.REPEATING.value:
                movie_instance.repeats += 1
                movie_instance.status = Media.Status.COMPLETED.value
            else:  # From IN_PROGRESS/PLANNING/PAUSED/DROPPED to COMPLETED
                movie_instance.status = Media.Status.COMPLETED.value
        elif movie_instance.status == Media.Status.COMPLETED.value:
            # Transition from COMPLETED to REPEATING
            movie_instance.status = Media.Status.REPEATING.value
            movie_instance.start_date = now  # Reset start date
            movie_instance.end_date = None  # Clear completion date
        elif movie_instance.status not in (
            Media.Status.REPEATING.value,
            Media.Status.IN_PROGRESS.value,
        ):
            # For other statuses (except REPEATING and IN_PROGRESS) set to IN_PROGRESS
            movie_instance.status = Media.Status.IN_PROGRESS.value
            if not movie_instance.start_date:
                movie_instance.start_date = now

        movie_instance.save()


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
