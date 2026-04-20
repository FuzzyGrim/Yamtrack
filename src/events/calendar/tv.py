import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.core.cache import cache
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from app.models import TV, Item, MediaTypes, Season, Status
from app.providers import services, tmdb
from events.models import Event

from .helpers import date_parser

logger = logging.getLogger(__name__)


def process_tv(tv_item, events_bulk):
    """Process TV item and create events for all seasons and episodes."""
    logger.info("Processing TV show: %s", tv_item)

    try:
        seasons_to_process = get_seasons_to_process(tv_item)

        if not seasons_to_process:
            logger.info("%s - No seasons need processing", tv_item)
            return

        processed_season_items = process_tv_seasons(
            tv_item,
            seasons_to_process,
            events_bulk,
        )
        reopen_completed_tv_with_new_seasons(
            tv_item,
            processed_season_items,
            events_bulk,
        )

    except services.ProviderAPIError:
        logger.warning(
            "Failed to fetch metadata for %s",
            tv_item,
        )
    except Exception:
        logger.exception("Error processing %s", tv_item)


def get_seasons_to_process(tv_item):
    """Identify which seasons of a TV show need to be processed."""
    tv_metadata = tmdb.tv(tv_item.media_id)

    if not tv_metadata.get("related", {}).get("seasons"):
        logger.warning("No seasons found for TV show: %s", tv_item)
        return []

    season_numbers = [
        season["season_number"] for season in tv_metadata["related"]["seasons"]
    ]

    if not season_numbers:
        logger.warning("No valid seasons found for TV show: %s", tv_item)
        return []

    next_episode_season = tv_metadata.get("next_episode_season")

    existing_season_events = Event.objects.filter(
        item__media_id=tv_item.media_id,
        item__source=tv_item.source,
        item__media_type=MediaTypes.SEASON.value,
    ).select_related("item")

    seasons_with_events = {event.item.season_number for event in existing_season_events}
    seasons_to_process = [
        season_num
        for season_num in season_numbers
        if season_num not in seasons_with_events
        or (next_episode_season and season_num >= next_episode_season)
    ]

    if not seasons_to_process:
        return []

    logger.info(
        "%s - Processing %d seasons (Next episode season: %s)",
        tv_item,
        len(seasons_to_process),
        next_episode_season,
    )

    return seasons_to_process


def process_tv_seasons(tv_item, seasons_to_process, events_bulk):
    """Process specific seasons of a TV show."""
    process_seasons_data = tmdb.tv_with_seasons(
        tv_item.media_id,
        seasons_to_process,
    )
    processed_season_items = []

    for season_number in seasons_to_process:
        season_key = f"season/{season_number}"
        if season_key not in process_seasons_data:
            logger.warning(
                "Season %s data not found for %s",
                season_number,
                tv_item,
            )
            continue

        season_metadata = process_seasons_data[season_key]

        season_item, _ = Item.objects.get_or_create(
            media_id=tv_item.media_id,
            source=tv_item.source,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_item.title,
                "image": season_metadata["image"],
            },
        )

        processed_season_items.append(season_item)
        process_season_episodes(season_item, season_metadata, events_bulk)

    return processed_season_items


def reopen_completed_tv_with_new_seasons(tv_item, season_items, events_bulk):
    """Reopen completed TV entries and create planning seasons when needed."""
    eligible_season_items = [
        season_item
        for season_item in season_items
        if season_item.season_number and season_item.season_number > 0
    ]
    season_item_map = {
        season_item.season_number: season_item for season_item in eligible_season_items
    }
    if not season_item_map:
        logger.info(
            "%s - No processed seasons eligible for completed-TV reopening",
            tv_item,
        )
        return

    # Only future season events should reopen a completed show; processed
    # past-only seasons can exist when local season tracking was incomplete.
    future_season_numbers = {
        event.item.season_number
        for event in events_bulk
        if event.item in eligible_season_items and event.datetime >= timezone.now()
    }
    if not future_season_numbers:
        logger.info(
            "%s - Processed seasons have no future events; "
            "skipping completed-TV reopening",
            tv_item,
        )
        return

    sorted_season_numbers = sorted(future_season_numbers)
    completed_tvs = list(
        TV.objects.filter(
            item=tv_item,
            status=Status.COMPLETED.value,
        )
        .select_related("user")
        .prefetch_related(
            Prefetch(
                "seasons",
                queryset=Season.objects.select_related("item"),
            ),
        ),
    )
    if not completed_tvs:
        logger.info("%s - No completed TV entries to reopen", tv_item)
        return

    logger.info(
        "%s - Checking %d completed TV entries against discovered seasons %s",
        tv_item,
        len(completed_tvs),
        sorted_season_numbers,
    )

    seasons_by_tv_id = {}
    tvs_to_update = []

    for tv in completed_tvs:
        existing_season_numbers = {
            season.item.season_number
            for season in tv.seasons.all()
            if season.item.season_number and season.item.season_number > 0
        }
        missing_season_numbers = [
            season_number
            for season_number in sorted_season_numbers
            if season_number not in existing_season_numbers
        ]

        if not missing_season_numbers:
            logger.info(
                "%s - User %s already tracks all discovered seasons",
                tv_item,
                tv.user,
            )
            continue

        logger.info(
            "%s - Reopening completed TV for user %s; new seasons: %s",
            tv_item,
            tv.user,
            missing_season_numbers,
        )
        seasons_by_tv_id[tv.id] = [
            Season(
                item=season_item_map[season_number],
                related_tv=tv,
                user=tv.user,
                status=Status.PLANNING.value,
            )
            for season_number in missing_season_numbers
        ]
        tv.status = Status.IN_PROGRESS.value
        tvs_to_update.append(tv)

    if not seasons_by_tv_id:
        logger.info("%s - No completed TV entries required reopening", tv_item)
        return

    with transaction.atomic():
        for tv in tvs_to_update:
            bulk_create_with_history(
                seasons_by_tv_id[tv.id],
                Season,
                default_user=tv.user,
            )
            bulk_update_with_history(
                [tv],
                TV,
                ["status"],
                default_user=tv.user,
            )
            logger.info(
                "%s - Reopened TV for user %s and created %d planning seasons",
                tv_item,
                tv.user,
                len(seasons_by_tv_id[tv.id]),
            )


