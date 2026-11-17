"""Shared rules for hiding unreleased media and episodes."""

from datetime import date, datetime

from django.utils import timezone

from app.models import BasicMedia, MediaTypes
from events.models import Event

YEAR_ONLY_PARTS = 1
YEAR_MONTH_PARTS = 2
IN_PROGRESS_STATUS = "In progress"


def is_released(air_date, current_date=None):
    """Return whether the supplied air date is today or earlier.

    Missing and invalid values are not released. A date after today is not
    released.
    """
    current_date = current_date or timezone.localdate()
    normalized_air_date = None

    if isinstance(air_date, datetime):
        if timezone.is_naive(air_date):
            normalized_air_date = air_date.date()
        else:
            normalized_air_date = timezone.localtime(air_date).date()
    elif isinstance(air_date, date):
        normalized_air_date = air_date
    elif isinstance(air_date, str):
        parts = air_date.split("-")
        if len(parts) == YEAR_ONLY_PARTS:
            air_date = f"{air_date}-01-01"
        elif len(parts) == YEAR_MONTH_PARTS:
            air_date = f"{air_date}-01"

        try:
            normalized_air_date = date.fromisoformat(air_date)
        except ValueError:
            return False
    else:
        return False

    return normalized_air_date <= current_date


def _air_date(episode):
    """Return a provider air date, or None when the episode has none."""
    air_date = episode.get("air_date")
    if air_date in (None, ""):
        return None
    return air_date


def is_released_episode(episode, later_episodes, current_date=None):
    """Return whether an episode should stay visible.

    A future air date is hidden. A missing air date is hidden when the episode
    is still in the future, which means no later episode in the season has
    already aired. An undated episode that sits before an aired later episode
    stays visible.
    """
    air_date = _air_date(episode)
    if air_date is not None:
        return is_released(air_date, current_date)

    for later in later_episodes:
        later_date = _air_date(later)
        if later_date is not None and is_released(later_date, current_date):
            return True
    return False


def released_episodes(episodes, current_date=None):
    """Return episodes that are already released."""
    ordered = sorted(episodes, key=lambda episode: episode.get("episode_number") or 0)
    visible = []
    for index, episode in enumerate(ordered):
        if is_released_episode(episode, ordered[index + 1 :], current_date):
            visible.append(episode)
    return visible


def released_seasons(seasons, keep_season_number=None, current_date=None):
    """Drop seasons whose first air date is missing or still in the future.

    ``keep_season_number`` stays in the list so the season being viewed does
    not disappear from the switcher.
    """
    visible = []
    for season in seasons:
        if (
            keep_season_number is not None
            and season.get("season_number") == keep_season_number
        ):
            visible.append(season)
            continue
        if is_released(season.get("first_air_date"), current_date):
            visible.append(season)
    return visible


def is_visible_media(media, status):
    """Return whether a tracked title stays visible when hiding unreleased.

    ``media.next_event`` is the next future release, including the far-future
    sentinel used when a future episode has no air date. In-progress titles
    stay visible while released backlog remains.
    """
    upcoming = getattr(media, "next_event", None) is not None
    if status == IN_PROGRESS_STATUS:
        if not upcoming:
            return True
        max_progress = media.max_progress
        return max_progress is not None and media.progress < max_progress

    return not upcoming


def is_visible_tv(tv):
    """Return whether the show's latest season still has released episodes.

    Earlier seasons do not keep the show on the list once the latest season
    has not premiered, or the viewer is caught up and only unreleased
    episodes remain.
    """
    seasons = [season for season in tv.seasons.all() if season.item.season_number != 0]
    if not seasons:
        return True
    last_season = max(seasons, key=lambda season: season.item.season_number)
    return is_visible_media(last_season, last_season.status)


def filter_tracked_media(media_items, media_type):
    """Drop tracked items that are still unreleased."""
    media_items = list(media_items)
    if media_type == MediaTypes.TV.value:
        _annotate_tv_seasons(media_items)
        visible = [media for media in media_items if is_visible_tv(media)]
        BasicMedia.objects.annotate_max_progress(visible, media_type)
        return visible

    _attach_prefetched_events(media_items)
    BasicMedia.objects.annotate_max_progress(media_items, media_type)
    BasicMedia.objects._annotate_next_event(media_items)
    return [media for media in media_items if is_visible_media(media, media.status)]


def _annotate_tv_seasons(tv_list):
    """Attach release state to each tracked season of the given shows."""
    seasons = [
        season
        for tv in tv_list
        for season in tv.seasons.all()
        if season.item.season_number != 0
    ]
    if not seasons:
        return

    _attach_prefetched_events(seasons)
    BasicMedia.objects.annotate_max_progress(seasons, MediaTypes.SEASON.value)
    BasicMedia.objects._annotate_next_event(seasons)


def _attach_prefetched_events(media_objects):
    """Ensure each item has ``prefetched_events`` for next-release lookup."""
    missing = [
        media for media in media_objects if not hasattr(media.item, "prefetched_events")
    ]
    if not missing:
        return

    grouped = {}
    item_ids = [media.item_id for media in missing]
    for event in Event.objects.filter(item_id__in=item_ids):
        grouped.setdefault(event.item_id, []).append(event)

    for media in missing:
        media.item.prefetched_events = grouped.get(media.item_id, [])
