from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import MEDIA_TYPES, Item
from app.providers import services
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q

from events.models import Event


@shared_task(name="Reload calendar")
def reload_calendar(user): # noqa:ARG001, used for metadata
    """Refresh the calendar with latest dates."""
    statuses = ["Planning", "In progress"]
    media_types_with_status = [media for media in MEDIA_TYPES if media != "episode"]

    query = Q()
    for media_type in media_types_with_status:
        query |= Q(**{f"{media_type}__status__in": statuses})

    items = Item.objects.filter(query).distinct()
    date_limit = datetime.now(tz=settings.TZ) - timedelta(days=120)
    event_list = []

    with transaction.atomic():
        Event.objects.all().delete()
        for item in items:
            process_item(item, date_limit, event_list)

        Event.objects.bulk_create(event_list)

    return f"Reloaded {len(event_list)} calendar events."


def process_item(item, date_limit, event_list):
    """Process each item and add events to the event list."""
    season_number = item.season_number
    metadata = services.get_media_metadata(
        item.media_type,
        item.media_id,
        season_number,
    )

    # it will have either of these keys
    date_keys = ["start_date", "release_date", "first_air_date"]
    for date_key in date_keys:
        if date_key in metadata["details"]:
            if item.media_type == "anime":
                process_anime(
                    item,
                    metadata["details"][date_key],
                    date_limit,
                    metadata["max_progress"],
                    event_list,
                )
            elif item.media_type == "season":
                process_season(item, metadata["episodes"], date_limit, event_list)
            else:
                process_other(
                    item,
                    metadata["details"][date_key],
                    date_limit,
                    event_list,
                )


def process_anime(item, start_date_str, date_limit, max_progress, event_list):
    """Process anime item and add events to the event list."""
    if start_date_str:
        try:
            start_date = date_parser(start_date_str)
            if start_date > date_limit and max_progress:
                for i in range(1, max_progress):
                    event_list.append(
                        Event(item=item, episode_number=i, date=start_date),
                    )
                    start_date += timedelta(days=7)
        except ValueError:
            pass


def process_season(item, episodes, date_limit, event_list):
    """Process season item and add events to the event list."""
    for episode in episodes:
        if episode["air_date"]:
            try:
                air_date = date_parser(episode["air_date"])
                if air_date > date_limit:
                    event_list.append(
                        Event(
                            item=item,
                            episode_number=episode["episode_number"],
                            date=air_date,
                        ),
                    )
            except ValueError:
                pass


def process_other(item, air_date_str, date_limit, event_list):
    """Process other types of items and add events to the event list."""
    if air_date_str:
        try:
            air_date = date_parser(air_date_str)
            if air_date > date_limit:
                event_list.append(Event(item=item, date=air_date))
        except ValueError:
            pass


def date_parser(date_str):
    """Parse date string to datetime object."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("UTC"))
