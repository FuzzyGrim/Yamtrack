import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from app import tmdb_rating_sync
from app.models import UserMessage

logger = logging.getLogger(__name__)


@shared_task(name="Refresh TMDB ratings")
def refresh_tmdb_ratings(*, force=False):
    """Backfill and periodically refresh TMDB ratings on movie/TV/season items."""
    return tmdb_rating_sync.refresh_tmdb_ratings(force=force)


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
