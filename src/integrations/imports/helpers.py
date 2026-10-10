import base64
import datetime
import hashlib
import json
import logging
from collections import defaultdict

from cryptography.fernet import Fernet
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import app
from app.models import MediaTypes, Status

logger = logging.getLogger(__name__)

# Order in which an import can move the status of existing media forward.
STATUS_PROGRESSION = (
    Status.PLANNING.value,
    Status.IN_PROGRESS.value,
    Status.COMPLETED.value,
)

# Watches of the same movie or episode this close together are the same watch,
# as other sources like media server webhooks record it at a slightly different time.
WATCH_MATCH_TOLERANCE = datetime.timedelta(minutes=5)

# Fields an import in "new" mode can change on existing media.
EXISTING_MEDIA_UPDATE_FIELDS = {
    MediaTypes.TV.value: ["status", "score", "notes"],
    MediaTypes.SEASON.value: ["status", "score", "notes"],
    MediaTypes.MOVIE.value: [
        "status",
        "score",
        "notes",
        "progress",
        "progressed_at",
        "start_date",
        "end_date",
    ],
    MediaTypes.ANIME.value: [
        "status",
        "score",
        "notes",
        "progress",
        "progressed_at",
        "end_date",
    ],
}


class MediaImportError(Exception):
    """Custom exception for import errors."""


class MediaImportUnexpectedError(Exception):
    """Custom exception for unexpected import errors."""


def get_existing_media(user):
    """Get all existing media for the user to check against during import."""
    excluded_types = [MediaTypes.SEASON.value, MediaTypes.EPISODE.value]
    valid_types = [value for value in MediaTypes.values if value not in excluded_types]
    existing = defaultdict(lambda: defaultdict(dict))

    for media_type in valid_types:
        media_model = apps.get_model(app_label="app", model_name=media_type)

        for media in media_model.objects.filter(user=user).select_related("item"):
            existing[media_type][media.item.source].setdefault(
                media.item.media_id,
                media,
            )

    counts = [
        f"{media_type}: {sum(len(source_dict) for source_dict in media_dict.values())}"
        for media_type, media_dict in existing.items()
    ]
    logger.debug("Existing media for user %s: %s", user.username, ", ".join(counts))
    return existing


def get_existing_seasons(user):
    """Get the user's existing seasons by source, media ID and season number."""
    return {
        (season.item.source, season.item.media_id, season.item.season_number): season
        for season in app.models.Season.objects.filter(user=user).select_related(
            "item",
        )
    }


def get_watch_key(
    media_type, source, media_id, season_number=None, episode_number=None
):
    """Return the key that identifies a movie or episode in the existing watches."""
    return (media_type, source, str(media_id), season_number, episode_number)


def get_existing_watches(user):
    """Get the dates of the movie and episode watches the user already has.

    Used in "new" mode to import only the watches missing from Yamtrack, instead
    of skipping every movie or show that already exists.
    """
    watches = defaultdict(list)

    movies = app.models.Movie.objects.filter(user=user).values_list(
        "item__source",
        "item__media_id",
        "end_date",
    )
    for source, media_id, end_date in movies:
        watch_key = get_watch_key(MediaTypes.MOVIE.value, source, media_id)
        watches[watch_key].append(end_date)

    episodes = app.models.Episode.objects.filter(
        related_season__user=user,
    ).values_list(
        "item__source",
        "item__media_id",
        "item__season_number",
        "item__episode_number",
        "end_date",
    )
    for source, media_id, season_number, episode_number, end_date in episodes:
        watch_key = get_watch_key(
            MediaTypes.EPISODE.value,
            source,
            media_id,
            season_number,
            episode_number,
        )
        watches[watch_key].append(end_date)

    logger.debug("Existing watched items for user %s: %s", user.username, len(watches))
    return watches


def is_existing_watch(existing_watches, watch_key, end_date):
    """Return whether the user already has a watch at around the same date."""
    for existing_date in existing_watches.get(watch_key, ()):
        if existing_date is None or end_date is None:
            if existing_date == end_date:
                return True
        elif abs(existing_date - end_date) <= WATCH_MATCH_TOLERANCE:
            return True
    return False


def advance_status(media, status):
    """Move the status of existing media forward, return whether it changed.

    Statuses outside of the progression, like paused or dropped, were set by the
    user and are left untouched.
    """
    if media.status not in STATUS_PROGRESSION or status not in STATUS_PROGRESSION:
        return False

    if STATUS_PROGRESSION.index(status) <= STATUS_PROGRESSION.index(media.status):
        return False

    media.status = status
    return True


def reopen_status(media):
    """Move completed media back to in progress, return whether it changed.

    Used when existing media gets something that was never watched before, like
    an episode of a new season.
    """
    if media.status != Status.COMPLETED.value:
        return False

    media.status = Status.IN_PROGRESS.value
    return True


def fill_empty_fields(media, values):
    """Set the score and notes existing media is missing, return whether it changed."""
    changed = False
    for field in ("score", "notes"):
        value = values.get(field)
        if value in (None, "") or getattr(media, field) not in (None, ""):
            continue
        setattr(media, field, value)
        changed = True
    return changed


def should_process_media(existing_media, to_delete, media_type, source, media_id, mode):
    """Determine if a media item should be processed based on mode."""
    exists = media_id in existing_media[media_type][source]

    if mode == "new" and exists:
        # In "new" mode, skip if media already exists
        logger.debug(
            "Skipping existing %s: %s (mode: new)",
            media_type,
            media_id,
        )
        return False

    if mode == "overwrite" and exists:
        # In "overwrite" mode, add to the deletion list
        logger.debug(
            "Adding existing %s to deletion list: %s (mode: overwrite)",
            media_type,
            media_id,
        )
        to_delete[media_type][source].add(media_id)

    return True