def process_season_episodes(item, metadata, events_bulk):
    """Process episodes for a season and add them to events_bulk."""
    tvmaze_map = {}
    if metadata.get("tvdb_id"):
        logger.info(
            "%s - TVDB ID found, fetching TVMaze episode data",
            item,
        )
        tvmaze_map = get_tvmaze_episode_map(metadata["tvdb_id"])
    else:
        logger.warning(
            "%s - No TVDB ID found, skipping TVMaze episode data",
            item,
        )

    if not metadata.get("episodes"):
        logger.warning("%s - No episodes found in metadata", item)
        return

    for episode in metadata["episodes"]:
        episode_number = episode["episode_number"]
        season_number = metadata["season_number"]

        episode_datetime = get_episode_datetime(
            episode,
            season_number,
            episode_number,
            tvmaze_map,
        )

        events_bulk.append(
            Event(
                item=item,
                content_number=episode_number,
                datetime=episode_datetime,
            ),
        )


def get_episode_datetime(episode, season_number, episode_number, tvmaze_map):
    """Determine the most accurate air datetime for an episode."""
    tvmaze_key = f"{season_number}_{episode_number}"
    tvmaze_airstamp = tvmaze_map.get(tvmaze_key)

    if tvmaze_airstamp:
        return datetime.fromisoformat(tvmaze_airstamp)

    if episode["air_date"]:
        try:
            return date_parser(episode["air_date"])
        except ValueError:
            logger.warning(
                "Invalid air date for S%sE%s from TMDB: %s",
                season_number,
                episode_number,
                episode["air_date"],
            )

    return datetime.min.replace(tzinfo=ZoneInfo("UTC"))


def get_tvmaze_episode_map(tvdb_id):
    """Fetch and process episode data from TVMaze using TVDB ID with caching."""
    cache_key = f"tvmaze_map_{tvdb_id}"
    cached_map = cache.get(cache_key)

    if cached_map:
        logger.info("%s - Using cached TVMaze episode map", tvdb_id)
        return cached_map

    show_response = get_tvmaze_response(tvdb_id)
    tvmaze_map = {}

    if show_response:
        episodes = show_response["_embedded"]["episodes"]

        for episode in episodes:
            season_num = episode.get("season")
            episode_num = episode.get("number")
            if season_num is not None and episode_num is not None:
                key = f"{season_num}_{episode_num}"
                tvmaze_map[key] = episode.get("airstamp")

    cache.set(cache_key, tvmaze_map)
    logger.info(
        "%s - Cached TVMaze episode map with %d entries",
        tvdb_id,
        len(tvmaze_map),
    )

    return tvmaze_map


def get_tvmaze_response(tvdb_id):
    """Fetch episode data from TVMaze using TVDB ID."""
    lookup_url = f"https://api.tvmaze.com/lookup/shows?thetvdb={tvdb_id}"
    try:
        lookup_response = services.api_request("TVMaze", "GET", lookup_url)
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == requests.codes.not_found:
            logger.warning(
                "TVMaze lookup failed for TVDB ID %s - %s",
                tvdb_id,
                err.response.text,
            )
        else:
            logger.warning(
                "%s - TVMaze lookup error: %s",
                tvdb_id,
                err.response.text,
            )
        lookup_response = {}

    if not lookup_response:
        logger.warning("%s - No TVMaze lookup response for TVDB ID", tvdb_id)
        return {}

    tvmaze_id = lookup_response.get("id")

    if not tvmaze_id:
        logger.warning("%s - TVMaze ID not found for TVDB ID", tvdb_id)
        return {}

    show_url = f"https://api.tvmaze.com/shows/{tvmaze_id}?embed=episodes"

    try:
        return services.api_request("TVMaze", "GET", show_url)
    except requests.exceptions.HTTPError:
        return {}
