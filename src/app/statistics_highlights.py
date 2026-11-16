"""History highlight card building: first/last play, today-in-history, today releases."""

import logging
import random
from collections import defaultdict
from datetime import date, datetime

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.db.models.functions import ExtractDay, ExtractMonth
from django.utils import timezone

from app import backdrops, history_cache
from app import statistics as stats
from app.models import MediaTypes

logger = logging.getLogger(__name__)


def _history_entry_card_payload(entry):
    if not entry:
        return None
    item = entry.get("item")
    if not item:
        return None
    played_at = entry.get("played_at_local")
    if isinstance(played_at, str):
        try:
            played_at = datetime.fromisoformat(played_at)
            if timezone.is_naive(played_at):
                played_at = timezone.make_aware(
                    played_at,
                    timezone.get_current_timezone(),
                )
        except ValueError:
            played_at = None
    fallback_image = entry.get("poster") or getattr(item, "image", "")
    title = entry.get("display_title") or entry.get("title")
    if not title and isinstance(item, dict):
        title = (
            item.get("localized_title")
            or item.get("title")
            or item.get("original_title")
        )
    elif not title:
        title = (
            getattr(item, "localized_title", None)
            or getattr(item, "title", None)
            or getattr(item, "original_title", None)
        )
    if not title:
        show = entry.get("show")
        if isinstance(show, dict):
            title = show.get("title")
        else:
            title = getattr(show, "title", None)
    if not title:
        album = entry.get("album")
        if isinstance(album, dict):
            title = album.get("title")
        else:
            title = getattr(album, "title", None)
    if not title:
        media_type = entry.get("media_type") or getattr(item, "media_type", None)
        title = {
            MediaTypes.MOVIE.value: "Movie",
            MediaTypes.TV.value: "TV Show",
            MediaTypes.EPISODE.value: "Episode",
            MediaTypes.GAME.value: "Game",
            MediaTypes.BOARDGAME.value: "Board Game",
            MediaTypes.MUSIC.value: "Music",
            MediaTypes.PODCAST.value: "Podcast",
            MediaTypes.BOOK.value: "Book",
            MediaTypes.COMIC.value: "Comic",
            MediaTypes.MANGA.value: "Manga",
            MediaTypes.ANIME.value: "Anime",
        }.get(media_type, "Item")
    return {
        "entry": entry,
        "item": item,
        "media_type": entry.get("media_type") or getattr(item, "media_type", None),
        "title": title,
        "image": _poster_image(item, fallback_image),
        "played_at": played_at,
    }


def _cached_horizontal_backdrop(item) -> str | None:
    """Return a cached horizontal backdrop without triggering provider lookups."""
    return backdrops.cached_backdrop(item)


def _poster_image(item, fallback_image):
    """Return the stored portrait artwork for a card, before backdrop upgrade."""
    if item is None:
        poster = fallback_image
    elif isinstance(item, dict):
        poster = fallback_image or item.get("image", "")
    else:
        poster = fallback_image or getattr(item, "image", "")
    return poster or settings.IMG_NONE


def _get_horizontal_history_image(item, fallback_image, *, allow_network=False):
    """Prefer horizontal artwork when available, matching list hub behavior.

    Statistics code always leaves ``allow_network`` off; missing backdrops are
    warmed in the background by ``normalize_highlight_images`` instead.
    """
    backdrop = backdrops.resolve_backdrop(item, allow_network=allow_network)
    return backdrop or _poster_image(item, fallback_image)


def _highlight_entries(history_highlights):
    """Yield the card entries of one highlights dict that carry artwork."""
    if not isinstance(history_highlights, dict):
        return
    for key in ("first_play", "last_play"):
        entry = history_highlights.get(key)
        if isinstance(entry, dict):
            yield entry
    today_card = history_highlights.get("today_card")
    if isinstance(today_card, dict):
        entry = today_card.get("entry")
        if isinstance(entry, dict):
            yield entry


def _upgrade_entry_images(entries) -> None:
    """Swap cached backdrops into card entries; warm the misses in background.

    Never calls a provider. An entry marked ``image_is_backdrop`` already holds
    its backdrop URL and is left alone, so payloads keep serving landscape
    artwork after the Redis backdrop key expires.
    """
    to_warm = []
    for entry in entries:
        if entry.get("image_is_backdrop"):
            continue
        item = entry.get("item")
        backdrop = _cached_horizontal_backdrop(item)
        if backdrop:
            entry["image"] = backdrop
            entry["image_is_backdrop"] = True
            continue
        entry["image"] = _poster_image(item, entry.get("image") or entry.get("poster"))
        if item is not None:
            to_warm.append(item)
    if to_warm:
        backdrops.schedule_backdrop_warm(to_warm)


