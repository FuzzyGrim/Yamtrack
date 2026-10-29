import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import MediaTypes, Sources
from app.providers import services
from events.models import Event, SentinelDatetime

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
        hour=SentinelDatetime.HOUR,
        minute=SentinelDatetime.MINUTE,
        second=SentinelDatetime.SECOND,
        microsecond=SentinelDatetime.MICROSECOND,
        tzinfo=ZoneInfo("UTC"),
    )

    return dt.timestamp()


def process_anime_bulk(items, events_bulk):
    """Process multiple anime items and add events to the event list."""
    if not items:
        return

    anime_data = {}
    for source in (Sources.MAL.value, Sources.ANILIST.value):
        media_ids = [item.media_id for item in items if item.source == source]
        if media_ids:
            anime_data[source] = get_anime_schedule_bulk(media_ids, source)

    for item in items:
        episodes = anime_data.get(item.source, {}).get(item.media_id)

        if episodes:
            for episode in episodes:
                if episode["airingAt"] is None:
                    episode_datetime = SentinelDatetime.min_datetime()
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


def _collect_airing_schedule_pages(query, url, media_ids, source, page):
    media_by_id = {}
    airing_page = 1
    # AniList filters by null variables, so only the used id filter is sent
    ids_variable = "malIds" if source == Sources.MAL.value else "ids"
    id_field = "idMal" if source == Sources.MAL.value else "id"

    while True:
        variables = {
            ids_variable: media_ids,
            "page": page,
            "airingPage": airing_page,
        }
        response = services.api_request(
            Sources.ANILIST.value,
            "POST",
            url,
            params={"query": query, "variables": variables},
        )

        has_next_airing_page = False
        for media in response["data"]["Page"]["media"]:
            media_id = str(media[id_field])
            media_data = media_by_id.setdefault(
                media_id,
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


def _process_anilist_media_schedule(media_id, media, source):
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
                "Filtered episodes for %s ID %s - keep only %s episodes",
                Sources(source).label,
                media_id,
                total_episodes,
            )

    if (
        total_episodes is None
        or not airing_schedule
        or airing_schedule[-1]["episode"] < total_episodes
    ):
        # AniList can lag behind MAL on the episode count of MAL items
        if source == Sources.MAL.value:
            mal_total_episodes = _get_mal_total_episodes(media_id)

            if (
                mal_total_episodes
                and total_episodes
                and mal_total_episodes > total_episodes
            ):
                logger.info(
                    "MAL ID %s - MAL has %s episodes, AniList has %s",
                    media_id,
                    mal_total_episodes,
                    total_episodes,
                )
                return None

        if not total_episodes or (
            airing_schedule and airing_schedule[-1]["episode"] >= total_episodes
        ):
            return airing_schedule

        logger.info(
            "Adding final episode for %s ID %s - Ep %s",
            Sources(source).label,
            media_id,
            total_episodes,
        )
        end_date_timestamp = anilist_date_parser(media["endDate"])
        airing_schedule.append(
            {"episode": total_episodes, "airingAt": end_date_timestamp},
        )

    return airing_schedule


def get_anime_schedule_bulk(media_ids, source=Sources.MAL.value):
    """Get the airing schedule for multiple anime items from AniList API.

    The media IDs are MAL IDs or AniList IDs depending on the source.
    """
    all_data = {}
    page = 1
    url = "https://graphql.anilist.co"
    query = """
    query ($ids: [Int], $malIds: [Int], $page: Int, $airingPage: Int) {
      Page(page: $page) {
        pageInfo {
          hasNextPage
        }
        media(id_in: $ids, idMal_in: $malIds, type: ANIME) {
          id
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
            source,
            page,
        )

        for media_id, media in media_by_id.items():
            airing_schedule = _process_anilist_media_schedule(
                media_id,
                media,
                source,
            )
            if airing_schedule:
                all_data[media_id] = airing_schedule

        if not has_next_page:
            break
        page += 1

    return all_data
