import contextlib
import logging
import math
import time
from collections import defaultdict
from datetime import date, timedelta
from functools import partial
from itertools import pairwise
from urllib.parse import urlencode

from django.apps import apps
from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models.functions import ExtractDay, ExtractMonth
from django.db.utils import OperationalError
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
    JsonResponse,
)
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.dateparse import parse_date
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from django.views.decorators.http import require_GET, require_http_methods

from app import (
    fork_services_history,
    helpers,
    history_cache,
    history_cache_reader,
)
from app import statistics as stats
from app.models import (
    Anime,
    BoardGame,
    Book,
    Comic,
    Episode,
    Game,
    Manga,
    MediaTypes,
    Movie,
    Podcast,
)

logger = logging.getLogger(__name__)

MONTHS_PER_YEAR = 12
SESSION_HISTORY_MAX_DAYS = 400
HISTORY_PAGE_RADIUS = 2

_MONTH_CACHE_UNSUPPORTED_FILTER_KEYS = frozenset(
    {
        "artist",
        "person_id",
        "person_source",
        "season",
        "season_number",
        "episode_number",
        "tv",
    },
)


@require_http_methods(["DELETE"])
def delete_history_record(request, media_type, history_id):
    """Delete a specific history record."""
    music_id = request.GET.get("music_id")
    podcast_id = request.GET.get("podcast_id")

    # FORK: deletion core shared with the REST API.
    try:
        fork_services_history.delete_history_record_core(
            request.user,
            media_type,
            history_id,
        )
    except fork_services_history.HistoryRecordNotFoundError:
        logger.exception(
            "History record %s not found for user %s",
            str(history_id),
            str(request.user),
        )
        return HttpResponse("Record not found", status=404)
    except fork_services_history.HistoryDeletionError as e:
        return HttpResponse(e.message, status=500)

    if music_id and media_type.lower() == "music":
        from app.models import Music
        from users.templatetags.user_tags import user_date_format

        try:
            music = Music.objects.get(id=music_id, user=request.user)
            remaining_history = list(
                music.history.filter(
                    history_user=request.user,
                ).order_by("-end_date"),
            ) or list(
                music.history.filter(
                    history_user__isnull=True,
                ).order_by("-end_date"),
            )

            remaining_count = len(remaining_history)

            if remaining_count > 0:
                last_entry = remaining_history[0]
                last_date_formatted = (
                    user_date_format(last_entry.end_date, request.user)
                    if last_entry.end_date
                    else "No date provided"
                )

                if remaining_count == 1:
                    history_text = f"Last listened: {last_date_formatted}"
                else:
                    history_text = (
                        f"Last listened: {last_date_formatted} "
                        f"• Listened {remaining_count} times"
                    )

                response = HttpResponse()
                response.write(
                    f'<p id="track-history-{music.id}" hx-swap-oob="true" '
                    'class="text-xs text-gray-400 mt-2 px-4">'
                    f"{history_text}</p>",
                )
                modal_text = (
                    "Listened once"
                    if remaining_count == 1
                    else f"Listened {remaining_count} times"
                )
                response.write(
                    f'<p id="modal-listen-count-{music.id}" hx-swap-oob="true" '
                    'class="text-sm text-gray-400 mt-1">'
                    f"{modal_text}</p>",
                )
                return response
            response = HttpResponse()
            response.write(
                f'<p id="track-history-{music.id}" hx-swap-oob="true" '
                'class="text-xs text-gray-400 mt-2 px-4" style="display: none;"></p>',
            )
            response.write(
                f'<p id="modal-listen-count-{music.id}" hx-swap-oob="true" '
                'class="text-sm text-gray-400 mt-1">Not listened yet</p>',
            )
        except Music.DoesNotExist:
            pass
        else:
            return response

    if podcast_id and media_type.lower() == "podcast":
        from app.models import Podcast
        from users.templatetags.user_tags import user_date_format

        try:
            podcast = Podcast.objects.get(id=podcast_id, user=request.user)
            remaining_history = list(
                podcast.plays.filter(end_date__isnull=False).order_by("-end_date"),
            )

            remaining_count = len(remaining_history)

            if remaining_count > 0:
                last_entry = remaining_history[0]
                last_date_formatted = (
                    user_date_format(last_entry.end_date, request.user)
                    if last_entry.end_date
                    else "No date provided"
                )

                if remaining_count == 1:
                    history_text = f"Last played: {last_date_formatted}"
                else:
                    history_text = (
                        f"Last played: {last_date_formatted} "
                        f"• Played {remaining_count} times"
                    )

                response = HttpResponse()
                modal_text = (
                    "Played once"
                    if remaining_count == 1
                    else f"Played {remaining_count} times"
                )
                response.write(
                    f'<p id="modal-listen-count-{podcast.id}" hx-swap-oob="true" '
                    'class="text-sm text-gray-400 mt-1">'
                    f"{modal_text}</p>",
                )
                response["HX-Trigger"] = "history-refresh-start"
                return response
            response = HttpResponse()
            response.write(
                f'<p id="modal-listen-count-{podcast.id}" hx-swap-oob="true" '
                'class="text-sm text-gray-400 mt-1">Not played yet</p>',
            )
            response["HX-Trigger"] = "history-refresh-start"
        except Podcast.DoesNotExist:
            pass
        else:
            return response

    response = HttpResponse()
    response["HX-Trigger"] = "history-refresh-start"
    return response


def _build_anniversary_history_days(user, month, day, logging_style=None):
    day_keys = []
    for day_key in history_cache_reader.get_history_index_day_keys(
        user, logging_style
    ):
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        if day_date.month == month and day_date.day == day:
            day_keys.append(day_key)
    return [
        day_payload
        for day_payload in history_cache_reader.get_cached_history_days(
            user, day_keys, logging_style
        )
        if day_payload.get("entries")
    ]


