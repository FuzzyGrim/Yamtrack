"""Services for diary and media consumption functionality."""
import logging
from django.utils import timezone
from django.db import transaction

from app.models import DiaryEntry, Item, Media, MediaLike, MediaTypes, Status
from app.tasks import update_daily_statistics

logger = logging.getLogger(__name__)


COMPLETABLE_MEDIA_MODELS = {
    MediaTypes.MOVIE.value: "Movie",
    MediaTypes.GAME.value: "Game",
    MediaTypes.ANIME.value: "Anime",
    MediaTypes.MANGA.value: "Manga",
    MediaTypes.COMIC.value: "Comic",
    MediaTypes.BOOK.value: "Book",
}


def _media_model(media_type):
    model_name = COMPLETABLE_MEDIA_MODELS.get(media_type)
    if not model_name:
        return None
    from django.apps import apps

    return apps.get_model("app", model_name)


def _add_tags_to_entry(entry, tag_names):
    """Add tags to a diary entry."""
    from app.models import Tag, DiaryEntryTag
    
    for tag_name in tag_names:
        tag_name = tag_name.strip().lower()
        if not tag_name:
            continue
            
        # Get or create the tag
        tag, created = Tag.objects.get_or_create(name=tag_name)
        
        # Create the relationship if it doesn't exist
        DiaryEntryTag.objects.get_or_create(
            diary_entry=entry,
            tag=tag
        )


def update_diary_entry_tags(entry, tag_names):
    """Update tags for a diary entry."""
    # Clear existing tags
    entry.tags.clear()
    
    # Add new tags
    if tag_names:
        _add_tags_to_entry(entry, tag_names)


def set_media_like(user, item: Item, liked: bool, *, audit=True, sync_diary=True):
    """Set the canonical user/title like."""
    if liked:
        media_like, created = MediaLike.objects.get_or_create(user=user, item=item)
        action = "media_like"
    else:
        deleted, _ = MediaLike.objects.filter(user=user, item=item).delete()
        media_like = None
        created = bool(deleted)
        action = "media_unlike"

    if sync_diary:
        DiaryEntry.objects.filter(user=user, item=item).exclude(liked=liked).update(liked=liked)

    if audit and created:
        from social.models import SocialAuditLog

        SocialAuditLog.objects.create(
            actor=user,
            action=action,
            target_type="item",
            target_id=item.id,
        )
    return media_like


def create_diary_entry(
    user,
    item: Item,
    *,
    consumed_at=None,
    rating=None,
    review="",
    liked=False,
    is_rewatch=False,
    auto_mark_consumed=False,
    tags=None,
) -> DiaryEntry:
    """
    Create a diary entry for a media item.
    
    Args:
        user: The user creating the entry
        item: The Item being logged
        consumed_at: When the item was consumed (defaults to now)
        rating: Optional rating (0-10)
        review: Optional review text
        liked: Whether the user liked the item
        is_rewatch: Whether this is a rewatch
        auto_mark_consumed: Whether to also mark the media as consumed
        tags: List of tag names to attach to the entry
    
    Returns:
        The created DiaryEntry instance
    """
    if consumed_at is None:
        consumed_at = timezone.now()
    
    if tags is None:
        tags = []

    with transaction.atomic():
        # Get progress snapshot for games
        progress_snapshot = None
        if item.media_type == 'game':
            from app.models import Game
            game_instance = Game.objects.filter(user=user, item=item).first()
            if game_instance:
                # Store playtime in minutes in progress_snapshot
                progress_snapshot = {
                    "playtime_minutes": game_instance.progress,
                    "formatted_playtime": game_instance.formatted_progress,
                }
        
        title_liked = liked or MediaLike.objects.filter(user=user, item=item).exists()

        # Create the diary entry
        entry = DiaryEntry.objects.create(
            user=user,
            item=item,
            consumed_at=consumed_at,
            rating=rating,
            review=review,
            liked=title_liked,
            is_rewatch=is_rewatch,
            progress_snapshot=progress_snapshot,
        )

        if liked:
            set_media_like(user, item, True)
        
        # Add tags to the entry
        if tags:
            _add_tags_to_entry(entry, tags)
        
        # Optionally mark as consumed
        if auto_mark_consumed:
            model = _media_model(item.media_type)
            if model:
                media_instance, created = model.objects.get_or_create(
                    item=item,
                    user=user,
                    defaults={
                        "status": Status.COMPLETED.value,
                        "end_date": consumed_at,
                    }
                )
                if not created:
                    media_instance.mark_consumed()
                    media_instance.end_date = consumed_at
                    media_instance.save(update_fields=["end_date"])
            elif item.media_type == MediaTypes.EPISODE.value:
                from app.models import Episode

                Episode.objects.filter(item=item, related_season__user=user).update(end_date=consumed_at)
            else:
                # For TV/season, keep the existing web flow responsible for cascade behavior.
                from app.models import BasicMedia
                media_instance = BasicMedia.objects.filter(user=user, item=item).first()
                if media_instance:
                    media_instance.mark_consumed()
            
        # Queue statistics update
        transaction.on_commit(lambda: update_daily_statistics.delay(
            user_id=user.id,
            date_str=consumed_at.isoformat(),
        ))
        
    logger.info(
        "Created diary entry for %s by %s (auto_mark_consumed=%s, tags=%s)",
        item,
        user,
        auto_mark_consumed,
        tags,
    )
    return entry


