import logging

from celery import shared_task

from events import notifications
from events.calendar.main import fetch_releases

logger = logging.getLogger(__name__)


@shared_task(name="Reload calendar")
def reload_calendar(user=None, items_to_process=None):
    """Refresh the calendar with latest dates for all users."""
    if user:
        logger.info("Reloading calendar for user: %s", user.username)
    else:
        logger.info("Reloading calendar for all users")

    return fetch_releases(
        user=user,
        items_to_process=items_to_process,
    )


@shared_task(name="Send release notifications")
def send_release_notifications():
    """Send notifications for recently released media."""
    logger.info("Starting recent release notification task")

    return notifications.send_releases()


@shared_task(name="Send daily digest")
def send_daily_digest_notifications():
    """Send daily digest of today's releases."""
    logger.info("Starting daily digest task")

    return notifications.send_daily_digest()