def _build_release_history_days(
    user, month=None, day=None, date_filters=None, filters=None
):
    active_types = list(getattr(user, "get_active_media_types", list)())
    if not active_types:
        active_types = list(MediaTypes.values)
    include_podcasts = MediaTypes.PODCAST.value in active_types
    active_types = [
        media_type
        for media_type in active_types
        if media_type not in (MediaTypes.EPISODE.value, MediaTypes.PODCAST.value)
    ]

    media_type_filter_raw = (filters or {}).get("media_type")
    media_type_filters = (
        {t.strip() for t in media_type_filter_raw.split(",") if t.strip()}
        if media_type_filter_raw
        else set()
    )
    include_episodes = True
    if media_type_filters:
        tv_like_types = {
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.EPISODE.value,
        }
        other_types = media_type_filters - tv_like_types - {MediaTypes.PODCAST.value}
        include_episodes = bool(media_type_filters & tv_like_types)
        include_podcasts = MediaTypes.PODCAST.value in media_type_filters
        active_types = (
            [mt for mt in active_types if mt in other_types] if other_types else []
        )

    def _matches_genre_filters(item, *, album=None):
        active_filters = filters or {}
        genre_filter = active_filters.get("genre")
        if genre_filter:
            requested = {
                g.strip().lower() for g in genre_filter.split(",") if g.strip()
            }
            values = getattr(item, "genres", None) or []
            if album is not None:
                values = list(values) + list(getattr(album, "genres", None) or [])
            if not ({str(g).lower() for g in values} & requested):
                return False
        implied_genre_filter = active_filters.get("implied_genre")
        if implied_genre_filter:
            requested = {
                g.strip().lower() for g in implied_genre_filter.split(",") if g.strip()
            }
            values = getattr(item, "implied_genres", None) or []
            if album is not None:
                values = list(values) + list(
                    getattr(album, "implied_genres", None) or []
                )
            if not ({str(g).lower() for g in values} & requested):
                return False
        return True

    start_date = None
    end_date = None
    if date_filters:
        start_date = parse_date(date_filters.get("start_date") or "")
        end_date = parse_date(date_filters.get("end_date") or "")

    release_days = defaultdict(list)
    seen_item_ids = set()
    for media_type in active_types:
        model = apps.get_model("app", media_type)
        queryset = model.objects.filter(
            user=user, item__release_datetime__isnull=False
        ).select_related("item")
        if month and day:
            queryset = queryset.annotate(
                release_month=ExtractMonth("item__release_datetime"),
                release_day=ExtractDay("item__release_datetime"),
            ).filter(release_month=month, release_day=day)
        elif start_date or end_date:
            if start_date:
                queryset = queryset.filter(item__release_datetime__date__gte=start_date)
            if end_date:
                queryset = queryset.filter(item__release_datetime__date__lte=end_date)

        for media in queryset:
            item = getattr(media, "item", None)
            if not item or item.id in seen_item_ids:
                continue
            if not _matches_genre_filters(item):
                continue
            seen_item_ids.add(item.id)
            release_dt = getattr(item, "release_datetime", None)
            localized = stats._localize_datetime(release_dt) if release_dt else None
            if not localized:
                continue
            release_date = localized.date()
            entry = {
                "item": item,
                "media_type": item.media_type,
                "title": item.title,
                "display_title": item.title,
                "poster": item.image,
                "played_at_local": localized,
                "entry_key": f"release-{item.id}-{release_date.isoformat()}",
            }
            release_days[release_date].append(entry)

    if include_episodes:
        Episode = apps.get_model("app", "Episode")
        episode_qs = Episode.all_objects.filter(
            related_season__user=user,
            item__release_datetime__isnull=False,
        ).select_related(
            "item",
            "related_season__item",
            "related_season__related_tv__item",
        )
        if month and day:
            episode_qs = episode_qs.annotate(
                release_month=ExtractMonth("item__release_datetime"),
                release_day=ExtractDay("item__release_datetime"),
            ).filter(release_month=month, release_day=day)
        elif start_date or end_date:
            if start_date:
                episode_qs = episode_qs.filter(
                    item__release_datetime__date__gte=start_date
                )
            if end_date:
                episode_qs = episode_qs.filter(
                    item__release_datetime__date__lte=end_date
                )

        for episode in episode_qs:
            episode_item = getattr(episode, "item", None)
            if not episode_item or episode_item.id in seen_item_ids:
                continue
            if not _matches_genre_filters(
                episode_item,
                album=None,
            ):
                continue
            seen_item_ids.add(episode_item.id)
            release_dt = getattr(episode_item, "release_datetime", None)
            localized = stats._localize_datetime(release_dt) if release_dt else None
            if not localized:
                continue
            release_date = localized.date()
            season_item = getattr(episode.related_season, "item", None)
            tv_item = getattr(
                getattr(episode.related_season, "related_tv", None), "item", None
            )
            title = (
                episode_item.title
                or (season_item.title if season_item else None)
                or (tv_item.title if tv_item else "")
            )
            display_title = history_cache._get_episode_display_title(episode)
            entry = {
                "item": episode_item,
                "media_type": MediaTypes.EPISODE.value,
                "title": title,
                "display_title": display_title or title,
                "poster": history_cache._get_episode_poster(episode),
                "played_at_local": localized,
                "entry_key": f"release-episode-{episode.id}-{release_date.isoformat()}",
            }
            release_days[release_date].append(entry)

    if include_podcasts:
        Podcast = apps.get_model("app", "Podcast")
        podcast_base = Podcast.objects.filter(user=user).select_related(
            "item", "episode", "show"
        )
        podcast_qs = podcast_base.filter(episode__published__isnull=False)
        if month and day:
            podcast_qs = podcast_qs.annotate(
                release_month=ExtractMonth("episode__published"),
                release_day=ExtractDay("episode__published"),
            ).filter(release_month=month, release_day=day)
        elif start_date or end_date:
            if start_date:
                podcast_qs = podcast_qs.filter(episode__published__date__gte=start_date)
            if end_date:
                podcast_qs = podcast_qs.filter(episode__published__date__lte=end_date)

        for podcast in podcast_qs:
            item = getattr(podcast, "item", None)
            if not item or item.id in seen_item_ids:
                continue
            if not _matches_genre_filters(item):
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
            poster = settings.IMG_NONE
            if show and show.image:
                poster = show.image
            elif item.image:
                poster = item.image
            title = item.title or getattr(
                getattr(podcast, "episode", None), "title", ""
            )
            entry = {
                "item": item,
                "media_type": MediaTypes.PODCAST.value,
                "title": title,
                "display_title": title,
                "show": show,
                "poster": poster,
                "played_at_local": localized,
                "entry_key": f"release-podcast-{podcast.id}-{release_date.isoformat()}",
            }
            seen_item_ids.add(item.id)
            release_days[release_date].append(entry)

        podcast_fallback_qs = podcast_base.filter(
            episode__published__isnull=True,
            item__release_datetime__isnull=False,
        )
        if month and day:
            podcast_fallback_qs = podcast_fallback_qs.annotate(
                release_month=ExtractMonth("item__release_datetime"),
                release_day=ExtractDay("item__release_datetime"),
            ).filter(release_month=month, release_day=day)
        elif start_date or end_date:
            if start_date:
                podcast_fallback_qs = podcast_fallback_qs.filter(
                    item__release_datetime__date__gte=start_date,
                )
            if end_date:
                podcast_fallback_qs = podcast_fallback_qs.filter(
                    item__release_datetime__date__lte=end_date,
                )

        for podcast in podcast_fallback_qs:
            item = getattr(podcast, "item", None)
            if not item or item.id in seen_item_ids:
                continue
            if not _matches_genre_filters(item):
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
            poster = settings.IMG_NONE
            if show and show.image:
                poster = show.image
            elif item.image:
                poster = item.image
            title = item.title or getattr(
                getattr(podcast, "episode", None), "title", ""
            )
            entry = {
                "item": item,
                "media_type": MediaTypes.PODCAST.value,
                "title": title,
                "display_title": title,
                "show": show,
                "poster": poster,
                "played_at_local": localized,
                "entry_key": f"release-podcast-{podcast.id}-{release_date.isoformat()}",
            }
            seen_item_ids.add(item.id)
            release_days[release_date].append(entry)

    history_days = []
    for release_date, entries in sorted(
        release_days.items(),
        key=lambda item: item[0],
        reverse=True,
    ):
        entries.sort(key=lambda entry: entry.get("played_at_local"), reverse=True)
        release_display_dt = entries[0]["played_at_local"]
        history_days.append(
            {
                "date": release_date,
                "weekday": formats.date_format(release_display_dt, "l"),
                "date_display": formats.date_format(release_display_dt, "F j, Y"),
                "entries": entries,
                "total_minutes": 0,
                "total_runtime_display": f"{len(entries)} release{'s' if len(entries) != 1 else ''}",
                "release_count": len(entries),
            },
        )
    return history_days


def _filter_history_by_enabled_media_types(history_days, user):
    """Filter history entries to only include enabled media types."""
    allowed_types = _enabled_history_media_types(user)
    if allowed_types is None:
        return history_days

    filtered_days = []
    for day in history_days:
        if isinstance(day, dict):
            if day.get("_entries_filtered"):
                if day.get("entries"):
                    filtered_days.append(day)
                continue
            entries = day.get("entries", [])
            filtered_entries = [
                entry for entry in entries if entry.get("media_type") in allowed_types
            ]
            if filtered_entries:
                filtered_day = day.copy()
                filtered_day["entries"] = filtered_entries
                total_minutes = sum(
                    entry.get("runtime_minutes") or 0 for entry in filtered_entries
                )
                filtered_day["total_minutes"] = total_minutes
                filtered_day["total_runtime_display"] = (
                    helpers.minutes_to_hhmm(total_minutes) if total_minutes else "0min"
                )
                filtered_days.append(filtered_day)
        else:
            filtered_days.append(day)

    return filtered_days


