import json
import logging

from django.utils import timezone

import app
from app.models import Media, MediaTypes, Sources

logger = logging.getLogger(__name__)


def process_payload(payload, user):
    """Process a Emby webhook payload."""
    logger.debug(
        "Processing Emby webhook payload: %s",
        json.dumps(payload, indent=2),
    )

    event_type = payload["Event"]
    if not _is_supported_event(event_type):
        logger.info("Ignoring Emby webhook event type: %s", event_type)
        return

    ids = _extract_external_ids(payload)
    logger.debug("Extracted IDs from payload: %s", ids)

    if not any(ids.values()):
        logger.info("Ignoring Emby webhook call because no ID was found.")
        return

    media_type = _get_media_type(payload)
    if not media_type:
        logger.info("Ignoring Emby webhook type: %s", payload["Item"].get("Type"))
        return

    if media_type == MediaTypes.TV.value:
        _process_tv(payload, user, ids)
    elif media_type == MediaTypes.MOVIE.value:
        _process_movie(payload, user, ids)


def _process_movie(payload, user, ids):
    title = payload["Item"]["Name"]
    year = payload["Item"]["ProductionYear"]

    logger.info("Detected movie: %s (%d)", title, year)
    if ids["tmdb_id"]:
        logger.debug("Processing movie with TMDB ID: %s", ids["tmdb_id"])
        handle_movie(ids["tmdb_id"], payload, user)
    elif ids["imdb_id"]:
        logger.debug("Processing movie with IMDB ID: %s", ids["imdb_id"])
        response = app.providers.tmdb.find(ids["imdb_id"], "imdb_id")
        media_id = (
            response["movie_results"][0]["id"]
            if response.get("movie_results")
            else None
        )
        if media_id:
            logger.debug("TMDB ID found: %s", media_id)
            handle_movie(media_id, payload, user)

        else:
            logger.info("No matching TMDB ID found for movie: %s (%d)", title, year)
    else:
        logger.info("No TMDB ID or IMDB ID found for movie: %s (%d)", title, year)


def _process_tv(payload, user, ids):
    series_title = payload["Item"]["SeriesName"]
    episode_name = payload["Item"]["Name"]
    media_id, season_number, episode_number = _find_tv_media_id(ids)

    if not media_id:
        logger.info(
            "No TMDB ID found for TV show: %s S%02dE%02d - %s",
            series_title,
            int(season_number),
            int(episode_number),
            episode_name,
        )
        return

    if not season_number or not episode_number:
        logger.info(
            "Could not match TV show episode for %s S%02dE%02d - %s with TMDB ID %s",
            series_title,
            int(season_number),
            int(episode_number),
            episode_name,
            media_id,
        )
        return

    logger.info(
        "Detected TV show: %s S%02dE%02d - %s",
        series_title,
        int(season_number),
        int(episode_number),
        episode_name,
    )
    handle_tv_episode(media_id, season_number, episode_number, payload, user)


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
                result = response["tv_episode_results"][0]
                show_id = result.get("show_id")
                season_number = result.get("season_number")
                episode_number = result.get("episode_number")
                return show_id, season_number, episode_number
    return None


def _get_media_type(payload):
    meta_type = payload["Item"].get("Type")
    if meta_type == "Episode":
        return MediaTypes.TV.value
    if meta_type == "Movie":
        return MediaTypes.MOVIE.value
    return None


def _is_supported_event(event_type):
    return event_type in ("playback.start", "playback.stop")


def _extract_external_ids(payload):
    provider_ids = payload["Item"].get("ProviderIds", {})
    return {
        "tmdb_id": provider_ids.get("Tmdb"),
        "imdb_id": provider_ids.get("Imdb"),
        "tvdb_id": provider_ids.get("Tvdb"),
    }


def handle_movie(media_id, payload, user):
    """Handle movie object from payload."""
    movie_metadata = app.providers.tmdb.movie(media_id)
    movie_played = (
        payload.get("PlaybackInfo", {}).get("PlayedToCompletion", False) is True
    )
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


def handle_tv_episode(media_id, season_number, episode_number, payload, user):
    """Add a TV show episode as watched."""
    episode_name = payload["Item"]["Name"]
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

    if created:
        logger.info(
            "Created new TV instance for user %s: %s",
            user.username,
            tv_metadata["title"],
        )
    elif not created and tv_instance.status not in (
        Media.Status.COMPLETED.value,
        Media.Status.REPEATING.value,
        Media.Status.IN_PROGRESS.value,
    ):
        tv_instance.status = Media.Status.IN_PROGRESS.value
        tv_instance.save()
        logger.info(
            "Updated TV instance for user %s: %s (status: %s)",
            user.username,
            tv_metadata["title"],
            tv_instance.status,
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

    season_instance, created = app.models.Season.objects.get_or_create(
        item=season_item,
        user=user,
        related_tv=tv_instance,
        defaults={
            "status": Media.Status.IN_PROGRESS.value,
        },
    )

    if created:
        logger.info(
            "Created new season instance for user %s: %s S%d",
            user.username,
            tv_metadata["title"],
            season_number,
        )
    elif not created and season_instance.status not in (
        Media.Status.COMPLETED.value,
        Media.Status.REPEATING.value,
        Media.Status.IN_PROGRESS.value,
    ):
        season_instance.status = Media.Status.IN_PROGRESS.value
        season_instance.save()
        logger.info(
            "Updated season instance for user %s: %s %d (status: %s)",
            user.username,
            tv_metadata["title"],
            season_number,
            season_instance.status,
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

    episode_played = (
        payload.get("PlaybackInfo", {}).get("PlayedToCompletion", False) is True
    )

    if episode_played:
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

        logger.info(
            "Marked episode as played for user %s: %s S%02dE%02d - %s",
            user.username,
            tv_metadata["title"],
            int(season_number),
            int(episode_number),
            episode_name,
        )
