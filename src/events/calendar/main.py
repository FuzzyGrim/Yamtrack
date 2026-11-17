import logging

from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from events import release_marker
from events.models import Event

from .anime import process_anime_bulk
from .comic import process_comic
from .other import process_other
from .podcast import process_podcast
from .selectors import get_items_to_process
from .tv import process_tv
from .video import process_video

logger = logging.getLogger(__name__)


def fetch_releases(user=None, items_to_process=None):
    """Fetch and process releases for the calendar."""
    if items_to_process and items_to_process[0].source == Sources.MANUAL.value:
        return "Manual sources are not processed"

    if items_to_process is None:
        items_to_process = get_items_to_process(user)
    if not items_to_process:
        return "No items to process"

    events_bulk, checked_items = process_items(items_to_process)
    items_updated = save_events(events_bulk)
    cleanup_invalid_events(events_bulk)
    record_calendar_checks(checked_items)

    return generate_final_message(items_to_process, items_updated)


def process_items(items_to_process):
    """Process items and categorize them.

    Returns (events_bulk, checked_items), where checked_items are the items whose
    processor reported success. Items whose provider call failed are left out so
    they are not marked as checked and get retried on the next reload.
    """
    events_bulk = []
    anime_to_process = []
    checked_items = []

    for item in items_to_process:
        if item.media_type == MediaTypes.ANIME.value:
            anime_to_process.append(item)
            continue

        if item.media_type == MediaTypes.TV.value:
            checked = process_tv(item, events_bulk)
        elif item.media_type == MediaTypes.COMIC.value:
            checked = process_comic(item, events_bulk)
        elif item.media_type == MediaTypes.PODCAST.value:
            checked = process_podcast(item, events_bulk)
        elif item.media_type == MediaTypes.VIDEO.value:
            checked = process_video(item, events_bulk)
        else:
            checked = process_other(item, events_bulk)

        if checked:
            checked_items.append(item)

    checked_items.extend(process_anime_bulk(anime_to_process, events_bulk))
    return events_bulk, checked_items


def record_calendar_checks(checked_items):
    """Record when each successfully checked item was last looked at.

    Written as one bulk UPDATE per call: on SQLite the single writer is the
    contended resource, and calendar reloads are chunked, so each chunk stamps
    only its own items and an interrupted reload keeps the progress it made.
    """
    item_ids = [item.id for item in checked_items if getattr(item, "id", None)]
    if not item_ids:
        return

    Item.objects.filter(id__in=item_ids).update(calendar_checked_at=timezone.now())


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
        (event.item_id, event.release_type, event.region): event
        for event in existing_events
        if event.content_number is None
    }

    to_create = []
    to_update = []
    moved = False

    for event in events_bulk:
        items_updated.add(event.item)

        if event.content_number is not None:
            key = (event.item_id, event.content_number)
            if key in existing_with_content:
                existing_event = existing_with_content[key]
                moved = moved or existing_event.datetime != event.datetime
                existing_event.datetime = event.datetime
                to_update.append(existing_event)
            else:
                to_create.append(event)
        elif (key := (event.item_id, event.release_type, event.region)) in (
            existing_without_content
        ):
            existing_event = existing_without_content[key]
            moved = moved or existing_event.datetime != event.datetime
            existing_event.datetime = event.datetime
            to_update.append(existing_event)
        else:
            to_create.append(event)

    if to_create:
        Event.objects.bulk_create(to_create)

    if to_update:
        Event.objects.bulk_update(to_update, ["datetime"])

    if to_create or moved:
        release_marker.bump()

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

    events_to_delete.extend(stale_release_type_event_ids(events_bulk))

    if events_to_delete:
        deleted_count = Event.objects.filter(id__in=events_to_delete).delete()[0]
        release_marker.bump()
        logger.info("Deleted %s invalid events for updated items", deleted_count)


def stale_release_type_event_ids(events_bulk):
    """Return ids of digital/physical events a refreshed movie no longer has.

    That covers a date TMDB removed and a user who changed region or stopped
    tracking. Movies the refresh skipped are left alone.
    """
    movie_item_ids = {
        event.item_id
        for event in events_bulk
        if event.content_number is None
        and not event.release_type
        and event.item.media_type == MediaTypes.MOVIE.value
    }
    kept = {
        (event.item_id, event.release_type, event.region)
        for event in events_bulk
        if event.release_type
    }
    return [
        event_id
        for event_id, item_id, release_type, region in Event.objects.filter(
            item_id__in=movie_item_ids,
        )
        .exclude(release_type="")
        .values_list("id", "item_id", "release_type", "region")
        if (item_id, release_type, region) not in kept
    ]
