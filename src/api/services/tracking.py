from contextlib import suppress
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from api.serializers.common import (
    get_or_create_item_from_metadata,
    progress_for_media,
    tracking_state,
)
from app.models import BasicMedia, Book, MediaTypes, Status
from app.providers import services as provider_services
from social.models import Activity, ProgressChange


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
    existing_media = media is not None
    previous_progress = _progress_snapshot(media) if existing_media and "progress" in data else None
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
    if previous_progress is not None:
        _record_progress_change(user, media, previous_progress)
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
        previous_progress = _progress_snapshot(season) if season is not None else None
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
        _record_progress_change(user, season, previous_progress)
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
        previous_progress = _progress_snapshot(season)
        season.unwatch(episode_number)
        season.refresh_from_db()
        _record_progress_change(user, season, previous_progress)
    return season


def watch_season(user, *, source, media_id, season_number):
    """Mark a season watched."""
    season = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        season_number=season_number,
    )
    previous_progress = _progress_snapshot(season) if season is not None else None
    season = create_or_update_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        data={"season_number": season_number, "status": Status.COMPLETED.value},
    )
    _record_progress_change(user, season, previous_progress)
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
        previous_progress = _progress_snapshot(season)
        season.episodes.all().delete()
        season.status = Status.IN_PROGRESS.value
        season.save(update_fields=["status"])
        _record_progress_change(user, season, previous_progress)
    return season


def log_book_progress(user, *, source, media_id, progress_type, value, notes=""):
    """Log a book reading session."""
    book = get_tracking(user, source=source, media_type=MediaTypes.BOOK.value, media_id=media_id)
    previous_progress = _progress_snapshot(book) if book is not None else None
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
        _record_progress_change(user, book, previous_progress)
    return book


def serialize_tracking(media):
    """Serialize tracking state after annotating max progress when possible."""
    if media is None:
        return None
    if not hasattr(media, "max_progress"):
        with suppress(Exception):
            BasicMedia.objects.annotate_max_progress([media], media.item.media_type)
    return tracking_state(media)


def _progress_snapshot(media):
    """Return the API progress shape for a tracked media item."""
    if media is None:
        return None
    if not hasattr(media, "max_progress"):
        with suppress(Exception):
            BasicMedia.objects.annotate_max_progress([media], media.item.media_type)
    return _jsonable(progress_for_media(media))


def _record_progress_change(user, media, previous_progress):
    """Record a durable progress delta and feed activity."""
    if media is None or previous_progress is None:
        return None
    current_progress = _progress_snapshot(media)
    if not current_progress or previous_progress == current_progress:
        return None
    change = ProgressChange.objects.create(
        actor=user,
        item=media.item,
        previous_progress=previous_progress,
        current_progress=current_progress,
    )
    Activity.objects.create(
        actor=user,
        verb="progress_updated",
        target_type="progress_change",
        target_id=change.id,
        item=media.item,
        snapshot={
            "previous": previous_progress,
            "current": current_progress,
        },
    )
    return change


def _jsonable(value):
    """Convert progress payloads to JSONField-safe primitives."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
