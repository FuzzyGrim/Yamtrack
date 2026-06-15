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


def _collect_airing_schedule_pages(query, url, media_ids, page):
    media_by_id = {}
    airing_page = 1

    while True:
        variables = {
            "ids": media_ids,
            "page": page,
            "airingPage": airing_page,
        }
        response = services.api_request(
            "ANILIST",
            "POST",
            url,
            params={"query": query, "variables": variables},
        )

        has_next_airing_page = False
        for media in response["data"]["Page"]["media"]:
            mal_id = str(media["idMal"])
            media_data = media_by_id.setdefault(
                mal_id,
                {
                    "endDate": media["endDate"],
                    "episodes": media["episodes"],
                    "airingSchedule": [],
                },
            )
            media_data["airingSchedule"].extend(
                media["airingSchedule"]["nodes"],
            )
            has_next_airing_page = has_next_airing_page or media["airingSchedule"].get(
                "pageInfo", {}
            ).get("hasNextPage", False)

        if not has_next_airing_page:
            break
        airing_page += 1

    return media_by_id, response["data"]["Page"]["pageInfo"]["hasNextPage"]


def _get_mal_total_episodes(mal_id):
    mal_metadata = services.get_media_metadata(
        media_type=MediaTypes.ANIME.value,
        media_id=mal_id,
        source=Sources.MAL.value,
    )
    return mal_metadata["max_progress"]


def _process_anilist_media_schedule(mal_id, media):
    airing_schedule = media["airingSchedule"]
    total_episodes = media["episodes"]

    if not total_episodes and not airing_schedule:
        return None

    if airing_schedule and total_episodes:
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

    if (
        total_episodes is None
        or not airing_schedule
        or airing_schedule[-1]["episode"] < total_episodes
    ):
        mal_total_episodes = _get_mal_total_episodes(mal_id)

        if (
            mal_total_episodes
            and total_episodes
            and mal_total_episodes > total_episodes
        ):
            logger.info(
                "MAL ID %s - MAL has %s episodes, AniList has %s",
                mal_id,
                mal_total_episodes,
                total_episodes,
            )
            return None

        if not total_episodes or (
            airing_schedule and airing_schedule[-1]["episode"] >= total_episodes
        ):
            return airing_schedule

        logger.info(
            "Adding final episode for MAL ID %s - Ep %s",
            mal_id,
            total_episodes,
        )
        end_date_timestamp = anilist_date_parser(media["endDate"])
        airing_schedule.append(
            {"episode": total_episodes, "airingAt": end_date_timestamp},
        )

    return airing_schedule


def get_anime_schedule_bulk(media_ids):
    """Get the airing schedule for multiple anime items from AniList API."""
    all_data = {}
    page = 1
    url = "https://graphql.anilist.co"
    query = """
    query ($ids: [Int], $page: Int, $airingPage: Int) {
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
          airingSchedule(page: $airingPage) {
            pageInfo {
              hasNextPage
            }
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
        media_by_id, has_next_page = _collect_airing_schedule_pages(
            query,
            url,
            media_ids,
            page,
        )

        for mal_id, media in media_by_id.items():
            airing_schedule = _process_anilist_media_schedule(mal_id, media)
            if airing_schedule:
                all_data[mal_id] = airing_schedule

        if not has_next_page:
            break
        page += 1

    return all_data
