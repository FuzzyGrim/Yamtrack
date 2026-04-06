import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import MediaTypes, Sources
from app.providers import services
from events.models import Event

from .other import process_other

logger = logging.getLogger(__name__)


def anilist_date_parser(start_date):
    """Parse the start date from AniList to a timestamp."""
    if not start_date["year"]:
        return None

    month = start_date["month"] or 1
    day = start_date["day"] or 1

    dt = datetime(
        start_date["year"],
        month,
        day,
        hour=23,
        minute=59,
        second=59,
        microsecond=999999,
        tzinfo=ZoneInfo("UTC"),
    )

    return dt.timestamp()


def process_anime_bulk(items, events_bulk):
    """Process multiple anime items and add events to the event list."""
    if not items:
        return

    anime_data = get_anime_schedule_bulk([item.media_id for item in items])

    for item in items:
        episodes = anime_data.get(item.media_id)

        if episodes:
            for episode in episodes:
                if episode["airingAt"] is None:
                    episode_datetime = datetime.min.replace(tzinfo=ZoneInfo("UTC"))
                else:
                    episode_datetime = datetime.fromtimestamp(
                        episode["airingAt"],
                        tz=ZoneInfo("UTC"),
                    )
                events_bulk.append(
                    Event(
                        item=item,
                        content_number=episode["episode"],
                        datetime=episode_datetime,
                    ),
                )
        else:
            logger.info(
                "Anime: %s (%s), not proccesed by AniList",
                item.title,
                item.media_id,
            )
            process_other(item, events_bulk)


def get_anime_schedule_bulk(media_ids):
    """Get the airing schedule for multiple anime items from AniList API."""
    all_data = {}
    page = 1
    url = "https://graphql.anilist.co"
    query = """
    query ($ids: [Int], $page: Int) {
      Page(page: $page) {
        pageInfo {
          hasNextPage
        }
        media(idMal_in: $ids, type: ANIME) {
          idMal
          endDate {
            year
            month
            day
          }
          episodes
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

    while True:
        variables = {"ids": media_ids, "page": page}
        response = services.api_request(
            "ANILIST",
            "POST",
            url,
            params={"query": query, "variables": variables},
        )

        for media in response["data"]["Page"]["media"]:
            airing_schedule = media["airingSchedule"]["nodes"]
            total_episodes = media["episodes"]
            mal_id = str(media["idMal"])

            if not total_episodes:
                continue

            if airing_schedule:
                original_length = len(airing_schedule)
                airing_schedule = [
                    episode
                    for episode in airing_schedule
                    if episode["episode"] <= total_episodes
                ]

                if original_length > len(airing_schedule):
                    logger.info(
                        "Filtered episodes for MAL ID %s - keep only %s episodes",
                        mal_id,
                        total_episodes,
                    )

            if not airing_schedule or airing_schedule[-1]["episode"] < total_episodes:
                mal_metadata = services.get_media_metadata(
                    media_type=MediaTypes.ANIME.value,
                    media_id=mal_id,
                    source=Sources.MAL.value,
                )
                mal_total_episodes = mal_metadata["max_progress"]
                if mal_total_episodes and mal_total_episodes > total_episodes:
                    logger.info(
                        "MAL ID %s - MAL has %s episodes, AniList has %s",
                        mal_id,
                        mal_total_episodes,
                        total_episodes,
                    )
                    continue

                logger.info(
                    "Adding final episode for MAL ID %s - Ep %s",
                    mal_id,
                    total_episodes,
                )
                end_date_timestamp = anilist_date_parser(media["endDate"])
                airing_schedule.append(
                    {"episode": total_episodes, "airingAt": end_date_timestamp},
                )

            all_data[mal_id] = airing_schedule

        if not response["data"]["Page"]["pageInfo"]["hasNextPage"]:
            break
        page += 1

    return all_data