def _enabled_history_media_types(user):
    """Return the media types History may show, or None for no restriction."""
    enabled_types = user.get_enabled_media_types()
    if not enabled_types:
        return None
    allowed_types = set(enabled_types)
    if MediaTypes.TV.value in allowed_types:
        allowed_types.update({MediaTypes.EPISODE.value, MediaTypes.SEASON.value})
    return allowed_types


class _IndexedHistoryDays:
    """Days of a date-bounded history, read a page at a time from the index."""

    def __init__(self, user, filters, date_filters, logging_style):
        self._user = user
        self._filters = filters
        self._date_filters = date_filters
        self._logging_style = logging_style
        self._total = None

    def _window(self, limit, offset):
        return history_cache_reader.get_cached_history_window(
            self._user,
            limit=limit,
            offset=offset,
            filters=self._filters or None,
            logging_style_override=self._logging_style,
            date_filters=self._date_filters,
            allowed_media_types=_enabled_history_media_types(self._user),
        )

    def __len__(self):
        if self._total is None:
            self._total = self._window(0, 0)[1]
        return self._total

    def __getitem__(self, key):
        start, stop, _ = key.indices(len(self))
        return self._window(stop - start, start)[0] if stop > start else []


def _can_use_cached_month_history(
    history_mode,
    filters,
    date_filters,
    anniversary_month,
    anniversary_day,
):
    if history_mode != "activity":
        return False
    if date_filters or anniversary_month or anniversary_day:
        return False
    if any(key in filters for key in _MONTH_CACHE_UNSUPPORTED_FILTER_KEYS):
        return False
    return not (filters.get("media_id") or filters.get("source"))


def _cached_history_entry_matches_filters(entry, filters):
    entry = entry or {}
    item = entry.get("item") or {}
    album = entry.get("album") or {}
    show = entry.get("show") or {}
    entry_media_type = entry.get("media_type")
    media_type_filter_raw = filters.get("media_type")
    if media_type_filter_raw:
        media_type_filters = {
            t.strip() for t in media_type_filter_raw.split(",") if t.strip()
        }
        allowed_media_types = set(media_type_filters)
        if MediaTypes.TV.value in media_type_filters:
            allowed_media_types.update(
                {MediaTypes.EPISODE.value, MediaTypes.SEASON.value},
            )
        if entry_media_type not in allowed_media_types:
            return False

    genre_filter = filters.get("genre")
    if genre_filter:
        genre_filters = {
            g.strip().lower() for g in genre_filter.split(",") if g.strip()
        }
        genres = entry.get("genres") or item.get("genres") or []
        item_genre_set = {str(g).lower() for g in genres}
        if not (item_genre_set & genre_filters):
            return False
    implied_genre_filter = filters.get("implied_genre")
    if implied_genre_filter:
        implied_genre_filters = {
            g.strip().lower() for g in implied_genre_filter.split(",") if g.strip()
        }
        implied_genres = entry.get("implied_genres") or item.get("implied_genres") or []
        item_implied_genre_set = {str(g).lower() for g in implied_genres}
        if not (item_implied_genre_set & implied_genre_filters):
            return False

    album_filter = filters.get("album")
    if album_filter is not None:
        if entry_media_type != MediaTypes.MUSIC.value:
            return False
        if album.get("id") != album_filter:
            return False

    podcast_show_filter = filters.get("podcast_show")
    if podcast_show_filter is not None:
        if entry_media_type != MediaTypes.PODCAST.value:
            return False
        if show.get("id") != podcast_show_filter:
            return False

    target_media_id = filters.get("media_id")
    if target_media_id is not None and str(item.get("media_id")) != str(
        target_media_id
    ):
        return False

    target_source = filters.get("source")
    return not (
        target_source is not None and str(item.get("source")) != str(target_source)
    )


def _cached_history_entry_is_visible(entry, *, filters, allowed_types):
    if allowed_types is not None and entry.get("media_type") not in allowed_types:
        return False
    return not filters or _cached_history_entry_matches_filters(entry, filters)


def _filter_cached_history_days(history_days, filters):
    if not filters:
        return history_days

    filtered_days = []
    for day in history_days:
        if not isinstance(day, dict):
            continue
        if day.get("_entries_filtered"):
            if day.get("entries"):
                filtered_day = day.copy()
                total_minutes = filtered_day.get("total_minutes") or 0
                filtered_day["total_runtime_display"] = (
                    helpers.minutes_to_hhmm(total_minutes) if total_minutes else "0min"
                )
                filtered_days.append(filtered_day)
            continue

        filtered_entries = [
            entry
            for entry in day.get("entries", [])
            if _cached_history_entry_matches_filters(entry, filters)
        ]
        if not filtered_entries:
            continue

        total_minutes = sum(
            entry.get("runtime_minutes") or 0 for entry in filtered_entries
        )
        filtered_day = day.copy()
        filtered_day["entries"] = filtered_entries
        filtered_day["total_minutes"] = total_minutes
        filtered_day["total_runtime_display"] = (
            helpers.minutes_to_hhmm(total_minutes) if total_minutes else "0min"
        )
        filtered_days.append(filtered_day)

    return filtered_days


def _history_day_key(day):
    """Return the canonical cache key for a rendered history day."""
    if not isinstance(day, dict):
        return None
    return history_cache.history_day_key(day.get("date"))


def _annotate_history_day_for_template(day):
    """Add stable template metadata without changing the entry list."""
    if not isinstance(day, dict):
        return None
    annotated_day = day.copy()
    day_date = annotated_day.get("date")
    if isinstance(day_date, str):
        day_date = parse_date(day_date)
    if day_date:
        annotated_day["weekday"] = formats.date_format(day_date, "l")
        annotated_day["date_display"] = formats.date_format(
            day_date,
            "DATE_FORMAT",
        )
    entries = list(annotated_day.get("entries", []))
    entry_count = annotated_day.get("entry_count", len(entries))
    entry_offset = annotated_day.get("_entry_window_offset", 0)
    annotated_day.update(
        {
            "day_key": _history_day_key(annotated_day),
            "entry_count": entry_count,
            "entry_offset": entry_offset,
            "next_entry_offset": entry_offset + len(entries),
            "has_more": entry_offset + len(entries) < entry_count,
            "remaining_entry_count": max(
                entry_count - entry_offset - len(entries),
                0,
            ),
        },
    )
    return annotated_day


def _attach_session_notes(days):
    """Attach current per-instance notes without changing cached history payloads."""
    ids_by_media_type = defaultdict(set)
    for day in days:
        for entry in day.get("entries", []):
            instance_id = entry.get("instance_id")
            media_type = entry.get("media_type")
            if instance_id is not None and media_type != MediaTypes.MUSIC.value:
                ids_by_media_type[media_type].add(instance_id)

    model_by_media_type = {
        MediaTypes.MOVIE.value: Movie,
        MediaTypes.EPISODE.value: Episode,
        MediaTypes.GAME.value: Game,
        MediaTypes.BOARDGAME.value: BoardGame,
        MediaTypes.BOOK.value: Book,
        MediaTypes.COMIC.value: Comic,
        MediaTypes.MANGA.value: Manga,
        MediaTypes.ANIME.value: Anime,
        MediaTypes.PODCAST.value: Podcast,
    }
    notes_by_media_type = {}
    for media_type, instance_ids in ids_by_media_type.items():
        model = model_by_media_type.get(media_type)
        if model is None:
            continue
        notes_by_media_type[media_type] = dict(
            model.objects.filter(id__in=instance_ids).values_list("id", "notes"),
        )

    for day in days:
        for entry in day.get("entries", []):
            media_notes = notes_by_media_type.get(entry.get("media_type"), {})
            entry["notes"] = media_notes.get(entry.get("instance_id"), "")