def normalize_highlight_images(data) -> None:
    """Prefer horizontal artwork on every highlight card of a statistics payload.

    Runs on reads and before a rebuilt payload is published; see the
    "Highlight artwork" section of docs/architecture/statistics-refresh-runs.md.
    """
    if not isinstance(data, dict):
        return
    entries = list(_highlight_entries(data.get("history_highlights")))
    by_type = data.get("history_highlights_by_type")
    if isinstance(by_type, dict):
        for type_highlights in by_type.values():
            entries.extend(_highlight_entries(type_highlights))
    _upgrade_entry_images(entries)


def _normalize_history_highlight_images(history_highlights):
    """Apply ``normalize_highlight_images`` to a single highlights dict."""
    _upgrade_entry_images(list(_highlight_entries(history_highlights)))


def _normalize_history_highlights_by_type(highlights_by_type):
    """Apply ``normalize_highlight_images`` to each per-type highlights dict."""
    normalize_highlight_images({"history_highlights_by_type": highlights_by_type})


_TV_FAMILY = {MediaTypes.TV.value, MediaTypes.EPISODE.value, MediaTypes.SEASON.value}
_ANIME_FAMILY = {MediaTypes.ANIME.value}


def _entry_matches_type_filter(entry, media_type_filter):
    if not media_type_filter:
        return True
    entry_type = entry.get("media_type")
    if media_type_filter == MediaTypes.TV.value:
        return entry_type in _TV_FAMILY
    if media_type_filter == MediaTypes.ANIME.value:
        return entry_type in _ANIME_FAMILY
    return entry_type == media_type_filter


def _select_history_entry_for_day(
    day_payload, pick_earliest=False, pick_latest=False, media_type_filter=None
):
    if not day_payload:
        return None
    entries = day_payload.get("entries") or []
    if media_type_filter:
        entries = [
            e for e in entries if _entry_matches_type_filter(e, media_type_filter)
        ]
    if not entries:
        return None
    if pick_earliest:
        entry = entries[-1]
    elif pick_latest:
        entry = entries[0]
    else:
        entry = random.choice(entries)  # noqa: S311  # sampling/jitter only, not cryptographic
    return _history_entry_card_payload(entry)


def _get_today_history_entries(user, media_type_filter=None, memo=None):
    today = timezone.localdate()
    day_keys = _get_history_index_days(user, memo)
    matching_dates = []
    for day_key in day_keys:
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        if day_date.month == today.month and day_date.day == today.day:
            matching_dates.append(day_date)

    if not matching_dates:
        return None, None

    if media_type_filter:
        candidates = matching_dates[:]
        random.shuffle(candidates)
        for candidate in candidates[:12]:
            payload = _get_history_day_payload(user, candidate, memo)
            entry = _select_history_entry_for_day(
                payload, media_type_filter=media_type_filter
            )
            if entry:
                return entry, candidate.year
        return None, None

    available_years = sorted({day_date.year for day_date in matching_dates})
    selected_year = random.choice(available_years)  # noqa: S311  # sampling/jitter only, not cryptographic
    year_dates = [
        day_date for day_date in matching_dates if day_date.year == selected_year
    ]
    selected_date = random.choice(year_dates) if year_dates else None  # noqa: S311  # sampling/jitter only, not cryptographic
    if not selected_date:
        return None, None

    day_payload = _get_history_day_payload(user, selected_date, memo)
    return _select_history_entry_for_day(day_payload), selected_year


# The candidate scan is library-wide (no date index can narrow it to a
# month/day), and one range aggregation asks for it once per media type. Keep
# the handful of matching items for a few minutes so the ranges of a sync pass
# share one scan instead of repeating it per range and per type.
TODAY_RELEASE_CACHE_TIMEOUT = 60 * 5


