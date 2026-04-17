import logging

from app.models import MediaTypes, Sources
from events.models import Event

from .anime import process_anime_bulk
from .comic import process_comic
from .other import process_other
from .selectors import get_items_to_process
from .tv import process_tv

logger = logging.getLogger(__name__)


def fetch_releases(user=None, items_to_process=None):
    """Fetch and process releases for the calendar."""
    if items_to_process and items_to_process[0].source == Sources.MANUAL.value:
        return "Manual sources are not processed"

    items_to_process = items_to_process or get_items_to_process(user)
    if not items_to_process:
        return "No items to process"

    events_bulk = process_items(items_to_process)
    items_updated = save_events(events_bulk)
    cleanup_invalid_events(events_bulk)

    return generate_final_message(items_to_process, items_updated)


def process_items(items_to_process):
    """Process items and categorize them."""
    events_bulk = []
    anime_to_process = []

    for item in items_to_process:
        if item.media_type == MediaTypes.ANIME.value:
            anime_to_process.append(item)
        elif item.media_type == MediaTypes.TV.value:
            process_tv(item, events_bulk)
        elif item.media_type == MediaTypes.COMIC.value:
            process_comic(item, events_bulk)
        else:
            process_other(item, events_bulk)

    process_anime_bulk(anime_to_process, events_bulk)
    return events_bulk


def save_events(events_bulk):
    """Save events in bulk with proper conflict handling."""
    items_updated = set()

    existing_events = Event.objects.filter(
        item__in=[event.item for event in events_bulk],
    ).select_related("item")

    existing_with_content = {
        (event.item_id, event.content_number): event
        for event in existing_events
        if event.content_number is not None
    }
    existing_without_content = {
        event.item_id: event
        for event in existing_events
        if event.content_number is None
    }

    to_create = []
    to_update = []

    for event in events_bulk:
        items_updated.add(event.item)

        if event.content_number is not None:
            key = (event.item_id, event.content_number)
            if key in existing_with_content:
                existing_event = existing_with_content[key]
                existing_event.datetime = event.datetime
                to_update.append(existing_event)
            else:
                to_create.append(event)
        elif event.item_id in existing_without_content:
            existing_event = existing_without_content[event.item_id]
            existing_event.datetime = event.datetime
            to_update.append(existing_event)
        else:
            to_create.append(event)

    if to_create:
        Event.objects.bulk_create(to_create)

    if to_update:
        Event.objects.bulk_update(to_update, ["datetime"])

    logger.info(
        "Successfully processed %d events (%d created, %d updated)",
        len(events_bulk),
        len(to_create),
        len(to_update),
    )

    return items_updated


def generate_final_message(items_to_process, items_updated):
    """Generate the final message summarizing the results."""
    processed_details = "\n".join(
        f"  - {item} ({item.get_media_type_display()})" for item in items_to_process
    )

    if items_updated:
        success_details = "\n".join(
            f"  - {item} ({item.get_media_type_display()})" for item in items_updated
        )
        return (
            f"Processed {len(items_to_process)} items:\n{processed_details}\n\n"
            f"Releases updated for {len(items_updated)} items:\n{success_details}"
        )

    return (
        f"Processed {len(items_to_process)} items:\n{processed_details}\n\n"
        f"No releases have been updated."
    )


def cleanup_invalid_events(events_bulk):
    """Remove events that are no longer valid based on updated items."""
    processed_items = {}

    for event in events_bulk:
        if event.content_number is not None:
            processed_items.setdefault(event.item.id, set()).add(event.content_number)

    all_events = Event.objects.filter(
        item_id__in=processed_items.keys(),
    ).select_related("item")

    events_to_delete = []

    for event in all_events:
        if (
            event.content_number is not None
            and event.item_id in processed_items
            and event.content_number not in processed_items[event.item_id]
        ):
            logger.info(
                "Invalid event detected: %s - Number %s (scheduled for %s)",
                event.item,
                event.content_number,
                event.datetime,
            )
            events_to_delete.append(event.id)

    if events_to_delete:
        deleted_count = Event.objects.filter(id__in=events_to_delete).delete()[0]
        logger.info("Deleted %s invalid events for updated items", deleted_count)