def _build_session_history_stats(history_days):
    """Build display-ready statistics for the filtered session history."""
    dated_days = [day for day in history_days if day.get("date")]
    dates = sorted({day["date"] for day in dated_days})
    activity_entries = sum(len(day.get("entries", [])) for day in history_days)
    total_minutes = sum(day.get("total_minutes") or 0 for day in history_days)

    gaps = [(current - previous).days for previous, current in pairwise(dates)]
    average_gap = sum(gaps) / len(gaps) if gaps else None

    longest_streak = 0
    current_streak = 0
    for index, current in enumerate(dates):
        previous = dates[index - 1] if index else None
        if previous is not None and (current - previous).days == 1:
            current_streak += 1
        else:
            current_streak = 1
        longest_streak = max(longest_streak, current_streak)

    entries_by_weekday = defaultdict(int)
    for day in dated_days:
        entries_by_weekday[day["date"].weekday()] += len(day.get("entries", []))
    most_active_weekday = None
    if entries_by_weekday:
        weekday = max(
            entries_by_weekday,
            key=lambda value: (entries_by_weekday[value], -value),
        )
        most_active_weekday = formats.date_format(
            date(2024, 1, 1) + timedelta(days=weekday),
            "l",
        )

    def format_minutes(minutes):
        return helpers.minutes_to_hhmm(round(minutes)) if minutes is not None else "-"

    def format_days(days):
        if days is None:
            return "-"
        rounded = round(days, 1)
        return f"{rounded:g} day{'s' if rounded != 1 else ''}"

    return [
        {"label": gettext_noop("Active days"), "value": str(len(dates))},
        {"label": gettext_noop("Activity entries"), "value": str(activity_entries)},
        {
            "label": gettext_noop("Tracked time"),
            "value": format_minutes(total_minutes if activity_entries else None),
        },
        {
            "label": gettext_noop("Average per active day"),
            "value": format_minutes(total_minutes / len(dates)) if dates else "-",
        },
        {"label": gettext_noop("Average gap"), "value": format_days(average_gap)},
        {
            "label": gettext_noop("Longest streak"),
            "value": (
                f"{longest_streak} day{'s' if longest_streak != 1 else ''}"
                if longest_streak
                else "-"
            ),
        },
        {
            "label": gettext_noop("Most active weekday"),
            "value": most_active_weekday or "-",
        },
    ]


def _activity_day_intensity(history_days):
    """Describe each dated day's activity relative to its month's matching unit."""
    days = {}
    maxima = {}
    for day in history_days:
        if not day.get("date") or not day.get("entries"):
            continue
        key = day["date"].isoformat()
        minutes = sum(max(0, entry.get("runtime_minutes") or 0) for entry in day["entries"])
        measure = "minutes" if minutes else "entries"
        value = minutes or len(day["entries"])
        days[key] = {"measure": measure, "value": value}
        group = (key[:7], measure)
        maxima[group] = max(maxima.get(group, 0), value)
    for key, activity in days.items():
        maximum = maxima[(key[:7], activity["measure"])]
        activity["level"] = min(3, max(1, math.ceil(activity["value"] * 3 / maximum)))
    return days


def _history_feed_sessions(history_days, user):
    """Return the activity dialog's bounded session list."""
    from users.templatetags import user_tags

    sessions = []
    for day in history_days:
        day_date = day.get("date")
        for entry in day.get("entries", []):
            played_at = entry.get("played_at_local")
            date_key = (
                played_at.date().isoformat()
                if hasattr(played_at, "date")
                else str(day_date) if day_date else ""
            )
            when = (
                user_tags.user_datetime_format(played_at, user)
                if played_at
                else ""
            )
            sessions.append(
                {
                    "key": entry.get("entry_key") or f"session-{len(sessions)}",
                    "date": date_key,
                    "when": when,
                    "label": _history_feed_verb(entry),
                    "runtime": entry.get("runtime_display")
                    or entry.get("progress_display")
                    or "",
                    "runtimeMinutes": entry.get("runtime_minutes") or 0,
                    "score": entry.get("score"),
                    "note": strip_tags(entry.get("notes") or "").strip(),
                },
            )
    return sessions


@require_GET
def activity_sessions_modal(request):
    """Return the in-place, date-grouped activity history for one media item."""
    filters, logging_style = _parse_history_filters(request)
    history_days = history_cache_reader.get_history_days(
        request.user,
        filters,
        None,
        logging_style,
        cap_entries_per_day=False,
    )
    history_days = _filter_history_by_enabled_media_types(
        history_days,
        request.user,
    )
    _attach_session_notes(history_days)
    history_days = [
        annotated_day
        for day in history_days
        if (annotated_day := _annotate_history_day_for_template(day))
    ]
    session_history_stats = _build_session_history_stats(history_days)
    activity_intensity = _activity_day_intensity(history_days)

    marker_days = sorted(
        {day["date"].isoformat() for day in history_days if day.get("date")},
    )
    newest_dated_day = next(
        (day["date"] for day in history_days if day.get("date")),
        timezone.localdate(),
    )
    visible_days = history_days[:SESSION_HISTORY_MAX_DAYS]
    visible_marker_days = sorted(
        {day["date"].isoformat() for day in visible_days if day.get("date")},
    )
    calendar_props = {
        "activityCalendar": True,
        "activityIntensity": activity_intensity,
        "selectableDays": visible_marker_days,
        "initialMonth": newest_dated_day.strftime("%Y-%m"),
        "weekStart": request.user.week_start_day or "monday",
        "labels": {
            "activity": _("View activity for"),
            "activityIntensity": _("{activity} {date}, {level} {dots}, {value} {measure}"),
            "dot": _("dot"),
            "dots": _("dots"),
            "minutes": _("minutes"),
            "entries": _("entries"),
            "previousMonth": _("Previous month"),
            "nextMonth": _("Next month"),
            "selectMonth": _("Select month"),
            "previousYear": _("Previous year"),
            "nextYear": _("Next year"),
            "selectYear": _("Select year"),
            "previousYears": _("Previous years"),
            "nextYears": _("Next years"),
            "year": _("Year"),
            "back": _("Back"),
        },
    }
    if request.GET.get("format") == "json":
        return JsonResponse(
            {
                "sessions": _history_feed_sessions(visible_days, request.user),
                "activityIntensity": activity_intensity,
                "stats": [
                    {"label": _(stat["label"]), "value": stat["value"]}
                    for stat in session_history_stats
                ],
            },
        )
    return render(
        request,
        "app/components/session_history_modal.html",
        {
            "user": request.user,
            "history_days": visible_days,
            "marker_days": marker_days,
            "activity_intensity": activity_intensity,
            "activity_calendar_props": calendar_props,
            "visible_marker_days": visible_marker_days,
            "session_history_stats": session_history_stats,
            "initial_month": newest_dated_day.strftime("%Y-%m"),
            "history_capped": len(history_days) > SESSION_HISTORY_MAX_DAYS,
            "history_querystring": request.GET.urlencode(),
        },
    )