def _today_release_candidates(
    user,
    today,
    active_types,
    *,
    include_episodes,
    include_podcasts,
):
    key = (
        f"stats:highlights:release:{user.id}:{today.isoformat()}:"
        f"{','.join(sorted(active_types))}:{int(include_episodes)}:"
        f"{int(include_podcasts)}"
    )
    cached = cache.get(key)
    if cached is not None:
        return cached

    items_by_year = defaultdict(list)
    seen_item_ids = set()

    for media_type in active_types:
        model = apps.get_model("app", media_type)
        qs = (
            model.objects.filter(user=user, item__release_datetime__isnull=False)
            .select_related("item")
            .annotate(
                release_month=ExtractMonth("item__release_datetime"),
                release_day=ExtractDay("item__release_datetime"),
            )
            .filter(release_month=today.month, release_day=today.day)
        )
        for media in qs:
            item = getattr(media, "item", None)
            if not item or item.id in seen_item_ids:
                continue
            seen_item_ids.add(item.id)
            release_dt = getattr(item, "release_datetime", None)
            if not release_dt:
                continue
            localized = stats._localize_datetime(release_dt)
            if not localized:
                continue
            release_date = localized.date()
            items_by_year[release_date.year].append(
                {
                    "item": item,
                    "media_type": item.media_type,
                    "title": item.title,
                    "image": _poster_image(item, item.image),
                    "release_date": release_date,
                }
            )

    Episode = apps.get_model("app", "Episode")
    episode_qs = (
        Episode.all_objects.filter(
            Episode.watch_q(),
            related_season__user=user,
            item__release_datetime__isnull=False,
        )
        .select_related(
            "item",
            "related_season__item",
            "related_season__related_tv__item",
        )
        .annotate(
            release_month=ExtractMonth("item__release_datetime"),
            release_day=ExtractDay("item__release_datetime"),
        )
        .filter(release_month=today.month, release_day=today.day)
        if include_episodes
        else []
    )
    for episode in episode_qs:
        episode_item = getattr(episode, "item", None)
        if not episode_item or episode_item.id in seen_item_ids:
            continue
        seen_item_ids.add(episode_item.id)
        release_dt = getattr(episode_item, "release_datetime", None)
        if not release_dt:
            continue
        localized = stats._localize_datetime(release_dt)
        if not localized:
            continue
        release_date = localized.date()
        display_title = history_cache._get_episode_display_title(episode)
        episode_poster = history_cache._get_episode_poster(episode)
        items_by_year[release_date.year].append(
            {
                "item": episode_item,
                "media_type": MediaTypes.EPISODE.value,
                "title": display_title or episode_item.title,
                "image": _poster_image(episode_item, episode_poster),
                "release_date": release_date,
            }
        )

    if include_podcasts:
        Podcast = apps.get_model("app", "Podcast")
        podcast_base = Podcast.objects.filter(user=user).select_related(
            "item", "episode", "show"
        )
        podcast_qs = (
            podcast_base.filter(episode__published__isnull=False)
            .annotate(
                release_month=ExtractMonth("episode__published"),
                release_day=ExtractDay("episode__published"),
            )
            .filter(release_month=today.month, release_day=today.day)
        )
        for podcast in podcast_qs:
            item = getattr(podcast, "item", None)
            if not item or item.id in seen_item_ids:
                continue
            release_dt = getattr(getattr(podcast, "episode", None), "published", None)
            localized = stats._localize_datetime(release_dt) if release_dt else None
            if not localized:
                continue
            release_date = localized.date()
            show = None
            if getattr(podcast, "episode", None) and podcast.episode.show:
                show = podcast.episode.show
            if not show:
                show = podcast.show
            image = (show.image if show and show.image else None) or item.image
            title = item.title or getattr(
                getattr(podcast, "episode", None), "title", ""
            )
            seen_item_ids.add(item.id)
            items_by_year[release_date.year].append(
                {
                    "item": item,
                    "media_type": MediaTypes.PODCAST.value,
                    "title": title,
                    "image": _poster_image(item, image),
                    "release_date": release_date,
                }
            )

        podcast_fallback_qs = (
            podcast_base.filter(
                episode__published__isnull=True,
                item__release_datetime__isnull=False,
            )
            .annotate(
                release_month=ExtractMonth("item__release_datetime"),
                release_day=ExtractDay("item__release_datetime"),
            )
            .filter(release_month=today.month, release_day=today.day)
        )
        for podcast in podcast_fallback_qs:
            item = getattr(podcast, "item", None)
            if not item or item.id in seen_item_ids:
                continue
            release_dt = getattr(item, "release_datetime", None)
            localized = stats._localize_datetime(release_dt) if release_dt else None
            if not localized:
                continue
            release_date = localized.date()
            show = None
            if getattr(podcast, "episode", None) and podcast.episode.show:
                show = podcast.episode.show
            if not show:
                show = podcast.show
            image = (show.image if show and show.image else None) or item.image
            title = item.title or getattr(
                getattr(podcast, "episode", None), "title", ""
            )
            seen_item_ids.add(item.id)
            items_by_year[release_date.year].append(
                {
                    "item": item,
                    "media_type": MediaTypes.PODCAST.value,
                    "title": title,
                    "image": _poster_image(item, image),
                    "release_date": release_date,
                }
            )

    cache.set(key, items_by_year, timeout=TODAY_RELEASE_CACHE_TIMEOUT)
    return items_by_year


