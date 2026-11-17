import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from app.models import Item, UserMessage
from app.providers import omdb
from app.ratings import refresh_item_rating, refresh_stale_ratings

logger = logging.getLogger(__name__)


@shared_task(name="Cleanup user messages")
def cleanup_user_messages():
    """Delete shown user messages older than the configured retention window."""
    cutoff = timezone.now() - timedelta(days=settings.USER_MESSAGE_RETENTION_DAYS)
    deleted_count, _ = UserMessage.objects.filter(
        shown_at__isnull=False,
        shown_at__lt=cutoff,
    ).delete()

    logger.info("Deleted %s old shown user messages.", deleted_count)

    return deleted_count


@shared_task(name="Refresh public ratings")
def refresh_public_ratings():
    """Refresh public ratings that are missing or older than seven days."""
    updated, deferred = refresh_stale_ratings()
    logger.info(
        "Refreshed public ratings for %s items (%s deferred).",
        updated,
        deferred,
    )
    return updated


@shared_task(name="Refresh item public rating")
def refresh_item_public_rating(item_id):
    """Refresh one item without blocking the page that queued it."""
    item = Item.objects.filter(pk=item_id).first()
    if item is None:
        return None
    try:
        return refresh_item_rating(item)
    except omdb.OmdbBudgetError:
        logger.info("OMDb daily limit reached, skipping item %s.", item_id)
        return None