def _prepare_history_day_page(day, user, filters, offset=0):
    """Filter and bound one cached month-view day for HTML rendering."""
    filtered_days = _filter_cached_history_days([day], filters)
    filtered_days = _filter_history_by_enabled_media_types(filtered_days, user)
    if not filtered_days:
        return None

    annotated_day = _annotate_history_day_for_template(filtered_days[0])
    if annotated_day is None:
        return None

    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    offset = max(offset, 0)

    page_size = history_cache.HISTORY_ENTRIES_PER_DAY_PAGE
    entries = annotated_day["entries"]
    window_offset = day.get("_entry_window_offset", 0)
    local_offset = max(offset - window_offset, 0)
    annotated_day["entries"] = entries[local_offset : local_offset + page_size]
    annotated_day["entry_offset"] = offset
    annotated_day["next_entry_offset"] = offset + len(annotated_day["entries"])
    annotated_day["has_more"] = (
        annotated_day["next_entry_offset"] < annotated_day["entry_count"]
    )
    annotated_day["remaining_entry_count"] = max(
        annotated_day["entry_count"] - annotated_day["next_entry_offset"],
        0,
    )
    return annotated_day


# About two screens of cards render with the page; later days wait as
# placeholders and load as the reader scrolls near them. Whole days only. The
# browser and Expo feeds share the same rule (frontend/ui/historyFirstView.js).
HISTORY_FIRST_VIEW_ENTRIES = 36


def _first_view_day_count(history_days, budget=HISTORY_FIRST_VIEW_ENTRIES):
    """Return how many leading days render in the first response.

    The first day always renders; each later day renders while the running
    entry count still fits the budget, and every day after the first that does
    not fit waits.
    """
    if not history_days:
        return 0
    total = len(history_days[0].get("entries") or [])
    count = 1
    for day in history_days[1:]:
        entries = len(day.get("entries") or [])
        if total + entries > budget:
            break
        total += entries
        count += 1
    return count


def _history_day_fragment_query(request, offset):
    """Preserve the current history filters for the next day fragment."""
    query = request.GET.copy()
    query.pop("entry_offset", None)
    query.pop("page", None)
    query["entry_offset"] = str(offset)
    return query.urlencode()


def _parse_history_filters(request):
    """Parse filters shared by the full history page and day fragments."""
    filters = {}
    int_params = (
        "album",
        "artist",
        "tv",
        "season",
        "season_number",
        "episode_number",
        "podcast_show",
    )
    str_params = (
        "genre",
        "implied_genre",
        "media_type",
        "media_id",
        "source",
        "person_source",
        "person_id",
    )
    for param in int_params:
        value = request.GET.get(param)
        if value:
            with contextlib.suppress(TypeError, ValueError):
                filters[param] = int(value)
    for param in str_params:
        value = request.GET.get(param)
        if value:
            filters[param] = value

    logging_style = request.GET.get("logging_style")
    if logging_style not in ("sessions", "repeats"):
        logging_style = None
    return filters, logging_style


@require_GET
def history_genres(request):
    """Return sorted list of unique genres from the user's tracked items."""
    from app.models import (
        BoardGame,
        Episode,
        Game,
        Movie,
        Music,
        Podcast,
    )

    def _is_valid_genre(value) -> bool:
        s = str(value).strip()
        return bool(s) and not s.lstrip("-").isdigit()

    genres: set[str] = set()
    implied_genres: set[str] = set()
    # Book, Comic, and Manga use Library of Congress subject headings in their genres
    # field rather than real genre names, so exclude them from the genre list.
    for model_cls in [Movie, Game, Music, BoardGame, Podcast]:
        for genres_list in model_cls.objects.filter(user=request.user).values_list(
            "item__genres", flat=True
        ):
            if genres_list:
                genres.update(str(g).strip() for g in genres_list if _is_valid_genre(g))
        for implied_genres_list in model_cls.objects.filter(
            user=request.user
        ).values_list(
            "item__implied_genres",
            flat=True,
        ):
            if implied_genres_list:
                implied_genres.update(
                    str(g).strip() for g in implied_genres_list if _is_valid_genre(g)
                )

    for genres_list in Episode.all_objects.filter(
        related_season__user=request.user
    ).values_list("related_season__related_tv__item__genres", flat=True):
        if genres_list:
            genres.update(str(g).strip() for g in genres_list if _is_valid_genre(g))

    genres.discard("")
    implied_genres.discard("")
    return JsonResponse(
        {
            "genres": sorted(genres, key=str.lower),
            "implied_genres": sorted(implied_genres, key=str.lower),
        },
    )


def _history_feed_labels():
    """Return the shared feed's labels without serializing template HTML."""
    return {
        "history": _("History"),
        "previousYear": _("Go back one year"),
        "previousMonth": _("Go to previous month"),
        "nextMonth": _("Go to next month"),
        "currentMonth": _("Go to current month"),
        "filters": _("Filter history"),
        "hideFilters": _("Hide filters"),
        "clearFilters": _("Clear filters"),
        "saveView": _("Save view"),
        "apply": _("Apply"),
        "allTypes": _("All types"),
        "mediaType": _("Media type"),
        "viewMode": _("View mode"),
        "activity": _("Activity (watched)"),
        "release": _("Release dates"),
        "genre": _("Genre"),
        "allGenres": _("All genres"),
        "impliedGenre": _("Implied genre"),
        "allImpliedGenres": _("All implied genres"),
        "from": _("From"),
        "to": _("To"),
        "dateRange": _("Date range"),
        "refreshing": _("Refreshing history in background..."),
        "dismiss": _("Dismiss refresh notice"),
        "refreshFailed": _(
            "History could not be refreshed. Showing saved activity.",
        ),
        "retryRefresh": _("Try again"),
        "loadMore": _("Load more"),
        "loadDay": _("Load this day"),
        "loadingDay": _("Loading this day..."),
        "loadDayError": _("This day could not be loaded."),
        "retryDay": _("Retry day"),
        "noHistory": _("No watch history yet"),
        "noHistoryCopy": _("Start tracking movies and episodes to see them here."),
        "loadError": _("History could not be loaded."),
        "retry": _("Try again"),
        "loading": _("Loading history..."),
        "edit": _("Edit"),
        "delete": _("Delete"),
        "showMore": _("Show more"),
        "showLess": _("Show less"),
        "status": _("Status"),
        "rate": _("Rate"),
        "score": _("Score"),
        "close": _("Close"),
        "viewActivity": _("Activity"),
        "open": _("Open"),
        "items": _("items"),
        "item": _("item"),
        "releases": _("releases"),
        "releaseOne": _("release"),
        "previousPage": _("Previous page"),
        "nextPage": _("Next page"),
        "page": _("Page"),
        "activityHistory": _("Activity history"),
        "jumpToDay": _("Jump to a session day"),
        "allDays": _("All days"),
        "noSessions": _("No sessions recorded for this entry."),
        "sessions": _("Sessions"),
        "firstSession": _("First session"),
        "lastSession": _("Last session"),
        "averageSession": _("Average session"),
        "editHistory": _("Edit history entry"),
        "editHistoryCopy": _("Change the date, score or status for this entry."),
        "editDate": _("Date"),
        "deleteHistory": _("Delete history entry"),
        "deleteConfirm": _("Delete this entry from your history? This cannot be undone."),
        "save": _("Save"),
        "cancel": _("Cancel"),
        "clearDay": _("Show all days"),
    }


HISTORY_FEED_MEDIA_TYPE_LABELS = (
    (MediaTypes.TV.value, "TV Show"),
    (MediaTypes.MOVIE.value, "Movie"),
    (MediaTypes.ANIME.value, "Anime"),
    (MediaTypes.GAME.value, "Game"),
    (MediaTypes.MUSIC.value, "Music"),
    (MediaTypes.BOOK.value, "Book"),
    (MediaTypes.MANGA.value, "Manga"),
    (MediaTypes.COMIC.value, "Comic"),
    (MediaTypes.PODCAST.value, "Podcast"),
    (MediaTypes.VIDEO.value, "Video"),
    (MediaTypes.BOARDGAME.value, "Board Game"),
)