def cleanup_existing_media(to_delete, user):
    """Delete existing media if in overwrite mode."""
    for media_type, sources in to_delete.items():
        if not sources:
            continue

        model = apps.get_model(app_label="app", model_name=media_type)
        total_deleted = 0

        for source, media_ids in sources.items():
            if not media_ids:
                continue

            deleted_count, _ = model.objects.filter(
                item__media_id__in=media_ids,
                item__source=source,
                user=user,
            ).delete()
            total_deleted += deleted_count

        if total_deleted > 0:
            logger.info(
                "Deleted %s %s objects for user %s in overwrite mode",
                total_deleted,
                media_type,
                user,
            )


def update_season_references(seasons, user):
    """Update season references with actual TV instances.

    When bulk_create skips existing TV shows, seasons would still reference
    the unsaved TV instances. This updates those references to point to
    the existing TV shows in the database, preventing the ValueError about
    unsaved related objects during bulk creation of seasons.
    """
    # Get existing TV shows from database
    existing_tv = {
        tv.item.media_id: tv
        for tv in app.models.TV.objects.filter(
            user=user,
            item__media_id__in=[season.item.media_id for season in seasons],
        )
    }

    # Update references
    for season in seasons:
        media_id = season.item.media_id
        if media_id in existing_tv:
            season.related_tv = existing_tv[media_id]
            logger.debug(
                "Updated new season %s with existing TV %s",
                season,
                existing_tv[media_id],
            )


def update_episode_references(episodes, user):
    """Update episode references with actual Season instances.

    When bulk_create skips existing seasons, episodes would still reference
    the unsaved season instances. This updates those references to point to
    the existing seasons in the database, preventing the ValueError about
    unsaved related objects during bulk creation of episodes.
    """
    # Create mapping of season instances
    existing_seasons = {
        (season.item.media_id, season.item.season_number): season
        for season in app.models.Season.objects.filter(
            user=user,
            item__media_id__in={episode.item.media_id for episode in episodes},
        )
    }

    # Update references
    for episode in episodes:
        season_key = (
            episode.item.media_id,
            episode.item.season_number,
        )
        if season_key in existing_seasons:
            episode.related_season = existing_seasons[season_key]
            logger.debug(
                "Updated new episode %s with existing season %s",
                episode,
                existing_seasons[season_key],
            )


def bulk_create_media(bulk_media_list, user):
    """Bulk create all media objects."""
    for media_type, bulk_media in bulk_media_list.items():
        if not bulk_media:
            continue

        model = apps.get_model(app_label="app", model_name=media_type)

        logger.info("Bulk importing %s", media_type)

        # Update references for seasons and episodes
        if media_type == MediaTypes.SEASON.value:
            logger.info("Updating references for season to existing TV shows")
            update_season_references(bulk_media, user)
        elif media_type == MediaTypes.EPISODE.value:
            logger.info(
                "Updating references for episodes to existing TV seasons",
            )
            update_episode_references(bulk_media, user)

        bulk_create_with_history(
            bulk_media,
            model,
            batch_size=500,
            default_user=user,
        )


def bulk_update_media(bulk_media_list, fields_by_media_type, user):
    """Bulk update media objects with history tracking."""
    for media_type, bulk_media in bulk_media_list.items():
        if not bulk_media:
            continue

        # Media tracked by primary key to update each instance only once
        if isinstance(bulk_media, dict):
            bulk_media = list(bulk_media.values())  # noqa: PLW2901

        fields = fields_by_media_type.get(media_type)
        if not fields:
            logger.warning(
                "Skipping bulk update for %s without configured fields",
                media_type,
            )
            continue

        model = apps.get_model(app_label="app", model_name=media_type)

        logger.info("Bulk updating %s", media_type)
        bulk_update_with_history(
            bulk_media,
            model,
            fields=fields,
            batch_size=500,
            default_user=user,
        )


def create_import_schedule(
    username,
    request,
    mode,
    frequency,
    import_time,
    source,
    *,
    task_kwargs=None,
):
    """Create an import schedule."""
    try:
        import_time = (
            datetime.datetime.strptime(import_time, "%H:%M")
            .astimezone(
                timezone.get_default_timezone(),
            )
            .time()
        )
    except ValueError:
        messages.error(request, "Invalid import time.")
        return

    task_name = f"Import from {source} for {username} at {import_time} {frequency}"
    if PeriodicTask.objects.filter(name=task_name).exists():
        messages.error(
            request,
            "The same import task is already scheduled.",
        )
        return

    crontab, _ = CrontabSchedule.objects.get_or_create(
        hour=import_time.hour,
        minute=import_time.minute,
        day_of_week="*" if frequency == "daily" else "*/2",
        timezone=timezone.get_default_timezone(),
    )

    kwargs = {
        "username": username,
        "user_id": request.user.id,
        "mode": mode,
    }

    if task_kwargs:
        kwargs.update(task_kwargs)

    # Create new periodic task
    PeriodicTask.objects.create(
        name=task_name,
        task=f"Import from {source}",
        crontab=crontab,
        kwargs=json.dumps(kwargs),
        start_time=timezone.now(),
    )
    messages.success(request, f"{source} import task scheduled.")


def join_with_commas_and(items):
    """Join a list of items with commas and 'and'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def fernet():
    """Derive a stable 32-byte key from Django's SECRET_KEY.

    Uses SHA-256 then urlsafe_b64encode to satisfy Fernet.
    """
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    """Return url-safe encrypted string."""
    return fernet().encrypt(value.encode()).decode()


def decrypt(token):
    """Decrypt value that was encrypted with `encrypt`."""
    return fernet().decrypt(token.encode()).decode()