def _get_today_release_entry(user, media_type_filter=None):
    today = timezone.localdate()
    active_types = list(getattr(user, "get_active_media_types", list)())
    if not active_types:
        active_types = list(MediaTypes.values)
    include_podcasts = MediaTypes.PODCAST.value in active_types
    active_types = [
        media_type
        for media_type in active_types
        if media_type not in (MediaTypes.EPISODE.value, MediaTypes.PODCAST.value)
    ]
    if media_type_filter:
        if media_type_filter == MediaTypes.TV.value:
            allowed = _TV_FAMILY - {MediaTypes.EPISODE.value}
        elif media_type_filter == MediaTypes.ANIME.value:
            allowed = _ANIME_FAMILY
        else:
            allowed = {media_type_filter}
        active_types = [mt for mt in active_types if mt in allowed]
        include_podcasts = (
            include_podcasts and media_type_filter == MediaTypes.PODCAST.value
        )

    include_episodes = not media_type_filter or media_type_filter == MediaTypes.TV.value
    items_by_year = _today_release_candidates(
        user,
        today,
        active_types,
        include_episodes=include_episodes,
        include_podcasts=include_podcasts,
    )
    if not items_by_year:
        return None, None

    available_years = sorted(items_by_year.keys())
    selected_year = random.choice(available_years)  # noqa: S311  # sampling/jitter only, not cryptographic
    selected_item = (
        random.choice(items_by_year[selected_year])  # noqa: S311  # sampling/jitter only, not cryptographic
        if items_by_year[selected_year]
        else None
    )
    if not selected_item:
        return None, None
    return selected_item, selected_year


def _get_history_index_days(user, memo=None):
    """Return the user's History day keys.

    ``memo`` is a caller-owned dict that lives for one Statistics pass: every
    range, and every media type inside a range, asks for the same index and a
    handful of the same days. It is never written to the cache.
    """
    if memo is not None and "history_index" in memo:
        return memo["history_index"]
    day_keys = _load_history_index_days(user)
    if memo is not None:
        memo["history_index"] = day_keys
    return day_keys


def _load_history_index_days(user):
    logging_style = history_cache._normalize_logging_style(None, user)
    cache_entry = cache.get(history_cache._cache_key(user.id, logging_style))
    if isinstance(cache_entry, dict):
        days = cache_entry.get("days")
        if isinstance(days, list):
            return days

    # Capture the era before reading rows (see history_cache_reader) so this
    # publish cannot embed rows from before a concurrent invalidation.
    era = history_cache._current_history_era(user.id, logging_style)
    day_keys = history_cache.build_history_index(
        user, logging_style_override=logging_style
    )
    history_cache.cache_history_index(user.id, logging_style, day_keys, era=era)
    return day_keys


def _get_history_day_payload(user, day_value, memo=None):
    """Return one History day; ``memo`` as in ``_get_history_index_days``.

    Callers only read a memoized day: the cards built from it are new dicts.
    """
    day_key = history_cache._day_key_from_value(day_value)
    if not day_key:
        return None
    days = None if memo is None else memo.setdefault("history_days", {})
    if days is not None and day_key in days:
        return days[day_key]

    logging_style = history_cache._normalize_logging_style(None, user)
    cache_key = history_cache._day_cache_key(user.id, logging_style, day_key)
    cached_payload = cache.get(cache_key)
    if cached_payload:
        payload = history_cache._deserialize_history_day(cached_payload)
        if days is not None:
            days[day_key] = payload
        return payload

    # Keep the day it had to build. Every range of a Statistics sync picks its
    # own random day, and an uncached day cost ~80 ms each time it came up.
    # A day built here is not memoized: the next read returns the cached form,
    # so a pass publishes the same cards with or without the memo.
    return history_cache._build_and_cache_history_day(
        user,
        day_key,
        logging_style,
    )


def _get_range_history_boundary_days(user, start_date, end_date, memo=None):
    # Deferred import: _normalize_day_value lives in statistics_cache; importing
    # at module level would create a load-time circular dependency.
    from app.statistics_cache import _normalize_day_value

    start_day = _normalize_day_value(start_date)
    end_day = _normalize_day_value(end_date)
    index_day_keys = _get_history_index_days(user, memo)
    if not index_day_keys:
        return None, None

    matching_days = []
    for day_key in index_day_keys:
        day_value = _normalize_day_value(day_key)
        if not day_value:
            continue
        if start_day and day_value < start_day:
            continue
        if end_day and day_value > end_day:
            continue
        matching_days.append(day_value)

    if not matching_days:
        return None, None
    return matching_days[-1], matching_days[0]