def _split_filter_values(value):
    """Return a comma filter as the list the shared feed edits."""
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _history_feed_url(request, *, page=None, **updates):
    """Build a History URL from the current query and overrides."""
    query = request.GET.copy()
    query.pop("entry_offset", None)
    if page is None:
        query.pop("page", None)
    else:
        query["page"] = str(page)
    for key, value in updates.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = str(value)
    encoded = query.urlencode()
    return f"{request.path}?{encoded}" if encoded else request.path


def _history_feed_filters(context):
    """Return the filter state the shared feed edits."""
    active = context.get("active_filters") or {}
    media_types = _split_filter_values(active.get("media_type"))
    genres = _split_filter_values(active.get("genre"))
    implied_genres = _split_filter_values(active.get("implied_genre"))
    date_from = active.get("start-date") or ""
    date_to = active.get("end-date") or ""
    mode = context.get("history_mode") or "activity"
    active_count = sum(
        (
            bool(date_from),
            bool(date_to),
            bool(media_types),
            bool(genres),
            bool(implied_genres),
            mode != "activity",
        ),
    )
    range_label = None
    if date_from or date_to:
        range_label = f"{date_from or 'earliest'} - {date_to or 'today'}"
    return {
        "from": date_from,
        "to": date_to,
        "mediaTypes": media_types,
        "genres": genres,
        "impliedGenres": implied_genres,
        "mode": mode,
        "activeCount": active_count,
        "rangeLabel": range_label,
    }


def _history_feed_options():
    """Return the shared filter options the server owns."""
    return {
        "mediaTypes": [
            {"value": value, "label": _(label)}
            for value, label in HISTORY_FEED_MEDIA_TYPE_LABELS
        ],
    }


def _history_feed_nav(request, context):
    """Return month navigation as feed intents, not template links."""
    if not context.get("use_month_view"):
        return {}
    view_year = context.get("view_year")
    view_month = context.get("view_month")
    if not view_year or not view_month:
        return {}
    previous_year = context.get("prev_year")
    previous_month = context.get("prev_month")
    next_year = context.get("next_year")
    next_month = context.get("next_month")
    current_year = context.get("current_year")
    current_month = context.get("current_month_num")
    nav = {
        "monthLabel": context.get("month_name") or "",
        "previousYear": {
            "label": str(view_year - 1),
            "url": _history_feed_url(request, year=view_year - 1, m=view_month),
        },
        "previousMonth": {
            "label": context.get("prev_month_name") or "",
            "url": _history_feed_url(
                request,
                year=previous_year,
                m=previous_month,
            ),
        },
        "currentMonth": {
            "label": str(current_year),
            "url": _history_feed_url(
                request,
                year=current_year,
                m=current_month,
            ),
            "disabled": bool(context.get("is_current_month")),
        },
    }
    nav["nextMonth"] = {
        "label": context.get("next_month_name") or "",
        "url": _history_feed_url(request, year=next_year, m=next_month),
        "disabled": not context.get("show_next_month"),
    }
    return nav


def _history_feed_pagination(request, context):
    """Return the filtered-view page controls as feed intents."""
    if context.get("use_month_view"):
        return None
    total_pages = int(context.get("total_pages") or 0)
    current_page = int(context.get("current_page") or 1)
    if total_pages <= 1:
        return None

    def page_item(number):
        return {
            "number": number,
            "url": _history_feed_url(request, page=number),
            "current": number == current_page,
        }

    pages = []
    previous = None
    for number in range(1, total_pages + 1):
        if number in {1, total_pages} or abs(number - current_page) <= HISTORY_PAGE_RADIUS:
            if previous is not None and number - previous > 1:
                pages.append(None)
            pages.append(page_item(number))
            previous = number
    return {
        "page": current_page,
        "pageCount": total_pages,
        "previousUrl": _history_feed_url(request, page=current_page - 1)
        if current_page > 1
        else None,
        "nextUrl": _history_feed_url(request, page=current_page + 1)
        if current_page < total_pages
        else None,
        "pages": pages,
    }


def _history_feed_day_url(request, day_key, offset=0):
    """Return the bounded JSON day URL preserving the current filters."""
    query = request.GET.copy()
    query.pop("page", None)
    query["entry_offset"] = str(max(int(offset or 0), 0))
    query["format"] = "json"
    return (
        reverse("history_day_fragment", kwargs={"day_key": day_key})
        + "?"
        + query.urlencode()
    )


def _history_feed_activity_url(entry):
    """Return the existing server activity-session dialog URL for an entry."""
    item = entry.get("item") or {}
    show = entry.get("show") or {}
    album = entry.get("album") or {}
    media_type = entry.get("media_type")
    media_id = item.get("media_id")
    source = item.get("source")
    params = {}
    if media_type == MediaTypes.MUSIC.value and album.get("id"):
        params["album"] = album["id"]
    elif media_type == MediaTypes.PODCAST.value and show.get("id"):
        params["media_type"] = MediaTypes.PODCAST.value
        params["podcast_show"] = show["id"]
    elif media_type == MediaTypes.EPISODE.value:
        params["media_type"] = MediaTypes.TV.value
        params["media_id"] = media_id
        params["source"] = source
        if item.get("season_number"):
            params["season_number"] = item["season_number"]
    else:
        params["media_type"] = media_type
        params["media_id"] = media_id
        params["source"] = source
    if media_type in {MediaTypes.GAME.value, MediaTypes.BOARDGAME.value}:
        params["logging_style"] = "sessions"
    params = {key: value for key, value in params.items() if value not in (None, "")}
    return reverse("activity_sessions_modal") + "?" + urlencode(params)


def _history_feed_verb(entry):
    """Return the action word shown before an entry's timestamp."""
    media_type = entry.get("media_type")
    entry_key = str(entry.get("entry_key") or "")
    if media_type in {MediaTypes.EPISODE.value, MediaTypes.MOVIE.value,
                      MediaTypes.VIDEO.value, MediaTypes.ANIME.value}:
        return _("Watched")
    if media_type in {MediaTypes.GAME.value, MediaTypes.BOARDGAME.value}:
        return _("Played")
    if media_type in {MediaTypes.MUSIC.value, MediaTypes.PODCAST.value}:
        return _("Listened")
    if media_type == MediaTypes.BOOK.value and entry_key.startswith("book-session"):
        return _("Listened")
    return _("Read")


def _history_feed_entry(entry, history_mode, user, request, index=0):
    """Convert one cached history entry into the shared feed row contract."""
    from app.media_card_model import history_actions, history_card_model
    from users.templatetags import user_tags

    media_type = entry.get("media_type")
    card_class = (
        "search-result-card-square"
        if media_type in {MediaTypes.MUSIC.value, MediaTypes.PODCAST.value}
        else "search-result-card"
    )
    card = history_card_model(
        entry,
        card_class,
        history_mode,
        user,
        request,
    ) or {}
    actions = history_actions(
        entry,
        history_mode,
        user,
        request,
    ) or {}
    track = next(
        (
            action
            for action in actions.get("items", [])
            if action.get("modal") == "track"
        ),
        None,
    )
    played_at = entry.get("played_at_local")
    when = ""
    if played_at:
        if history_mode == "release":
            when = user_tags.user_date_format(played_at, user)
        else:
            when = user_tags.user_datetime_format(played_at, user)
    return {
        "key": entry.get("entry_key") or f"history-entry-{index}",
        # The full shared card the release page renders: status/runtime chips,
        # hover actions and the play-count line. Callers that only need the row
        # contract keep using the flattened fields below.
        "card": card,
        "mediaType": card.get("mediaType") or media_type,
        "title": card.get("title") or entry.get("display_title")
        or entry.get("title") or "",
        "altTitle": card.get("altTitle") or None,
        "href": card.get("href") or "",
        "image": card.get("image") or {
            "src": None,
            "fallback": "",
            "source": "primary",
        },
        "subtitle": entry.get("episode_code") or entry.get("episode_label")
        or entry.get("time_range_display") or None,
        "verb": _("Released") if history_mode == "release"
        else _history_feed_verb(entry),
        "when": when,
        "runtime": entry.get("runtime_display")
        or entry.get("progress_display") or "",
        "score": card.get("scoreText"),
        "status": entry.get("status") or None,
        "date": played_at.date().isoformat() if hasattr(played_at, "date") else None,
        "runtimeMinutes": entry.get("runtime_minutes") or 0,
        "canEdit": bool(track),
        "canDelete": False,
        "canViewActivity": history_mode != "release",
        "editUrl": track.get("url") if track else None,
        "editTarget": track.get("target") if track else None,
        "editVals": track.get("vals") if track else None,
        "activityUrl": _history_feed_activity_url(entry)
        if history_mode != "release"
        else None,
        "activityTarget": "#session-history-modal-host",
        "activityVals": {},
    }


