from contextlib import suppress

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from api.serializers.common import get_or_create_item_from_metadata, tracking_state
from app.models import BasicMedia, Book, MediaTypes, Status
from app.providers import services as provider_services


def get_tracking(user, *, source, media_type, media_id, season_number=None, episode_number=None):
    """Return a tracked media instance or None."""
    return BasicMedia.objects.filter_media_prefetch(
        user,
        media_id,
        media_type,
        source,
        season_number,
        episode_number,
    ).first()


def create_or_update_tracking(user, *, source, media_type, media_id, data, partial=True):
    """Create or update tracking using existing model behavior."""
    season_number = data.get("season_number")
    media = get_tracking(
        user,
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
    )
    if media is None:
        metadata = provider_services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number] if season_number is not None else None,
        )
        item = get_or_create_item_from_metadata(
            {
                "source": source,
                "media_type": media_type,
                "media_id": media_id,
                "season_number": season_number,
                "episode_number": None,
            },
            metadata,
        )
        model = apps.get_model("app", media_type)
        media = model(item=item, user=user)

    for api_field, model_field in {
        "status": "status",
        "rating": "score",
        "progress": "progress",
        "start_date": "start_date",
        "end_date": "end_date",
        "notes": "notes",
    }.items():
        if api_field in data and not (
            api_field == "progress" and media_type == MediaTypes.TV.value
        ):
            setattr(media, model_field, data[api_field])
    media.save()
    return media


def delete_tracking(user, *, source, media_type, media_id, season_number=None):
    """Delete tracked media if it exists."""
    media = get_tracking(
        user,
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
    )
    if media is not None:
        media.delete()


def consume_media(user, *, source, media_type, media_id, consumed_at=None):
    """Mark media consumed/completed."""
    media = get_tracking(user, source=source, media_type=media_type, media_id=media_id)
    if media is None:
        media = create_or_update_tracking(
            user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            data={"status": Status.COMPLETED.value},
        )
    media.end_date = consumed_at or timezone.now()
    media.mark_consumed()
    return media


def set_status(user, *, source, media_type, media_id, status):
    """Set status on an existing or new tracked item."""
    media = create_or_update_tracking(
        user,
        source=source,
        media_type=media_type,
        media_id=media_id,
        data={"status": status},
    )
    return media


def watch_episode(user, *, source, media_id, season_number, episode_number, watched_at=None):
    """Watch one TV episode using Season.watch."""
    with transaction.atomic():
        season = get_tracking(
            user,
            source=source,
            media_type=MediaTypes.SEASON.value,
            media_id=media_id,
            season_number=season_number,
        )
        if season is None:
            season = create_or_update_tracking(
                user,
                source=source,
                media_type=MediaTypes.SEASON.value,
                media_id=media_id,
                data={"season_number": season_number, "status": Status.IN_PROGRESS.value},
            )
        season.watch(episode_number, watched_at or timezone.now().replace(second=0, microsecond=0))
        season.refresh_from_db()
        return season


def unwatch_episode(user, *, source, media_id, season_number, episode_number):
    """Unwatch the latest matching TV episode."""
    season = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        season_number=season_number,
    )
    if season is not None:
        season.unwatch(episode_number)
        season.refresh_from_db()
    return season


def watch_season(user, *, source, media_id, season_number):
    """Mark a season watched."""
    season = create_or_update_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        data={"season_number": season_number, "status": Status.COMPLETED.value},
    )
    return season


def unwatch_season(user, *, source, media_id, season_number):
    """Remove watched episodes for a season and move it in progress."""
    season = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        season_number=season_number,
    )
    if season is not None:
        season.episodes.all().delete()
        season.status = Status.IN_PROGRESS.value
        season.save(update_fields=["status"])
    return season


def log_book_progress(user, *, source, media_id, progress_type, value, notes=""):
    """Log a book reading session."""
    book = get_tracking(user, source=source, media_type=MediaTypes.BOOK.value, media_id=media_id)
    if book is None:
        book = create_or_update_tracking(
            user,
            source=source,
            media_type=MediaTypes.BOOK.value,
            media_id=media_id,
            data={"status": Status.IN_PROGRESS.value},
        )
    if isinstance(book, Book):
        book.log_reading_session(progress_type, value, notes)
        book.refresh_from_db()
    return book


def serialize_tracking(media):
    """Serialize tracking state after annotating max progress when possible."""
    if media is None:
        return None
    if not hasattr(media, "max_progress"):
        with suppress(Exception):
            BasicMedia.objects.annotate_max_progress([media], media.item.media_type)
    return tracking_state(media)
