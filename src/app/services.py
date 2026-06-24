"""Services for diary and media consumption functionality."""
import logging
from django.utils import timezone
from django.db import transaction

from app.models import DiaryEntry, Item, Media, MediaLike
from app.tasks import update_daily_statistics

logger = logging.getLogger(__name__)


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
            # For movies, get or create a Movie tracking instance
            if item.media_type == 'movie':
                from app.models import Movie, Status
                movie_instance, created = Movie.objects.get_or_create(
                    item=item,
                    user=user,
                    defaults={
                        "status": Status.COMPLETED.value,
                        "end_date": consumed_at,
                    }
                )
                if not created:
                    movie_instance.mark_consumed()
            elif item.media_type == 'game':
                # For games, get or create a Game tracking instance
                from app.models import Game, Status
                game_instance, created = Game.objects.get_or_create(
                    item=item,
                    user=user,
                    defaults={
                        "status": Status.COMPLETED.value,
                        "end_date": consumed_at,
                    }
                )
                if not created:
                    game_instance.mark_consumed()
            else:
                # For other media types, try to find existing instance
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
