import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q

from app.models import MEDIA_TYPES, READABLE_MEDIA_TYPES, Item
from app.providers import services, tmdb
from events.models import Event

logger = logging.getLogger(__name__)

DEFAULT_MONTH_DAY = "-01-01"
DEFAULT_DAY = "-01"


@shared_task(name="Reload calendar")
def reload_calendar(user=None):  # , used for metadata
    """Refresh the calendar with latest dates for all users."""
    statuses = ["Planning", "In progress"]
    media_types_with_status = [media for media in MEDIA_TYPES if media != "episode"]

    query = Q()
    for media_type in media_types_with_status:
        query |= Q(**{f"{media_type}__status__in": statuses})

    items_with_status = Item.objects.filter(query).distinct()

    future_events = Event.objects.filter(date__gte=datetime.now(tz=settings.TZ))
    future_event_item_ids = set(future_events.values_list("item_id", flat=True))
    items_without_events = items_with_status.exclude(
        id__in=Event.objects.values_list("item_id", flat=True),
    )

    # Combine items with future events and items without any events
    items_to_process = items_with_status.filter(
        Q(id__in=future_event_item_ids) | Q(id__in=items_without_events),
    )

    events_bulk = []
    anime_to_process = []
    user_reloaded_items = []
    with transaction.atomic():
        # Delete all events related to items with at least one future event
        Event.objects.filter(item_id__in=future_event_item_ids).delete()
        for item in items_to_process:
            # anime can later be processed in bulk
            if item.media_type == "anime":
                anime_to_process.append(item)
            elif process_item(item, events_bulk):
                add_user_reloaded(item, user, user_reloaded_items)

        # process anime items in bulk
        process_anime_bulk(anime_to_process, events_bulk, user, user_reloaded_items)

        Event.objects.bulk_create(events_bulk)

    user_reloaded_count = len(user_reloaded_items)
    user_reloaded_msg = "\n".join(
        f"{item} ({READABLE_MEDIA_TYPES[item.media_type]})"
        for item in user_reloaded_items
    )

    if user_reloaded_count > 0:
        return f"""The following items have been reloaded for you:\n
                   {user_reloaded_msg}"""
    return "There have been no changes in your calendar"


def process_item(item, events_bulk):
    """Process each item and add events to the event list."""
    try:
        if item.media_type == "season":
            metadata = tmdb.season(item.media_id, item.season_number)
            reloaded = process_season(item, metadata, events_bulk)
        else:
            metadata = services.get_media_metadata(
                item.media_type,
                item.media_id,
                item.source,
            )
            reloaded = process_other(item, metadata, events_bulk)
    except requests.exceptions.HTTPError as err:
        # happens for niche media in which the mappings during import are incorrect
        if err.response.status_code == requests.codes.not_found:
            msg = f"{item} ({item.media_id}) not found on {item.source}. Deleting it."
            logger.warning(msg)
            item.delete()
            reloaded = False
        else:
            raise
    return reloaded


def process_anime_bulk(items, events_bulk, user, user_reloaded_items):
    """Process multiple anime items and add events to the event list."""
    anime_data = get_anime_schedule_bulk([item.media_id for item in items])

    for item in items:
        episodes = anime_data.get(item.media_id, [])
        for episode in episodes:
            air_date = datetime.fromtimestamp(episode["airingAt"], tz=ZoneInfo("UTC"))
            local_air_date = air_date.astimezone(settings.TZ)
            events_bulk.append(
                Event(
                    item=item,
                    episode_number=episode["episode"],
                    date=local_air_date,
                ),
            )
        if episodes:
            add_user_reloaded(item, user, user_reloaded_items)


def get_anime_schedule_bulk(media_ids):
    """Get the airing schedule for multiple anime items from AniList API."""
    all_data = {}
    page = 1

    while True:
        query = """
        query ($ids: [Int], $page: Int) {
          Page(page: $page) {
            pageInfo {
              hasNextPage
            }
            media(idMal_in: $ids, type: ANIME) {
              idMal
              startDate {
                year
                month
                day
              }
              airingSchedule {
                nodes {
                  episode
                  airingAt
                }
              }
            }
          }
        }
        """
        variables = {"ids": media_ids, "page": page}
        url = "https://graphql.anilist.co"
        response = services.api_request(
            "ANILIST",
            "POST",
            url,
            params={"query": query, "variables": variables},
        )

        media_list = response["data"]["Page"]["media"]

        for media in media_list:
            airing_schedule = media["airingSchedule"]["nodes"]

            # if no airing schedule is available, use the start date
            if not airing_schedule:
                timestamp = anilist_date_parser(media["startDate"])
                if timestamp:
                    airing_schedule = [
                        {
                            "episode": 1,
                            "airingAt": timestamp,
                        },
                    ]
                else:
                    airing_schedule = []

            all_data[media["idMal"]] = airing_schedule

        if not response["data"]["Page"]["pageInfo"]["hasNextPage"]:
            break

        page += 1

    return all_data


def process_season(item, metadata, events_bulk):
    """Process season item and add events to the event list."""
    for episode in reversed(metadata["episodes"]):
        if episode["air_date"]:
            try:
                air_date = date_parser(episode["air_date"])
                events_bulk.append(
                    Event(
                        item=item,
                        episode_number=episode["episode_number"],
                        date=air_date,
                    ),
                )
            except ValueError:
                pass
    return bool(metadata["episodes"])


def process_other(item, metadata, events_bulk):
    """Process other types of items and add events to the event list."""
    # it will have either of these keys
    date_keys = ["start_date", "release_date", "first_air_date"]
    for date_key in date_keys:
        if date_key in metadata["details"] and metadata["details"][date_key]:
            try:
                air_date = date_parser(metadata["details"][date_key])
                events_bulk.append(Event(item=item, date=air_date))
            except ValueError:
                return False
            else:
                return True

    return False


def add_user_reloaded(item, user, user_reloaded_items):
    """Add the item to the user reloaded list if the user is tracking it."""
    user_query = Q(**{f"{item.media_type}__user": user})
    if user and Item.objects.filter(user_query, id=item.id).exists():
        user_reloaded_items.append(item)


def date_parser(date_str):
    """Parse string in %Y-%m-%d to datetime. Raises ValueError if invalid."""
    year_only_parts = 1
    year_month_parts = 2
    # Preprocess the date string
    parts = date_str.split("-")
    if len(parts) == year_only_parts:
        date_str += DEFAULT_MONTH_DAY
    elif len(parts) == year_month_parts:
        # Year and month are provided, append "-01"
        date_str += DEFAULT_DAY

    # Parse the date string
    return datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=ZoneInfo("UTC"),
    )


def anilist_date_parser(start_date):
    """Parse the start date from AniList to a timestamp."""
    if not start_date["year"]:
        return None

    month = start_date["month"] or 1
    day = start_date["day"] or 1
    return datetime(start_date["year"], month, day, tzinfo=ZoneInfo("UTC")).timestamp()