def _history_feed_day(day, history_mode, user, request):
    """Convert one prepared history day into the shared feed day contract."""
    day_key = day.get("day_key") or _history_day_key(day)
    lazy = bool(day.get("lazy"))
    # Do not convert a lazy day's entries at all: the browser fetches the
    # bounded slice when the day is requested.
    entries = [] if lazy else [
        _history_feed_entry(entry, history_mode, user, request, index)
        for index, entry in enumerate(day.get("entries", []))
    ]
    entry_count = day.get("entry_count", len(day.get("entries", [])))
    next_offset = day.get("next_entry_offset", len(day.get("entries", [])))
    return {
        "key": day_key,
        "weekday": day.get("weekday", ""),
        "dateLabel": day.get("date_display", ""),
        "entryCount": entry_count,
        "offset": day.get("entry_offset", 0),
        "releaseCount": day.get("release_count")
        if history_mode == "release"
        else None,
        "runtimeLabel": day.get("total_runtime_display", "")
        if history_mode != "release"
        else "",
        "lazy": lazy,
        "loading": False,
        "loadingMore": False,
        "hasMore": bool(day.get("has_more")),
        "remaining": day.get("remaining_entry_count", 0),
        "entries": entries,
        "loadUrl": _history_feed_day_url(request, day_key, 0) if day_key else None,
        "nextUrl": _history_feed_day_url(request, day_key, next_offset)
        if day_key and day.get("has_more")
        else None,
    }


def _history_feed_model(request, context):
    """Return the bounded initial model for the browser History feed."""
    history_days = context.get("history_days") or []
    history_mode = context.get("history_mode") or "activity"
    user = context["user"]
    return {
        "mode": history_mode,
        "refreshing": bool(context.get("history_refreshing")),
        "refreshError": bool(context.get("database_error")),
        "summary": [],
        "days": [
            _history_feed_day(day, history_mode, user, request)
            for day in history_days
        ],
        "filters": _history_feed_filters(context),
        "options": _history_feed_options(),
        "nav": _history_feed_nav(request, context),
        "pagination": _history_feed_pagination(request, context),
    }