def sync_tracking_from_diary_entry(entry, *, previous_consumed_at=None):
    """Sync completed tracking dates from a diary entry date edit."""
    if previous_consumed_at == entry.consumed_at:
        return

    if entry.item.media_type == MediaTypes.EPISODE.value:
        from app.models import Episode

        Episode.objects.filter(item=entry.item, related_season__user=entry.user).update(end_date=entry.consumed_at)
    else:
        model = _media_model(entry.item.media_type)
        if model:
            media_instance = model.objects.filter(user=entry.user, item=entry.item).first()
            if media_instance and (
                media_instance.status == Status.COMPLETED.value
                or (
                    entry.item.media_type == MediaTypes.BOOK.value
                    and media_instance.completion_diary_entry_id == entry.id
                )
            ):
                media_instance.end_date = entry.consumed_at
                media_instance.save(update_fields=["end_date"])

    transaction.on_commit(lambda: update_daily_statistics.delay(
        user_id=entry.user_id,
        date_str=entry.consumed_at.isoformat(),
    ))


def update_diary_entry(entry, data, *, tags=None):
    """Update a diary entry and keep title-level state in sync."""
    from social.models import Activity, SocialAuditLog

    previous_consumed_at = entry.consumed_at
    snapshot = {}
    with transaction.atomic():
        for field in [
            "consumed_at",
            "rating",
            "review",
            "review_title",
            "liked",
            "is_rewatch",
            "contains_spoilers",
            "visibility",
        ]:
            if field not in data:
                continue
            old_value = getattr(entry, field)
            new_value = data[field]
            setattr(entry, field, new_value)
            if old_value != new_value and field in {"rating", "consumed_at"}:
                if field == "consumed_at":
                    snapshot[field] = new_value.isoformat()
                else:
                    snapshot[field] = str(new_value) if new_value is not None else None
        entry.save()
        if "consumed_at" in data:
            sync_tracking_from_diary_entry(entry, previous_consumed_at=previous_consumed_at)
        if "liked" in data:
            set_media_like(entry.user, entry.item, data["liked"])
        if tags is not None:
            update_diary_entry_tags(entry, tags)
        Activity.objects.create(
            actor=entry.user,
            verb="diary_updated",
            target_type="diary",
            target_id=entry.id,
            item=entry.item,
            visibility=entry.visibility,
            snapshot=snapshot,
        )
        SocialAuditLog.objects.create(
            actor=entry.user,
            action="diary_updated",
            target_type="diary",
            target_id=entry.id,
            metadata=snapshot,
        )
    return entry


def delete_diary_entry(user, entry):
    """Delete a diary entry and mirror web tracking side effects."""
    from social.models import Activity, SocialAuditLog
    from app.models import Book, Movie, Season, TV

    item = entry.item
    book_instance = None
    book_completion_entry = False
    if item.media_type == MediaTypes.BOOK.value:
        book_instance = Book.objects.filter(user=user, item=item).first()
        book_completion_entry = bool(
            book_instance and book_instance.completion_diary_entry_id == entry.id
        )

    snapshot = {
        "rating": str(entry.rating) if entry.rating is not None else None,
        "consumed_at": entry.consumed_at.isoformat(),
    }
    with transaction.atomic():
        Activity.objects.create(
            actor=user,
            verb="diary_deleted",
            target_type="diary",
            target_id=entry.id,
            item=item,
            visibility=entry.visibility,
            snapshot=snapshot,
        )
        SocialAuditLog.objects.create(
            actor=user,
            action="diary_deleted",
            target_type="diary",
            target_id=entry.id,
            metadata=snapshot,
        )
        entry.delete()

        remaining_entries = DiaryEntry.objects.filter(user=user, item=item).exists()
        if not remaining_entries:
            if item.media_type == MediaTypes.MOVIE.value:
                Movie.objects.filter(user=user, item=item).delete()
            elif item.media_type == MediaTypes.TV.value:
                tv_instance = TV.objects.filter(user=user, item=item).first()
                if tv_instance:
                    for season in tv_instance.seasons.all():
                        season.episodes.all().delete()
                        season.delete()
                    tv_instance.delete()
            elif item.media_type == MediaTypes.SEASON.value:
                season_instance = Season.objects.filter(user=user, item=item).first()
                if season_instance:
                    season_instance.episodes.all().delete()
                    season_instance.delete()
            elif item.media_type == MediaTypes.BOOK.value and book_instance and not book_instance.completed_manually:
                book_instance.delete()
        elif item.media_type == MediaTypes.BOOK.value and book_instance and book_completion_entry:
            book_instance.completion_diary_entry = None
            book_instance.save(update_fields=["completion_diary_entry"])


def mark_consumed(user, media_instance: Media, when=None):
    """
    Mark a media item as consumed without creating a diary entry.
    
    Args:
        user: The user marking the item
        media_instance: The Media instance to mark
        when: Optional datetime for when it was consumed (defaults to now)
    """
    if when is None:
        when = timezone.now()
        
    with transaction.atomic():
        # Set the end date explicitly before marking consumed
        media_instance.end_date = when
        media_instance.mark_consumed()
        
        # Queue statistics update
        transaction.on_commit(lambda: update_daily_statistics.delay(
            user_id=user.id,
            date_str=when.isoformat(),
        ))
    
    logger.info("Marked %s as consumed by %s", media_instance, user)
