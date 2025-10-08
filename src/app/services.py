"""Services for diary and media consumption functionality."""
import logging
from django.utils import timezone
from django.db import transaction

from app.models import DiaryEntry, Media, Item
from app.tasks import update_daily_statistics

logger = logging.getLogger(__name__)


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
) -> DiaryEntry:
    """
    Create a diary entry for a media item.
    
    Args:
        user: The user creating the entry
        item: The Item being logged
        consumed_at: When the item was consumed (defaults to now)
        rating: Optional rating (0-10)
        review: Optional review text
        auto_mark_consumed: Whether to also mark the media as consumed
    
    Returns:
        The created DiaryEntry instance
    """
    if consumed_at is None:
        consumed_at = timezone.now()

    with transaction.atomic():
        # Create the diary entry
        entry = DiaryEntry.objects.create(
            user=user,
            item=item,
            consumed_at=consumed_at,
            rating=rating,
            review=review,
            liked=liked,
            is_rewatch=is_rewatch,
        )
        
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
        "Created diary entry for %s by %s (auto_mark_consumed=%s)",
        item,
        user,
        auto_mark_consumed,
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