@require_GET
def history(request):
    """Show a day-by-day history of episode and movie plays."""
    # Surface the media type the user came from (e.g. "view your activity
    # history" on a movie page) so the navbar search type stays in context
    # instead of falling back to a stale last_search_type. Invalid values are
    # dropped so the search bar falls back to last_search_type.
    requested_media_type = request.GET.get("media_type")
    context_media_type = (
        requested_media_type if requested_media_type in MediaTypes.values else None
    )
    try:
        view_start = time.perf_counter()
        history_mode = request.GET.get("history_mode")
        if history_mode != "release":
            history_mode = "activity"

        filters, logging_style = _parse_history_filters(request)

        date_filters = {}
        start_date_str = request.GET.get("start-date")
        end_date_str = request.GET.get("end-date")
        if start_date_str:
            date_filters["start_date"] = start_date_str
        if end_date_str:
            date_filters["end_date"] = end_date_str

        anniversary_month = request.GET.get("month")
        anniversary_day = request.GET.get("day")
        try:
            anniversary_month = int(anniversary_month) if anniversary_month else None
            anniversary_day = int(anniversary_day) if anniversary_day else None
        except (TypeError, ValueError):
            anniversary_month = None
            anniversary_day = None

        now = timezone.localtime()
        try:
            view_year = int(request.GET.get("year", now.year))
            view_month = int(request.GET.get("m", now.month))
            if view_month < 1 or view_month > MONTHS_PER_YEAR:
                view_month = now.month
        except (TypeError, ValueError):
            view_year = now.year
            view_month = now.month

        logger.info(
            "history_view_start user_id=%s year=%s month=%s filters=%s date_filters=%s logging_style=%s",
            request.user.id,
            view_year,
            view_month,
            filters,
            date_filters,
            logging_style,
        )

        use_month_cache = _can_use_cached_month_history(
            history_mode,
            filters,
            date_filters,
            anniversary_month,
            anniversary_day,
        )
        history_refreshing = False

        if use_month_cache:
            allowed_types = _enabled_history_media_types(request.user)
            entry_filter = partial(
                _cached_history_entry_is_visible,
                filters=filters,
                allowed_types=allowed_types,
            )
            history_days, cache_meta = history_cache.get_month_history(
                request.user,
                view_year,
                view_month,
                logging_style_override=logging_style,
                entry_filter=entry_filter,
            )
            history_days = [
                prepared_day
                for day in history_days
                if (
                    prepared_day := _prepare_history_day_page(
                        day,
                        request.user,
                        filters,
                    )
                )
            ]
            history_refreshing = cache_meta.get("refreshing", False)

            page_obj = None
            current_page = 1
            total_pages = 1
            total_days = len(history_days)

            if view_month == 1:
                prev_year, prev_month = view_year - 1, 12
            else:
                prev_year, prev_month = view_year, view_month - 1
            if view_month == MONTHS_PER_YEAR:
                next_year, next_month = view_year + 1, 1
            else:
                next_year, next_month = view_year, view_month + 1

            prev_month_name = formats.date_format(
                date(prev_year, prev_month, 1),
                "M",
            )
            next_month_name = formats.date_format(
                date(next_year, next_month, 1),
                "M",
            )
            is_current_month = view_year == now.year and view_month == now.month
            show_next_month = next_year < now.year or (
                next_year == now.year and next_month <= now.month
            )
        else:
            try:
                page_number = int(request.GET.get("page", 1))
            except (TypeError, ValueError):
                page_number = 1

            if history_mode == "release":
                history_days_all = _build_release_history_days(
                    request.user,
                    month=anniversary_month,
                    day=anniversary_day,
                    date_filters=date_filters,
                    filters=filters,
                )
                history_refreshing = False
            elif anniversary_month and anniversary_day:
                history_days_all = _build_anniversary_history_days(
                    request.user,
                    month=anniversary_month,
                    day=anniversary_day,
                    logging_style=logging_style,
                )
                history_refreshing = False
            elif date_filters and set(filters).issubset({"media_type"}):
                # Date bounds only drop whole days, so page the day index
                # rather than rebuilding every matching play.
                history_days_all = None
            else:
                history_days_all = history_cache.get_history_days(
                    request.user,
                    filters=filters,
                    date_filters=date_filters,
                    logging_style_override=logging_style,
                )

            if history_days_all is None:
                history_days_all = _IndexedHistoryDays(
                    request.user,
                    filters,
                    date_filters,
                    logging_style,
                )
                page_number = max(page_number, 1)
                paginator = Paginator(
                    history_days_all,
                    history_cache.HISTORY_DAYS_PER_PAGE,
                )
            else:
                history_days_all = _filter_history_by_enabled_media_types(
                    history_days_all,
                    request.user,
                )
                paginator = Paginator(
                    history_days_all,
                    history_cache.HISTORY_DAYS_PER_PAGE,
                )

            if paginator.count == 0:
                page_obj = None
                history_days = []
                current_page = 1
                total_pages = 1
                total_days = 0
            else:
                try:
                    page_obj = paginator.page(page_number)
                except EmptyPage:
                    page_obj = paginator.page(paginator.num_pages)

                history_days = page_obj.object_list
                current_page = page_obj.number
                total_pages = paginator.num_pages
                total_days = paginator.count

            history_days = [
                annotated_day
                for day in history_days
                if (annotated_day := _annotate_history_day_for_template(day))
            ]

            prev_year = prev_month = next_year = next_month = None
            prev_month_name = next_month_name = None
            show_next_month = False
            is_current_month = False

        active_filters = filters.copy()
        if date_filters.get("start_date"):
            active_filters["start-date"] = date_filters["start_date"]
        if date_filters.get("end_date"):
            active_filters["end-date"] = date_filters["end_date"]
        if logging_style:
            active_filters["logging_style"] = logging_style
        if anniversary_month and anniversary_day:
            active_filters["month"] = anniversary_month
            active_filters["day"] = anniversary_day
        if history_mode == "release":
            active_filters["history_mode"] = "release"
        month_nav_query = urlencode(active_filters)
        month_name = (
            formats.date_format(date(view_year, view_month, 1), "F")
            if use_month_cache
            else None
        )

        for day in history_days:
            if day.get("has_more"):
                day["next_entry_query"] = _history_day_fragment_query(
                    request,
                    day["next_entry_offset"],
                )

        if _can_use_cached_month_history("activity", filters, {}, None, None) and (
            history_mode == "activity"
        ):
            # Days below the fold load as they scroll into view: a busy month is
            # several MB of cards, and the day fragment renders the same markup
            # (it takes the day from the request path, so date bounds and the
            # anniversary pick need no handling there).
            for day in history_days[_first_view_day_count(history_days):]:
                day["lazy"] = True
                day["lazy_query"] = _history_day_fragment_query(request, 0)

        context = {
            "user": request.user,
            "history_days": history_days,
            "page_obj": page_obj,
            "current_page": current_page,
            "total_pages": total_pages,
            "total_days": total_days,
            "active_filters": active_filters,
            "history_refreshing": history_refreshing,
            "history_mode": history_mode,
            "media_type": context_media_type,
            "use_month_view": use_month_cache,
            "view_year": view_year,
            "view_month": view_month,
            "month_name": month_name,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "prev_month_name": prev_month_name,
            "next_year": next_year,
            "next_month": next_month,
            "next_month_name": next_month_name,
            "show_next_month": show_next_month,
            "is_current_month": is_current_month,
            "current_year": now.year,
            "current_month_num": now.month,
            "month_nav_query": month_nav_query,
        }
        day_entry_counts = []
        total_entries = 0
        rendered_entries = 0
        for day in history_days:
            entries = (
                day.get("entries", [])
                if isinstance(day, dict)
                else getattr(day, "entries", [])
            )
            count = (
                day.get("entry_count", len(entries))
                if isinstance(day, dict)
                else len(entries)
            )
            total_entries += count
            rendered_entries += len(entries)
            day_entry_counts.append((day.get("date_display") or day.get("date"), count))
        top_days = sorted(day_entry_counts, key=lambda item: item[1], reverse=True)[:3]
        logger.info(
            "history_page_entry_counts user_id=%s page=%s total_entries=%s top_days=%s",
            request.user.id,
            current_page,
            total_entries,
            top_days,
        )
        logger.info(
            "history_page_rendered_entry_counts user_id=%s page=%s rendered_entries=%s",
            request.user.id,
            current_page,
            rendered_entries,
        )
        context["history_feed"] = _history_feed_model(request, context)
        context["history_feed_props"] = {
            "model": context["history_feed"],
            "labels": _history_feed_labels(),
            "chrome": True,
            "baseUrl": request.path,
            "filterOptionsUrl": reverse("history_genres"),
            "saveViewUrl": reverse("saved_view_create"),
            "saveViewPrompt": _("Name this view"),
            "csrfToken": get_token(request),
        }
        render_start = time.perf_counter()
        logger.info(
            "history_render_start user_id=%s page=%s",
            request.user.id,
            current_page,
        )
        response = render(request, "app/history.html", context)
        render_ms = (time.perf_counter() - render_start) * 1000
        response_bytes = len(response.content)
        logger.info(
            "history_render_end user_id=%s page=%s render_ms=%.2f response_bytes=%s",
            request.user.id,
            current_page,
            render_ms,
            response_bytes,
        )
        logger.info(
            "history_view_end user_id=%s page=%s total_days=%s page_days=%s total_pages=%s elapsed_ms=%.2f response_bytes=%s",
            request.user.id,
            current_page,
            total_days,
            len(history_days),
            total_pages,
            (time.perf_counter() - view_start) * 1000,
            response_bytes,
        )
    except OperationalError:
        logger.exception("Database error in history view")
        context = {
            "user": request.user,
            "history_days": [],
            "page_obj": None,
            "current_page": 1,
            "total_pages": 0,
            "total_days": 0,
            "days_per_page": history_cache.HISTORY_DAYS_PER_PAGE,
            "active_filters": {},
            "database_error": True,
            "history_refreshing": False,
            "media_type": context_media_type,
        }
        context["history_feed"] = _history_feed_model(request, context)
        return render(request, "app/history.html", context)
    else:
        return response


@require_GET
def history_day_fragment(request, day_key):
    """Render one bounded page of a month-view history day."""
    normalized_day_key = history_cache.history_day_key(day_key)
    if normalized_day_key != day_key:
        return HttpResponseBadRequest("Invalid history day key.")
    try:
        history_cache._date_from_day_key(normalized_day_key)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid history day key.")

    raw_offset = request.GET.get("entry_offset", "0")
    try:
        entry_offset = int(raw_offset)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid history entry offset.")
    if entry_offset < 0:
        return HttpResponseBadRequest("Invalid history entry offset.")

    filters, logging_style = _parse_history_filters(request)
    if not _can_use_cached_month_history(
        "activity",
        filters,
        date_filters={},
        anniversary_month=None,
        anniversary_day=None,
    ):
        return HttpResponseBadRequest("History filters are not supported here.")

    day = history_cache.get_cached_history_day(
        request.user,
        normalized_day_key,
        logging_style_override=logging_style,
        entry_offset=entry_offset,
        max_entries=history_cache.HISTORY_ENTRIES_PER_DAY_PAGE,
        entry_filter=partial(
            _cached_history_entry_is_visible,
            filters=filters,
            allowed_types=_enabled_history_media_types(request.user),
        ),
    )
    if day is not None and entry_offset > day.get(
        "entry_count", len(day.get("entries", []))
    ):
        return HttpResponseBadRequest("Invalid history entry offset.")
    prepared_day = _prepare_history_day_page(
        day,
        request.user,
        filters,
        offset=entry_offset,
    )
    if prepared_day is None:
        return HttpResponseNotFound("History day not found.")
    prepared_day["next_entry_query"] = _history_day_fragment_query(
        request,
        prepared_day["next_entry_offset"],
    )
    if request.GET.get("format") == "json":
        return JsonResponse(
            {
                "day": _history_feed_day(
                    prepared_day,
                    "activity",
                    request.user,
                    request,
                ),
            },
        )
    return render(
        request,
        "app/components/history_day.html",
        {
            "day": prepared_day,
            "history_mode": "activity",
            "user": request.user,
        },
    )
