import logging

from app.providers import comicvine, services
from events.models import Event

from .helpers import date_parser

logger = logging.getLogger(__name__)


def process_comic(item, events_bulk):
    """Process comic item and add events to the event list."""
    logger.info("Fetching releases for %s", item)
    try:
        metadata = services.get_media_metadata(
            item.media_type,
            item.media_id,
            item.source,
        )
    except services.ProviderAPIError:
        logger.warning(
            "Failed to fetch metadata for %s",
            item,
        )
        return

    latest_event = Event.objects.filter(item=item).order_by("-datetime").first()
    last_issue_event_number = latest_event.content_number if latest_event else 0
    last_published_issue_number = metadata["max_issue_number"]
    if last_issue_event_number == last_published_issue_number:
        return

    try:
        issue_metadata = comicvine.issue(metadata["last_issue_id"])
    except services.ProviderAPIError:
        logger.warning(
            "Failed to fetch issue metadata for %s",
            item,
        )
        return

    if issue_metadata["store_date"]:
        issue_datetime = date_parser(issue_metadata["store_date"])
    elif issue_metadata["cover_date"]:
        issue_datetime = date_parser(issue_metadata["cover_date"])
    else:
        return

    events_bulk.append(
        Event(
            item=item,
            content_number=last_published_issue_number,
            datetime=issue_datetime,
        ),
    )
