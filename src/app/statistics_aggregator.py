"""Range-level statistics aggregator — extracted from statistics_cache.py."""

import calendar
import copy
import heapq
import itertools
import logging
import random as _random
import time
from collections import Counter, defaultdict
from datetime import date as date_type
from datetime import datetime, timedelta

from django.apps import apps
from django.core.cache import cache
from django.db.models import Model
from django.utils import timezone
from django.utils.translation import gettext_noop

from app import config, helpers
from app import statistics as stats
from app.history_cache_utils import history_deferred_item_fields
from app.models import Item, MediaTypes, Status
from app.statistics_day_builder import (
    _day_boundary_datetime,
    build_stats_for_day,
)
from app.statistics_day_cache import _day_cache_key
from app.statistics_highlights import (
    _get_history_day_payload,
    _get_range_history_boundary_days,
    _get_today_history_entries,
    _get_today_release_entry,
    _select_history_entry_for_day,
)
from app.statistics_talent import (
    STATISTICS_TOP_N,
    STATISTICS_TOP_RATED_OVERALL,
    _aggregate_top_talent,
)
from app.templatetags import app_tags

logger = logging.getLogger(__name__)

COIN_FLIP_PROBABILITY = 0.5
SCORE_SCALE_FIVE_POINT = 5


def _tv_season_items(items_by_type):
    """Return season metadata that belongs in the TV bucket, not Anime.

    Season rows are folded into TV so a season-only rating still surfaces.
    Grouped anime seasons are stored as Season rows too, but their parent is a
    TV row in the anime library; folding them into TV leaks the same show into
    both activity cards. ``stats_media_type`` records the bucket the day
    builder attributed the season to.
    """
    seasons = items_by_type.get(MediaTypes.SEASON.value, {})
    return {
        item_id: meta
        for item_id, meta in seasons.items()
        if meta.get("stats_media_type", MediaTypes.SEASON.value)
        != MediaTypes.ANIME.value
    }


def _build_today_card(
    today_in_history,
    today_in_history_year,
    today_in_user_history,
    today_in_user_history_year,
):
    if today_in_history and today_in_user_history:
        use_user = _random.random() < COIN_FLIP_PROBABILITY  # noqa: S311  # sampling/jitter only, not cryptographic
    elif today_in_user_history:
        use_user = True
    else:
        use_user = False
    if use_user:
        return {
            "entry": today_in_user_history,
            "year": today_in_user_history_year,
            "label": gettext_noop("TODAY IN YOUR HISTORY"),
            "date_prefix": gettext_noop("Played in"),
            "use_release_url": False,
        }
    return {
        "entry": today_in_history,
        "year": today_in_history_year,
        "label": gettext_noop("TODAY IN HISTORY"),
        "date_prefix": gettext_noop("Released in"),
        "use_release_url": True,
    }


def _parse_activity_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return stats._localize_datetime(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return stats._localize_datetime(parsed)
    return None


def _compute_metric_breakdown_for_range(total_value, start_date, end_date):
    breakdown = {"total": total_value, "per_year": 0, "per_month": 0, "per_day": 0}
    if not total_value or not start_date or not end_date:
        return breakdown
    start_dt = stats._localize_datetime(start_date)
    end_dt = stats._localize_datetime(end_date)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    total_days = (end_dt.date() - start_dt.date()).days + 1
    if total_days <= 0:
        total_days = 1
    total_years = max(total_days / 365.25, 1)
    total_months = max(total_days / 30.4375, 1)
    breakdown["per_year"] = total_value / total_years if total_years else total_value
    breakdown["per_month"] = total_value / total_months if total_months else total_value
    breakdown["per_day"] = total_value / total_days if total_days else total_value
    return breakdown


def _sum_top_played_plays(top_played_by_type, media_type):
    return sum(
        entry.get("plays", 0)
        for entry in top_played_by_type.get(media_type, {}).values()
    )


def _avg_daily_playtime_hours(total_hours, day_minutes_map):
    days_active = sum(1 for m in day_minutes_map.values() if m and m > 0)
    return round(total_hours / days_active, 2) if days_active else 0


def _build_media_charts_from_counts(
    day_counts, hour_counts, color, dataset_label, *, to_hours=False
):
    empty_chart = {"labels": [], "datasets": []}
    if not day_counts:
        return {
            "by_year": empty_chart,
            "by_month": empty_chart,
            "by_weekday": empty_chart,
            "by_time_of_day": empty_chart,
        }

    def _transform(value):
        return round(value / 60, 2) if to_hours else value

    year_counts = Counter()
    month_counts = Counter()
    weekday_counts = Counter()
    for day_str, count in day_counts.items():
        try:
            day = datetime.strptime(day_str, "%Y-%m-%d").date()  # noqa: DTZ007  # date-only value; no timezone applies
        except ValueError:
            continue
        year_counts[day.year] += count
        month_counts[day.month] += count
        weekday_counts[day.weekday()] += count

    sorted_years = sorted(year_counts)
    year_labels = [str(year) for year in sorted_years]
    year_values = [_transform(year_counts[year]) for year in sorted_years]

    month_labels = [calendar.month_abbr[i] for i in range(1, 13)]
    month_values = [_transform(month_counts.get(i, 0)) for i in range(1, 13)]

    weekday_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    weekday_order = [6, 0, 1, 2, 3, 4, 5]
    weekday_labels = [weekday_map[index] for index in weekday_order]
    weekday_values = [
        _transform(weekday_counts.get(index, 0)) for index in weekday_order
    ]

    hour_labels = [stats._format_hour_label(hour) for hour in range(24)]
    hour_values = [_transform(hour_counts.get(str(hour), 0)) for hour in range(24)]

    return {
        "by_year": stats._build_single_series_chart(
            year_labels, year_values, color, dataset_label
        ),
        "by_month": stats._build_single_series_chart(
            month_labels, month_values, color, dataset_label
        ),
        "by_weekday": stats._build_single_series_chart(
            weekday_labels, weekday_values, color, dataset_label
        ),
        "by_time_of_day": stats._build_single_series_chart(
            hour_labels, hour_values, color, dataset_label
        ),
    }


def _date_to_iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


_EPOCH_DAY = date_type(1970, 1, 1)


def _active_day_runs(day_map):
    """Return one type's active days as [[first_epoch_day, length], ...] runs.

    Lets the page merge several media types and recompute streaks in the
    browser without shipping every active date.
    """
    epoch_days = sorted(
        (date_type.fromisoformat(day) - _EPOCH_DAY).days
        for day, minutes in day_map.items()
        if minutes > 0
    )
    runs = []
    for epoch_day in epoch_days:
        if runs and runs[-1][0] + runs[-1][1] == epoch_day:
            runs[-1][1] += 1
        else:
            runs.append([epoch_day, 1])
    return runs


def _weekday_minutes(day_map, day_list_iso):
    """Return minutes per weekday (Monday first) for days inside the range."""
    totals = [0.0] * 7
    for day, minutes in day_map.items():
        if minutes > 0 and day in day_list_iso:
            totals[date_type.fromisoformat(day).weekday()] += minutes
    return totals


def _build_daily_hours_chart(day_minutes_by_type, day_list):
    labels = [day.isoformat() for day in day_list]
    datasets = []
    ordered_types = list(stats.MEDIA_TYPE_HOURS_ORDER)
    ordered_types.extend(
        [
            media_type
            for media_type in day_minutes_by_type
            if media_type not in ordered_types
        ]
    )
    for media_type in ordered_types:
        minutes_map = day_minutes_by_type.get(media_type)
        if not minutes_map:
            continue
        totals = [minutes_map.get(label, 0) for label in labels]
        if not totals or sum(totals) == 0:
            continue
        datasets.append(
            {
                "label": app_tags.media_type_readable(media_type),
                "media_type": media_type,
                "data": [round(minutes / 60, 2) for minutes in totals],
                "background_color": config.get_stats_color(media_type),
            }
        )
    return {"labels": labels, "datasets": datasets}


def _build_activity_data(
    date_counts,
    date_type_counts,
    day_minutes_by_type,
    day_list,
    start_date,
    end_date,
    *,
    week_start_sunday=False,
):
    """Build activity data for the calendar heatmap and stats cards.

    Args:
        date_counts: Dict mapping date -> activity count (for heatmap)
        date_type_counts: Dict mapping date -> {media_type: activity count}
        day_minutes_by_type: Dict mapping media_type -> {date_iso_str -> minutes}
        day_list: List of date objects in the filtered range
        start_date: Start of the date range
        end_date: End of the date range
    """
    if end_date is None:
        end_date = timezone.localtime()

    if start_date is None and date_counts:
        min_date = min(date_counts)
        start_date = _day_boundary_datetime(min_date)

    start_date_aligned = stats.get_aligned_week_start(
        start_date, week_start_sunday=week_start_sunday
    )
    if start_date_aligned is None:
        return {
            "calendar_weeks": [],
            "months": [],
            "weekday_labels": [],
            "stats": {
                "most_active_day": None,
                "most_active_day_percentage": 0,
                "current_streak": 0,
                "longest_streak": 0,
                "longest_streak_start": None,
                "longest_streak_end": None,
            },
        }

    date_range = [
        start_date_aligned.date() + timedelta(days=offset)
        for offset in range((end_date.date() - start_date_aligned.date()).days + 1)
    ]

    # Use day_minutes_by_type for most active day calculation (same data as chart)
    most_active_day, day_percentage = stats.calculate_most_active_weekday(
        day_minutes_by_type,
        day_list,
    )
    end_date_value = end_date.date() if hasattr(end_date, "date") else end_date
    streaks = stats.calculate_streak_details(
        date_counts,
        end_date_value,
    )

    activity_data = [
        {
            "date": current_date.strftime("%Y-%m-%d"),
            "count": date_counts.get(current_date, 0),
            "level": stats.get_level(date_counts.get(current_date, 0)),
            # Lets the page re-colour the heatmap for the selected media types.
            "by_type": date_type_counts.get(current_date, {}),
        }
        for current_date in date_range
    ]

    calendar_weeks = [activity_data[i : i + 7] for i in range(0, len(activity_data), 7)]

    base_weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekday_labels = (
        [base_weekdays[6], *base_weekdays[:6]] if week_start_sunday else base_weekdays
    )
    week_start_weekday = 6 if week_start_sunday else 0

    months = []
    mondays_per_month = []
    current_month = date_range[0].strftime("%b") if date_range else None
    monday_count = 0

    for current_date in date_range:
        if current_date.weekday() == week_start_weekday:
            month = current_date.strftime("%b")
            if current_month != month:
                if current_month is not None:
                    months.append(current_month if monday_count > 1 else "")
                    mondays_per_month.append(monday_count)
                current_month = month
                monday_count = 0
            monday_count += 1

    if monday_count > 1:
        months.append(current_month)
        mondays_per_month.append(monday_count)

    return {
        "calendar_weeks": calendar_weeks,
        "months": list(zip(months, mondays_per_month, strict=False)),
        "weekday_labels": weekday_labels,
        "stats": {
            "most_active_day": most_active_day,
            "most_active_day_percentage": day_percentage,
            "current_streak": streaks["current_streak"],
            "longest_streak": streaks["longest_streak"],
            "longest_streak_start": streaks["longest_streak_start"],
            "longest_streak_end": streaks["longest_streak_end"],
        },
    }


def _fetch_media_objects(media_refs):
    media_objects = {}
    by_type = defaultdict(set)
    for media_type, media_id in media_refs:
        if media_type and media_id:
            by_type[media_type].add(media_id)

    for media_type, media_ids in by_type.items():
        model = apps.get_model("app", media_type)
        item_paths = ("item",)
        if media_type == MediaTypes.SEASON.value:
            item_paths = ("item", "related_tv__item")
        elif media_type == MediaTypes.EPISODE.value:
            item_paths = (
                "item",
                "related_season__item",
                "related_season__related_tv__item",
            )
        # These rows are stored in the cached range payload. The cards read a
        # few item columns, never the wide ones (watch_providers alone is
        # ~146 KiB a title), so leave those out of every item the row carries.
        queryset = (
            model.objects.filter(id__in=media_ids)
            .select_related(*item_paths)
            .defer(*history_deferred_item_fields(*item_paths))
        )
        found_ids = set()
        for media in queryset.iterator(chunk_size=500):
            media_objects[(media_type, media.id)] = media
            found_ids.add(media.id)

        # Grouped anime (TV shows with library_media_type='anime') are stored in the TV
        # table, so any anime IDs not found in the Anime model belong to TV instances.
        if media_type == MediaTypes.ANIME.value:
            missing_ids = media_ids - found_ids
            if missing_ids:
                TV = apps.get_model("app", MediaTypes.TV.value)
                grouped = (
                    TV.objects.filter(id__in=missing_ids)
                    .select_related("item")
                    .defer(*history_deferred_item_fields("item"))
                )
                for tv in grouped:
                    # Grouped anime is stored on TV rows; without the route
                    # annotation the shared media card links it to /tv/.
                    tv.route_media_type = MediaTypes.ANIME.value
                    if getattr(tv, "item", None) is not None:
                        tv.item.route_media_type = MediaTypes.ANIME.value
                    media_objects[(media_type, tv.id)] = tv

    return media_objects


def _detached_copy(instance):
    """Copy a loaded row and the related rows it carries.

    A range sets per-range attributes on its rows (``aggregated_score``), so a
    row shared by several ranges, or by two cards of one range, is handed out
    as a copy. Related rows (the item, a season's show) are copied too, so a
    copy never shares an object with another card. Prefetched collections are
    shared; nothing writes to them.
    """
    clone = copy.copy(instance)
    fields_cache = clone._state.fields_cache
    for name, related in fields_cache.items():
        if isinstance(related, Model):
            fields_cache[name] = _detached_copy(related)
    return clone


def _shared_rows(memo, kind, refs, load):
    """Return ``{ref: row}`` for ``refs``, loading only refs not seen before.

    ``memo`` lives for one sync pass (or one aggregate): every range of the
    pass asks for many of the same rows, and loading a row does not depend on
    the range. ``load(refs)`` returns ``{ref: row}`` and may leave out refs it
    cannot find; those are remembered as absent. The stored rows are never
    handed out, only copies of them.
    """
    store = memo.setdefault(kind, {})
    missing = [ref for ref in refs if ref not in store]
    if missing:
        loaded = load(missing)
        for ref in missing:
            store[ref] = loaded.get(ref)
    return {ref: _detached_copy(store[ref]) for ref in refs if store[ref] is not None}


def _shared_media_objects(memo, media_refs):
    """``_fetch_media_objects`` through the pass memo."""
    refs = {
        (media_type, media_id)
        for media_type, media_id in media_refs
        if media_type and media_id
    }
    return _shared_rows(memo, "media_rows", refs, _fetch_media_objects)


def _aggregate_minutes_per_media_type_from_days(
    user,
    day_list,
    *,
    build_missing=False,
    build_days=None,
    missing_days=None,
):
    """Add up ``minutes_by_type`` across the range's day payloads.

    ``build_days(days)`` builds and caches a batch of missing days, as in
    ``_walk_days``; without it a missing day is built alone when
    ``build_missing`` is set. Days still without a payload are appended to
    ``missing_days`` when given.
    """
    minutes_by_type = defaultdict(float)
    if not day_list:
        return {}

    chunk_size = 50
    for offset in range(0, len(day_list), chunk_size):
        chunk = day_list[offset : offset + chunk_size]
        key_map = {day: _day_cache_key(user.id, day) for day in chunk}
        cached = cache.get_many(key_map.values())
        if build_days is not None:
            absent = [day for day in chunk if key_map[day] not in cached]
            if absent:
                build_days(absent)
                cached.update(cache.get_many([key_map[day] for day in absent]))
        for day in chunk:
            cache_key = key_map[day]
            day_stats = cached.pop(cache_key, None)
            if not day_stats and build_missing:
                day_stats = build_stats_for_day(user.id, day)
            if not day_stats:
                if missing_days is not None:
                    missing_days.append(day)
                continue

            for media_type, minutes in (
                day_stats.get("totals", {}).get("minutes_by_type", {}).items()
            ):
                minutes_by_type[media_type] += minutes or 0

    return dict(minutes_by_type)


def _empty_top_talent_payload(sort_by="plays"):
    empty_bucket = {
        "top_actors": [],
        "top_actresses": [],
        "top_directors": [],
        "top_writers": [],
        "top_studios": [],
    }
    by_sort = {
        "plays": dict(empty_bucket),
        "time": dict(empty_bucket),
        "titles": dict(empty_bucket),
    }
    return {
        "sort_by": sort_by,
        "by_sort": by_sort,
        **by_sort.get(sort_by, dict(empty_bucket)),
    }


def _has_dateless_movie_or_episode_activity(user) -> bool:
    """Check for movie/episode rows with no date, missed by day-bucketed play counts.

    The day-based cache only counts a movie/episode toward `plays_by_type` on
    a day its query can place it on (an exact end_date, or a start/end
    overlap range); an entry with no date at all never lands in any day's
    bucket. For "All Time", such entries are still valid activity (see
    `_require_movie_or_game_date` in `statistics_talent.py`), so top talent
    must not be skipped just because day-bucketed counts saw nothing.
    """
    Movie = apps.get_model("app", "Movie")
    if Movie.objects.filter(
        user=user, start_date__isnull=True, end_date__isnull=True
    ).exists():
        return True
    Episode = apps.get_model("app", "Episode")
    return Episode.objects.filter(
        Episode.watch_q(), related_season__user=user, end_date__isnull=True
    ).exists()


def _listening_payload(minutes):
    """Audiobook listening time for a range, kept apart from page counts."""
    if not minutes:
        return None
    return {
        "minutes": minutes,
        "formatted": helpers.minutes_to_hhmm(minutes),
    }


def _empty_reading_consumption(unit_name="Unit", completion_label="Items Finished"):
    return {
        "units": {"total": 0, "per_year": 0, "per_month": 0, "per_day": 0},
        "completions": {"total": 0, "per_year": 0, "per_month": 0, "per_day": 0},
        "charts": {
            "by_year": {"labels": [], "datasets": []},
            "by_month": {"labels": [], "datasets": []},
            "by_weekday": {"labels": [], "datasets": []},
            "by_time_of_day": {"labels": [], "datasets": []},
        },
        "completion_charts": {
            "by_year": {"labels": [], "datasets": []},
            "by_month": {"labels": [], "datasets": []},
        },
        "completed_length_chart": {"labels": [], "datasets": []},
        "release_chart": {"labels": [], "datasets": []},
        "completed_lengths": [],
        "listening": None,
        "has_data": False,
        "unit_name": unit_name,
        "unit_label": f"{unit_name}s Read",
        "completion_label": completion_label,
        "top_items": [],
        "top_authors": [],
        "top_genres": [],
        "highlights": {
            "longest_item": None,
            "shortest_item": None,
            "average_length": 0,
            "average_rating": None,
        },
    }


def _build_platform_breakdown(game_data, collection_platform_map, item_info_map):
    """Summarize game time using collection choices before unambiguous metadata."""
    platform_hours = defaultdict(float)
    platform_game_ids = defaultdict(set)
    for item in game_data:
        item_id = item.get("item_id")
        if item_id is None:
            continue
        hours = item["hours"]
        # Priority 1: collection entry resolution
        platform = collection_platform_map.get(item_id)
        # Priority 2: single IGDB platform from Item.platforms
        if not platform:
            raw_platforms = item_info_map.get(item_id, {}).get("platforms") or []
            if isinstance(raw_platforms, list):
                cleaned = [str(p).strip() for p in raw_platforms if str(p).strip()]
            else:
                cleaned = []
            if len(cleaned) == 1:
                platform = cleaned[0]
        if not platform:
            continue
        platform_hours[platform] += hours
        platform_game_ids[platform].add(item_id)

    return sorted(
        [
            {
                "name": name,
                "games": len(platform_game_ids[name]),
                "hours": hours,
                "formatted_hours": helpers.minutes_to_hhmm(hours * 60),
            }
            for name, hours in platform_hours.items()
        ],
        key=lambda x: x["hours"],
        reverse=True,
    )


def _build_weekday_hour_charts(weekday_hour_counts):
    """Build per-type and combined heatmaps from already aggregated activity."""

    def _build_weekday_hour_matrix(wh_counts):
        return [[wh_counts[wd][h] for h in range(24)] for wd in range(7)]

    all_wh: defaultdict = defaultdict(lambda: defaultdict(int))
    for mt_counts in weekday_hour_counts.values():
        for wd, hours in mt_counts.items():
            for h, count in hours.items():
                all_wh[wd][h] += count

    weekday_hour_chart_data = {"all": _build_weekday_hour_matrix(all_wh)}
    for mt, wh in weekday_hour_counts.items():
        weekday_hour_chart_data[mt] = _build_weekday_hour_matrix(wh)

    return weekday_hour_chart_data


def _build_combined_hours_charts(day_minutes_by_type, hour_minutes):
    """Prepare comparable hour charts for media measured in elapsed time."""
    combined_hours_media_types = (
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.ANIME.value,
        MediaTypes.MUSIC.value,
        MediaTypes.PODCAST.value,
        MediaTypes.VIDEO.value,
    )

    merged_day_minutes: defaultdict = defaultdict(float)
    merged_hour_minutes: defaultdict = defaultdict(float)
    per_type_hours_charts = {}
    for media_type in combined_hours_media_types:
        type_day_minutes = day_minutes_by_type.get(media_type, {})
        type_hour_minutes = hour_minutes.get(media_type, {})
        per_type_hours_charts[media_type] = _build_media_charts_from_counts(
            type_day_minutes,
            type_hour_minutes,
            config.get_stats_color(media_type),
            "Hours",
            to_hours=True,
        )
        for day_str, minutes in type_day_minutes.items():
            merged_day_minutes[day_str] += minutes or 0
        for hour, minutes in type_hour_minutes.items():
            merged_hour_minutes[hour] += minutes or 0

    all_hours_chart = _build_media_charts_from_counts(
        dict(merged_day_minutes),
        dict(merged_hour_minutes),
        "#6366f1",
        "Hours",
        to_hours=True,
    )

    combined_hours_charts = {
        "by_month": {"all": all_hours_chart["by_month"]},
        "by_weekday": {"all": all_hours_chart["by_weekday"]},
        "by_time_of_day": {"all": all_hours_chart["by_time_of_day"]},
    }
    for media_type, chart in per_type_hours_charts.items():
        combined_hours_charts["by_month"][media_type] = chart["by_month"]
        combined_hours_charts["by_weekday"][media_type] = chart["by_weekday"]
        combined_hours_charts["by_time_of_day"][media_type] = chart["by_time_of_day"]

    return combined_hours_charts


class _PhaseClock:
    """Wall and CPU time per phase of one range aggregate.

    Costs two clock reads per phase. ``timings`` is the caller's dict; with
    none the clock records nothing.
    """

    __slots__ = ("_cpu", "_wall", "timings")

    def __init__(self, timings):
        self.timings = timings
        self._wall = time.monotonic()
        self._cpu = time.process_time()

    def mark(self, phase):
        if self.timings is None:
            return
        wall = time.monotonic()
        cpu = time.process_time()
        timings = self.timings
        wall_key = f"{phase}_ms"
        cpu_key = f"{phase}_cpu_ms"
        timings[wall_key] = timings.get(wall_key, 0.0) + (wall - self._wall) * 1000
        timings[cpu_key] = timings.get(cpu_key, 0.0) + (cpu - self._cpu) * 1000
        self._wall = wall
        self._cpu = cpu


_NON_PLAY_ACTIVITY_TYPES = {
    MediaTypes.ANIME.value,
    MediaTypes.GAME.value,
    MediaTypes.BOARDGAME.value,
    MediaTypes.MANGA.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
}

_READING_MEDIA_TYPES = (
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.MANGA.value,
)


def _play_rollup():
    return defaultdict(lambda: {"minutes": 0, "plays": 0, "name": ""})


def _decade_rollup():
    return defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""})


def _reading_genre_rollup():
    return defaultdict(lambda: {"units": 0, "titles": 0, "name": ""})


class _RangeAccumulators:
    """What the walk over one range's day payloads adds up.

    The day walk only writes these; each finishing phase reads the few it
    needs. One instance per range: nested defaults hold mutable dicts and
    sets, so two ranges must never share one.
    """

    __slots__ = (
        "activity_counts",
        "activity_counts_by_type",
        "anime_decades",
        "anime_genres",
        "day_minutes_by_type",
        "day_play_counts",
        "game_decades",
        "game_genres",
        "game_rollups",
        "hour_counts",
        "hour_minutes",
        "items_by_type",
        "listening_minutes_total",
        "minutes_by_type",
        "movie_decades",
        "movie_genres",
        "music_rollups",
        "plays_by_type",
        "podcast_rollups",
        "reading_genres",
        "top_played_by_type",
        "tv_decades",
        "tv_genres",
        "weekday_hour_counts",
    )

    def __init__(self):
        self.items_by_type = defaultdict(dict)
        self.top_played_by_type = defaultdict(dict)
        self.minutes_by_type = defaultdict(float)
        self.plays_by_type = defaultdict(int)
        self.hour_counts = defaultdict(lambda: defaultdict(int))
        self.hour_minutes = defaultdict(lambda: defaultdict(float))
        self.weekday_hour_counts = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        self.day_play_counts = defaultdict(dict)
        self.day_minutes_by_type = defaultdict(dict)
        self.listening_minutes_total = 0
        self.movie_genres = _play_rollup()
        self.tv_genres = _play_rollup()
        self.anime_genres = _play_rollup()
        self.game_genres = defaultdict(
            lambda: {"minutes": 0, "plays": 0, "name": "", "game_ids": set()}
        )
        self.movie_decades = _decade_rollup()
        self.tv_decades = _decade_rollup()
        self.anime_decades = _decade_rollup()
        self.game_decades = defaultdict(
            lambda: {"minutes": 0, "label": "", "game_ids": set()}
        )
        self.reading_genres = {
            media_type: _reading_genre_rollup() for media_type in _READING_MEDIA_TYPES
        }
        self.music_rollups = {
            "artists": defaultdict(
                lambda: {"minutes": 0, "plays": 0, "name": "", "image": "", "id": None}
            ),
            "albums": defaultdict(
                lambda: {
                    "minutes": 0,
                    "plays": 0,
                    "title": "",
                    "artist": "",
                    "artist_id": None,
                    "artist_name": "",
                    "image": "",
                    "id": None,
                },
            ),
            "tracks": defaultdict(
                lambda: {
                    "minutes": 0,
                    "plays": 0,
                    "title": "",
                    "artist": "",
                    "album": "",
                    "album_image": "",
                    "album_id": None,
                    "album_artist_id": None,
                    "album_artist_name": "",
                    "id": None,
                },
            ),
            "genres": defaultdict(lambda: {"minutes": 0, "plays": 0, "name": ""}),
            "decades": defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""}),
            "countries": defaultdict(
                lambda: {"minutes": 0, "plays": 0, "code": "", "name": ""}
            ),
        }
        self.podcast_rollups = {
            "shows": defaultdict(
                lambda: {
                    "minutes": 0,
                    "plays": 0,
                    "title": "",
                    "show": "",
                    "show_id": None,
                    "podcast_uuid": None,
                    "slug": "",
                    "image": "",
                }
            ),
            "episodes": defaultdict(
                lambda: {
                    "title": "",
                    "show": "",
                    "show_id": None,
                    "episode_id": None,
                    "podcast_uuid": None,
                    "slug": "",
                    "image": "",
                    "duration_seconds": 0,
                }
            ),
        }
        self.game_rollups = {}
        self.activity_counts = {}
        self.activity_counts_by_type = {}


# --- Day walk -------------------------------------------------------------


def _walk_days(
    acc,
    user,
    day_list,
    *,
    build_missing,
    prebuilt_days,
    build_days,
    clock,
    timings,
    missing_days=None,
):
    """Read the range's day payloads 50 at a time and merge each into ``acc``.

    Returns the credit backfill hints of the days it had to build. Days with
    no payload (not built yet) are appended to ``missing_days`` when given.
    """
    credit_backfill_hints = 0
    # Day payloads built moments ago by the caller (e.g. a full refresh) are
    # passed in-memory so we don't re-fetch and unpickle them from the cache.
    prebuilt_days = prebuilt_days or {}
    chunk_size = 50
    for offset in range(0, len(day_list), chunk_size):
        chunk = day_list[offset : offset + chunk_size]
        key_map = {day: _day_cache_key(user.id, day) for day in chunk}
        fetch_keys = [key_map[day] for day in chunk if day not in prebuilt_days]
        cached = cache.get_many(fetch_keys) if fetch_keys else {}
        if build_days is not None:
            absent = [
                day
                for day in chunk
                if day not in prebuilt_days and key_map[day] not in cached
            ]
            if absent:
                credit_backfill_hints += int(build_days(absent) or 0)
                cached.update(cache.get_many([key_map[day] for day in absent]))
        clock.mark("day_fetch")
        if timings is not None:
            timings["days_decoded"] = timings.get("days_decoded", 0) + len(cached)
        for day in chunk:
            cache_key = key_map[day]
            day_stats = prebuilt_days.get(day) or cached.pop(cache_key, None)
            if not day_stats and build_missing:
                day_stats = build_stats_for_day(user.id, day)
                if day_stats:
                    credit_backfill_hints += int(
                        day_stats.get("backfill", {}).get("missing_credits") or 0,
                    )
            if not day_stats:
                if missing_days is not None:
                    missing_days.append(day)
                continue
            _merge_day(acc, day, day_stats)
        clock.mark("day_merge")
    return credit_backfill_hints


def _merge_item_meta(items_by_type, media_type, item_id, meta):
    activity_dt = _parse_activity_dt(meta.get("activity_dt"))
    score_dt = _parse_activity_dt(meta.get("score_dt"))
    if score_dt is None and meta.get("score") is not None:
        score_dt = activity_dt

    existing = items_by_type[media_type].get(item_id)
    if not existing:
        items_by_type[media_type][item_id] = {
            "item_id": item_id,
            "media_id": meta.get("media_id"),
            "media_type": meta.get("media_type"),
            "stats_media_type": meta.get("stats_media_type"),
            "status": meta.get("status"),
            "score": meta.get("score"),
            "score_dt": score_dt,
            "activity_dt": activity_dt,
        }
        return

    existing_activity = existing.get("activity_dt")
    if activity_dt and (not existing_activity or activity_dt > existing_activity):
        existing["media_id"] = meta.get("media_id") or existing.get("media_id")
        existing["status"] = meta.get("status")
        existing["activity_dt"] = activity_dt

    if meta.get("score") is not None:
        existing_score_dt = existing.get("score_dt")
        if existing_score_dt is None or (score_dt and score_dt > existing_score_dt):
            existing["score"] = meta.get("score")
            existing["score_dt"] = score_dt

    items_by_type[media_type][item_id] = existing


def _merge_top_played_entry(top_played_by_type, media_type, item_id, entry):
    aggregate = top_played_by_type[media_type].get(item_id)
    if not aggregate:
        aggregate = {
            "item_id": item_id,
            "media_id": entry.get("media_id"),
            "minutes": 0.0,
            "plays": 0,
            "episode_count": 0,
            "activity_dt": None,
        }
        top_played_by_type[media_type][item_id] = aggregate
    aggregate["minutes"] += entry.get("minutes") or 0
    aggregate["plays"] += entry.get("plays") or 0
    aggregate["episode_count"] += entry.get("episode_count") or 0
    activity_dt = _parse_activity_dt(entry.get("activity_dt"))
    if activity_dt and (
        aggregate["activity_dt"] is None or activity_dt > aggregate["activity_dt"]
    ):
        aggregate["activity_dt"] = activity_dt
        if entry.get("media_id"):
            aggregate["media_id"] = entry.get("media_id")


_MUSIC_FILL_FIELDS = (
    "name",
    "image",
    "id",
    "title",
    "artist",
    "artist_id",
    "artist_name",
    "album",
    "album_image",
    "album_id",
    "album_artist_id",
    "album_artist_name",
    "label",
    "code",
)
_PODCAST_SHOW_FILL_FIELDS = (
    "title",
    "show",
    "show_id",
    "podcast_uuid",
    "slug",
    "image",
)
_PODCAST_EPISODE_FILL_FIELDS = (
    "title",
    "show",
    "show_id",
    "episode_id",
    "podcast_uuid",
    "slug",
    "image",
)


def _merge_rollups(acc, day_stats):
    """Genre, decade, music, podcast and game rollups of one day."""
    genres = day_stats.get("genres", {})
    for rollup, key in (
        (acc.movie_genres, "movie"),
        (acc.tv_genres, "tv"),
        (acc.anime_genres, "anime"),
    ):
        for genre, payload in genres.get(key, {}).items():
            rollup[genre]["minutes"] += payload.get("minutes", 0)
            rollup[genre]["plays"] += payload.get("plays", 0)
            rollup[genre]["name"] = payload.get("name") or genre

    for genre, payload in genres.get("game", {}).items():
        acc.game_genres[genre]["minutes"] += payload.get("minutes", 0)
        acc.game_genres[genre]["plays"] += payload.get("plays", 0)
        acc.game_genres[genre]["name"] = payload.get("name") or genre
        for game_id in payload.get("game_ids", []):
            acc.game_genres[genre]["game_ids"].add(str(game_id))

    decades = day_stats.get("decades", {})
    for rollup, key in (
        (acc.movie_decades, "movie"),
        (acc.tv_decades, "tv"),
        (acc.anime_decades, "anime"),
    ):
        for label, payload in decades.get(key, {}).items():
            rollup[label]["minutes"] += payload.get("minutes", 0)
            rollup[label]["plays"] += payload.get("plays", 0)
            rollup[label]["label"] = payload.get("label") or label

    for label, payload in decades.get("game", {}).items():
        acc.game_decades[label]["minutes"] += payload.get("minutes", 0)
        acc.game_decades[label]["label"] = payload.get("label") or label
        for game_id in payload.get("game_ids", []):
            acc.game_decades[label]["game_ids"].add(str(game_id))

    for reading_type in _READING_MEDIA_TYPES:
        rollup = acc.reading_genres[reading_type]
        for genre, payload in genres.get(reading_type, {}).items():
            rollup[genre]["units"] += payload.get("units", 0)
            rollup[genre]["titles"] += payload.get("titles", 0)
            rollup[genre]["name"] = payload.get("name") or genre

    for key, value in day_stats.get("music", {}).items():
        for item_id_str, payload in value.items():
            existing = acc.music_rollups[key][item_id_str]
            existing["minutes"] += payload.get("minutes", 0)
            existing["plays"] += payload.get("plays", 0)
            for field in _MUSIC_FILL_FIELDS:
                if payload.get(field) and not existing.get(field):
                    existing[field] = payload.get(field)

    for key, value in day_stats.get("podcast", {}).items():
        for item_id_str, payload in value.items():
            existing = acc.podcast_rollups[key][item_id_str]
            if key == "shows":
                existing["minutes"] += payload.get("minutes", 0)
                existing["plays"] += payload.get("plays", 0)
                fill_fields = _PODCAST_SHOW_FILL_FIELDS
            else:
                duration = payload.get("duration_seconds", 0) or 0
                if duration > existing.get("duration_seconds", 0):
                    existing["duration_seconds"] = duration
                fill_fields = _PODCAST_EPISODE_FILL_FIELDS
            for field in fill_fields:
                if payload.get(field) and not existing.get(field):
                    existing[field] = payload.get(field)

    for item_id_str, payload in day_stats.get("game", {}).get("by_game", {}).items():
        try:
            item_id = int(item_id_str)
        except (TypeError, ValueError):
            continue
        existing = acc.game_rollups.get(item_id)
        if not existing:
            existing = {
                "minutes_total": 0,
                "days": 0,
                "activity_dt": None,
                "media_id": payload.get("media_id"),
            }
            acc.game_rollups[item_id] = existing
        existing["minutes_total"] += payload.get("minutes_total", 0)
        existing["days"] += payload.get("days", 0)
        activity_dt = _parse_activity_dt(payload.get("activity_dt"))
        if activity_dt and (
            existing["activity_dt"] is None or activity_dt > existing["activity_dt"]
        ):
            existing["activity_dt"] = activity_dt
            if payload.get("media_id"):
                existing["media_id"] = payload.get("media_id")


def _merge_day(acc, day, day_stats):
    """Add one day payload to the range's accumulators."""
    for media_type, items in day_stats.get("items", {}).items():
        for item_id_str, meta in items.items():
            try:
                item_id = int(item_id_str)
            except (TypeError, ValueError):
                continue
            _merge_item_meta(acc.items_by_type, media_type, item_id, meta)

    for media_type, items in day_stats.get("top_played", {}).items():
        for item_id_str, entry in items.items():
            try:
                item_id = int(item_id_str)
            except (TypeError, ValueError):
                continue
            _merge_top_played_entry(acc.top_played_by_type, media_type, item_id, entry)

    totals = day_stats.get("totals", {})
    day_iso = day.isoformat()
    for media_type, minutes in totals.get("minutes_by_type", {}).items():
        acc.minutes_by_type[media_type] += minutes or 0
    acc.listening_minutes_total += totals.get("listening_minutes") or 0
    for media_type, plays in totals.get("plays_by_type", {}).items():
        acc.plays_by_type[media_type] += plays or 0
        acc.day_play_counts[media_type][day_iso] = plays

    weekday = day.weekday()
    for media_type, hours in day_stats.get("hour_counts", {}).items():
        if not hours:
            # Touching the defaultdicts would add an empty heatmap row.
            continue
        type_hours = acc.hour_counts[media_type]
        type_weekday = acc.weekday_hour_counts[media_type][weekday]
        for hour, count in hours.items():
            type_hours[hour] += count
            type_weekday[int(hour)] += count

    for media_type, hours in day_stats.get("hour_minutes", {}).items():
        if not hours:
            continue
        type_minutes = acc.hour_minutes[media_type]
        for hour, minutes in hours.items():
            type_minutes[hour] += minutes or 0

    for media_type, minutes in day_stats.get("daily_minutes_by_type", {}).items():
        acc.day_minutes_by_type[media_type][day_iso] = minutes

    _merge_rollups(acc, day_stats)

    daily_minutes = day_stats.get("daily_minutes_by_type", {}) or {}
    plays_total = sum(totals.get("plays_by_type", {}).values())
    activity_total = plays_total
    # Same rule as the total, kept per media type so the heatmap can
    # follow the media-type filter.
    by_type = {
        media_type: plays
        for media_type, plays in totals.get("plays_by_type", {}).items()
        if plays
    }
    for media_type in _NON_PLAY_ACTIVITY_TYPES:
        if daily_minutes.get(media_type, 0):
            activity_total += 1
            by_type[media_type] = by_type.get(media_type, 0) + 1
    if activity_total == 0 and sum(daily_minutes.values()) > 0:
        activity_total = 1
        busiest = max(daily_minutes, key=daily_minutes.get)
        by_type[busiest] = 1
    acc.activity_counts[day] = activity_total
    if by_type:
        acc.activity_counts_by_type[day] = by_type


# --- Media types and undated rows ------------------------------------------


def _active_media_types(user, acc):
    """Return the user's active media types, without seasons if switched off."""
    active_types = list(getattr(user, "get_active_media_types", list)())
    if not active_types:
        active_types = list(MediaTypes.values)

    if not user.season_enabled and MediaTypes.SEASON.value in active_types:
        active_types = [mt for mt in active_types if mt != MediaTypes.SEASON.value]
        acc.items_by_type.pop(MediaTypes.SEASON.value, None)
        acc.top_played_by_type.pop(MediaTypes.SEASON.value, None)
    return active_types


_UNDATED_MODELS = (
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOARDGAME.value,
    MediaTypes.MUSIC.value,
    MediaTypes.PODCAST.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
)


def _merge_undated_rows(acc, user, active_types):
    """Add All Time's tracker rows that have no date at all.

    Entries without any start/end date can never land in a calendar-day
    bucket, so the day-builder pipeline never sees them. Pick them up here
    directly so undated items (e.g. Planning-status items that haven't been
    started, or imports missing dates) still count towards status, score and
    media-count aggregates. They belong to All Time only: a date range holds
    just the activity that happened inside it (#1521).
    TV is excluded here: it (and Season) override start_date/end_date as
    computed properties derived from Episode rows, not real DB columns, so
    they can't be filtered on directly and raise FieldError if added back.
    """
    items_by_type = acc.items_by_type
    for media_type in _UNDATED_MODELS:
        if media_type not in active_types:
            continue
        model = apps.get_model("app", media_type)
        qs = model.objects.filter(
            user=user,
            start_date__isnull=True,
            end_date__isnull=True,
            status__in=[
                Status.IN_PROGRESS.value,
                Status.COMPLETED.value,
                Status.DROPPED.value,
                Status.PAUSED.value,
                Status.PLANNING.value,
            ],
        ).values("id", "item_id", "status", "score", "created_at")
        for row in qs.iterator(chunk_size=500):
            activity_dt = _parse_activity_dt(row.get("created_at"))
            score = row.get("score")
            score_dt = activity_dt if score is not None else None
            existing = items_by_type[media_type].get(row["item_id"])
            if not existing:
                items_by_type[media_type][row["item_id"]] = {
                    "item_id": row["item_id"],
                    "media_id": row["id"],
                    "media_type": media_type,
                    "status": row.get("status"),
                    "score": float(score) if score is not None else None,
                    "score_dt": score_dt,
                    "activity_dt": activity_dt,
                }
                continue

            existing_activity = existing.get("activity_dt")
            if activity_dt and (
                not existing_activity or activity_dt > existing_activity
            ):
                existing["media_id"] = row["id"] or existing.get("media_id")
                existing["status"] = row.get("status")
                existing["activity_dt"] = activity_dt

            if score is not None:
                existing_score_dt = existing.get("score_dt")
                if existing_score_dt is None or (
                    score_dt and score_dt > existing_score_dt
                ):
                    existing["score"] = float(score)
                    existing["score_dt"] = score_dt

            items_by_type[media_type][row["item_id"]] = existing


def _merge_album_trackers(acc, user, active_types, start_date, end_date, is_all_time_query):
    """Add the user's album trackers to the Music items.

    Album trackers are separate from Music track plays. They count in
    library and score statistics without inventing plays or listening time.
    """
    if MediaTypes.MUSIC.value not in active_types:
        return
    items_by_type = acc.items_by_type
    AlbumTracker = apps.get_model("app", "AlbumTracker")
    albums = list(
        AlbumTracker.objects.filter(user=user)
        .select_related("album")
        .order_by("-updated_at", "-pk")
    )
    release_ids = {
        row.album.musicbrainz_release_id
        for row in albums
        if row.album.musicbrainz_release_id
    }
    Album = apps.get_model("app", "Album")
    release_groups = dict(
        Album.objects.filter(musicbrainz_release_id__in=release_ids)
        .exclude(musicbrainz_release_group_id__isnull=True)
        .exclude(musicbrainz_release_group_id="")
        .values_list("musicbrainz_release_id", "musicbrainz_release_group_id")
    )
    seen_albums = set()
    for tracker in albums:
        album = tracker.album
        identity = (
            album.musicbrainz_release_group_id
            or release_groups.get(album.musicbrainz_release_id)
            or album.musicbrainz_release_id
            or f"local:{album.pk}"
        )
        if identity in seen_albums:
            continue
        seen_albums.add(identity)
        dates = [value for value in (tracker.start_date, tracker.end_date) if value]
        if not is_all_time_query and not any(
            (start_date is None or value >= start_date)
            and (end_date is None or value <= end_date)
            for value in dates
        ):
            continue
        items_by_type[MediaTypes.MUSIC.value][f"album:{identity}"] = {
            "item_id": None,
            "media_id": None,
            "media_type": MediaTypes.MUSIC.value,
            "status": tracker.status,
            "score": float(tracker.score) if tracker.score is not None else None,
            "activity_dt": tracker.end_date or tracker.start_date or tracker.created_at,
        }


# --- Status, scores and top rated ------------------------------------------


def _distribution_items(items_by_type, media_type):
    """One type's items; TV also holds the seasons that roll up into it."""
    items = dict(items_by_type.get(media_type, {}))
    if media_type == MediaTypes.TV.value:
        items.update(_tv_season_items(items_by_type))
    return items


def _build_status_distribution(acc, active_types):
    """Media counts and the status chart.

    Returns ``(media_count, distribution_types, status_distribution,
    status_distribution_payload)``.
    """
    items_by_type = acc.items_by_type
    media_count = {"total": 0}
    for media_type in active_types:
        if media_type == MediaTypes.SEASON.value:
            # Grouped anime seasons belong to their show in the anime bucket.
            count = len(_tv_season_items(items_by_type))
        else:
            count = len(items_by_type.get(media_type, {}))
        if count:
            media_count[media_type] = count
            media_count["total"] += count

    # TV Seasons roll up into their parent TV Show bar here, matching how
    # Media Type Distribution already merges season minutes into "tv".
    distribution_types = list(active_types)
    if (
        MediaTypes.SEASON.value in distribution_types
        and MediaTypes.TV.value in distribution_types
    ):
        distribution_types = [
            mt for mt in distribution_types if mt != MediaTypes.SEASON.value
        ]

    no_status_label = "No status"
    status_order = [*Status.values, no_status_label]
    status_distribution = {}
    total_completed = 0
    for media_type in distribution_types:
        items = _distribution_items(items_by_type, media_type)
        if not items:
            continue
        status_counts = dict.fromkeys(status_order, 0)
        for meta in items.values():
            status_value = meta.get("status") or no_status_label
            status_counts[status_value] = status_counts.get(status_value, 0) + 1
            if status_value == Status.COMPLETED.value:
                total_completed += 1
        status_distribution[media_type] = status_counts

    status_distribution_payload = {
        "labels": [app_tags.media_type_readable(x) for x in status_distribution],
        "datasets": [
            {
                "label": status,
                "data": [
                    status_distribution[media_type][status]
                    for media_type in status_distribution
                ],
                "background_color": stats.get_status_color(status),
                "total": sum(
                    status_distribution[media_type][status]
                    for media_type in status_distribution
                ),
            }
            for status in status_order
        ],
        "total_completed": total_completed,
    }
    return (
        media_count,
        distribution_types,
        status_distribution,
        status_distribution_payload,
    )


def _user_score_scale(user):
    score_scale_max = getattr(user, "rating_scale_max", 10)
    try:
        score_scale_max = int(score_scale_max)
    except (TypeError, ValueError):
        score_scale_max = 10
    if score_scale_max not in (5, 10):
        score_scale_max = 10
    return score_scale_max


def _top_rated_ref(meta):
    return (meta.get("media_type"), meta.get("media_id"), meta.get("item_id"))


def _load_grouped_anime(refs):
    """Load grouped anime shows: TV rows a flat Anime id would not find."""
    TV = apps.get_model("app", MediaTypes.TV.value)
    grouped = {
        (tv.pk, tv.item_id): tv
        for tv in TV.objects.filter(pk__in=[ref[1] for ref in refs]).select_related(
            "item"
        )
    }
    loaded = {}
    for ref in refs:
        tv = grouped.get((ref[1], ref[2]))
        if tv:
            tv.route_media_type = MediaTypes.ANIME.value
            tv.item.route_media_type = MediaTypes.ANIME.value
            loaded[ref] = tv
    return loaded


def _annotate_top_rated_refs(refs, media_map):
    """Annotate the rows of ``refs``; a row deleted meanwhile is left out."""
    annotated = stats._annotate_top_rated_media([media_map[ref] for ref in refs])
    by_identity = {(row._meta.concrete_model, row.pk): row for row in annotated}
    loaded = {}
    for ref in refs:
        row = media_map[ref]
        found = by_identity.get((row._meta.concrete_model, row.pk))
        if found is not None:
            loaded[ref] = found
    return loaded


def _build_scores_and_top_rated(acc, distribution_types, score_scale_max, row_memo):
    """Build the score chart and the top-rated cards.

    Returns ``(score_distribution_payload, top_rated_media,
    top_rated_by_type_media, average_score, total_scored)``.
    """
    items_by_type = acc.items_by_type
    score_distribution = {}
    total_scored = 0
    total_score_sum = 0
    top_rated_heap = []
    top_rated_by_type = {}
    global_counter = itertools.count()
    score_range = range(score_scale_max + 1)

    for media_type in distribution_types:
        items = _distribution_items(items_by_type, media_type)
        if not items:
            continue
        score_counts = dict.fromkeys(score_range, 0)
        type_heap = []
        type_counter = itertools.count()
        for meta in items.values():
            score = meta.get("score")
            if score is None:
                continue
            score_value = float(score)
            score_value_scaled = (
                score_value / 2
                if score_scale_max == SCORE_SCALE_FIVE_POINT
                else score_value
            )
            binned = int(score_value_scaled)
            binned = max(binned, 0)
            binned = min(binned, score_scale_max)
            score_counts[binned] += 1
            total_scored += 1
            total_score_sum += score_value_scaled
            media_id = meta.get("media_id")
            if media_id is None:
                continue
            if len(top_rated_heap) < STATISTICS_TOP_RATED_OVERALL:
                heapq.heappush(
                    top_rated_heap, (score_value, next(global_counter), meta)
                )
            else:
                heapq.heappushpop(
                    top_rated_heap, (score_value, next(global_counter), meta)
                )
            if len(type_heap) < STATISTICS_TOP_N:
                heapq.heappush(type_heap, (score_value, next(type_counter), meta))
            else:
                heapq.heappushpop(type_heap, (score_value, next(type_counter), meta))
        score_distribution[media_type] = score_counts
        top_rated_by_type[media_type] = [
            meta for _, _, meta in sorted(type_heap, key=lambda x: (-x[0], x[1]))
        ]

    average_score = (
        round(total_score_sum / total_scored, 2) if total_scored > 0 else None
    )
    top_rated_meta = [
        meta for _, _, meta in sorted(top_rated_heap, key=lambda x: (-x[0], x[1]))
    ]

    media_refs = [_top_rated_ref(meta) for meta in top_rated_meta]
    for metas in top_rated_by_type.values():
        media_refs.extend(_top_rated_ref(meta) for meta in metas)

    # A card names its row by type and id, plus the item when the id alone is
    # ambiguous (a grouped anime show lives in the TV table).
    fetched = _shared_media_objects(
        row_memo, {(kind, pk) for kind, pk, _ in media_refs}
    )
    media_map = {}
    grouped_refs = []
    for ref in set(media_refs):
        kind, pk, item_id = ref
        media = fetched.get((kind, pk))
        if media and (not item_id or media.item_id == item_id):
            media_map[ref] = media
        elif kind == MediaTypes.ANIME.value and pk and item_id:
            grouped_refs.append(ref)
    if grouped_refs:
        media_map.update(
            _shared_rows(row_memo, "grouped_anime_rows", grouped_refs, _load_grouped_anime)
        )
    # The overall list and the per-type lists mostly name the same rows, and
    # so do the ranges of a pass. Annotate each row once per pass (the
    # annotation re-reads the row and looks up its progress total); both
    # lists of this range share one copy of it.
    annotated_refs = [ref for ref in dict.fromkeys(media_refs) if media_map.get(ref)]
    annotated_map = _shared_rows(
        row_memo,
        "top_rated_rows",
        annotated_refs,
        lambda refs: _annotate_top_rated_refs(refs, media_map),
    )
    top_rated_media = [
        annotated_map[ref]
        for ref in (_top_rated_ref(meta) for meta in top_rated_meta)
        if ref in annotated_map
    ]

    top_rated_by_type_media = {}
    for media_type, metas in top_rated_by_type.items():
        top_rated_by_type_media[media_type] = [
            annotated_map[ref]
            for ref in (_top_rated_ref(meta) for meta in metas)
            if ref in annotated_map
        ]

    top_rated_score_map = {}
    for meta in top_rated_meta:
        ref = _top_rated_ref(meta)
        score = meta.get("score")
        if ref[0] and ref[1] and score is not None:
            top_rated_score_map[ref] = score
    for metas in top_rated_by_type.values():
        for meta in metas:
            ref = _top_rated_ref(meta)
            score = meta.get("score")
            if ref[0] and ref[1] and score is not None:
                top_rated_score_map[ref] = score

    for ref, media in annotated_map.items():
        score = top_rated_score_map.get(ref)
        if score is not None:
            media.aggregated_score = score

    score_distribution_payload = {
        "labels": [str(score) for score in score_range],
        "datasets": [
            {
                "label": app_tags.media_type_readable(media_type),
                "data": [
                    score_distribution[media_type][score] for score in score_range
                ],
                "background_color": config.get_stats_color(media_type),
            }
            for media_type in score_distribution
        ],
        "average_score": average_score,
        "total_scored": total_scored,
        "scale_max": score_scale_max,
    }
    return (
        score_distribution_payload,
        top_rated_media,
        top_rated_by_type_media,
        average_score,
        total_scored,
    )


# --- Top played ------------------------------------------------------------

_TOP_PLAYED_MEDIA_TYPES = ("movie", "tv", "game", "boardgame", "anime", "music")


def _plays_label(total_minutes):
    """Format a board game's "minutes", which are plays."""
    return f"{int(total_minutes)} play{'s' if int(total_minutes) != 1 else ''}"


def _build_top_played(acc, row_memo):
    """Build the most played titles per media type, with their rows."""
    top_played = {}
    media_refs = []
    for media_type in _TOP_PLAYED_MEDIA_TYPES:
        entries = list(acc.top_played_by_type.get(media_type, {}).values())
        entries = [entry for entry in entries if entry.get("minutes", 0) > 0]
        baseline_dt = datetime(1970, 1, 1, tzinfo=timezone.get_current_timezone())
        entries.sort(
            key=lambda entry, baseline_dt=baseline_dt: (
                entry.get("minutes", 0),
                entry.get("activity_dt") or baseline_dt,
            ),
            reverse=True,
        )
        entries = entries[:STATISTICS_TOP_N]
        top_played[media_type] = entries
        media_refs.extend((media_type, entry.get("media_id")) for entry in entries)

    media_map = _shared_media_objects(row_memo, media_refs)
    for media_type, entries in top_played.items():
        enriched = []
        for entry in entries:
            media = media_map.get((media_type, entry.get("media_id")))
            if not media:
                continue
            total_minutes = entry.get("minutes", 0)
            formatted_duration = helpers.minutes_to_hhmm(total_minutes)
            if media_type == "boardgame":
                formatted_duration = _plays_label(total_minutes)
            enriched.append(
                {
                    "media": media,
                    "total_time_minutes": total_minutes,
                    "formatted_duration": formatted_duration,
                    "episode_count": entry.get("episode_count", 0),
                    "last_activity": entry.get("activity_dt"),
                    "play_count": entry.get("plays", 0),
                }
            )
        top_played[media_type] = enriched
    return top_played


# --- Activity and watch / listen consumption -------------------------------


def _build_hours_per_media_type(acc, user):
    hours_per_media_type = {}
    for media_type, total_minutes in acc.minutes_by_type.items():
        if media_type == MediaTypes.BOARDGAME.value:
            hours_per_media_type[media_type] = _plays_label(total_minutes)
        else:
            hours_per_media_type[media_type] = stats._format_hours_minutes(
                total_minutes, user.duration_format
            )
    return hours_per_media_type


def _build_activity_charts(
    acc,
    user,
    day_list,
    start_date,
    end_date,
    media_count,
    status_distribution_payload,
):
    """Build the heatmap, the distribution charts and the daily hours chart.

    Returns ``(activity_data, media_type_distribution, status_pie_chart_data,
    daily_hours_by_media_type)``.
    """
    activity_counts_by_date = {day: acc.activity_counts.get(day, 0) for day in day_list}
    from users.models import (
        WeekStartDayChoices,
    )

    week_start_sunday = user.week_start_day == WeekStartDayChoices.SUNDAY
    activity_data = _build_activity_data(
        activity_counts_by_date,
        acc.activity_counts_by_type,
        acc.day_minutes_by_type,
        day_list,
        start_date,
        end_date,
        week_start_sunday=week_start_sunday,
    )

    media_type_distribution = stats.get_media_type_distribution(
        media_count,
        acc.minutes_by_type,
    )
    status_pie_chart_data = stats.get_status_pie_chart_data(status_distribution_payload)

    daily_hours_by_media_type = _build_daily_hours_chart(
        acc.day_minutes_by_type, day_list
    )
    return (
        activity_data,
        media_type_distribution,
        status_pie_chart_data,
        daily_hours_by_media_type,
    )


def _top_by_minutes(values):
    return [
        {**item, "formatted_duration": helpers.minutes_to_hhmm(item["minutes"])}
        for item in sorted(
            values,
            key=lambda x: (x["minutes"], x["plays"]),
            reverse=True,
        )[:STATISTICS_TOP_N]
    ]


def _top_items(values, key_fields=("minutes", "plays"), limit=STATISTICS_TOP_N):
    items = sorted(
        values,
        key=lambda x: tuple(x.get(field, 0) for field in key_fields),
        reverse=True,
    )[:limit]
    for item in items:
        if "minutes" in item:
            item["formatted_duration"] = helpers.minutes_to_hhmm(item["minutes"])
    return items


def _type_chart(acc, media_type, dataset_label):
    return _build_media_charts_from_counts(
        acc.day_play_counts.get(media_type, {}),
        acc.hour_counts.get(media_type, {}),
        config.get_stats_color(media_type),
        dataset_label,
    )


def _build_watched_consumption(acc, media_type, dataset_label, genres, decades, span):
    """Movie, TV or anime: hours, plays, charts, top genres and decades."""
    start_date, end_date = span
    total_minutes = acc.minutes_by_type.get(media_type, 0)
    total_hours = total_minutes / 60 if total_minutes else 0
    plays = acc.plays_by_type.get(media_type, 0)
    return {
        "hours": _compute_metric_breakdown_for_range(total_hours, start_date, end_date),
        "plays": _compute_metric_breakdown_for_range(plays, start_date, end_date),
        "charts": _type_chart(acc, media_type, dataset_label),
        "has_data": plays > 0,
        "top_genres": _top_by_minutes(genres.values()),
        "top_decades": _top_by_minutes(decades.values()),
    }


def _build_listened_consumption(acc, media_type, dataset_label, span):
    """Music, podcast or video: minutes, plays and charts."""
    start_date, end_date = span
    plays = acc.plays_by_type.get(media_type, 0)
    return {
        "minutes": _compute_metric_breakdown_for_range(
            acc.minutes_by_type.get(media_type, 0), start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(plays, start_date, end_date),
        "charts": _type_chart(acc, media_type, dataset_label),
        "has_data": plays > 0,
    }


def _build_play_consumptions(acc, start_date, end_date):
    """Build the six play-counted consumption sections, keyed by media type."""
    span = (start_date, end_date)
    # The charts are built in the order the page lists the types; the
    # consumption sections follow.
    tv_consumption = _build_watched_consumption(
        acc, MediaTypes.TV.value, "Episode Plays", acc.tv_genres, acc.tv_decades, span
    )
    movie_consumption = _build_watched_consumption(
        acc,
        MediaTypes.MOVIE.value,
        "Movie Plays",
        acc.movie_genres,
        acc.movie_decades,
        span,
    )
    anime_consumption = _build_watched_consumption(
        acc,
        MediaTypes.ANIME.value,
        "Anime Plays",
        acc.anime_genres,
        acc.anime_decades,
        span,
    )

    music_consumption = _build_listened_consumption(
        acc, MediaTypes.MUSIC.value, "Music Plays", span
    )
    music_rollups = acc.music_rollups
    music_consumption.update(
        {
            "top_artists": _top_items(
                list(music_rollups["artists"].values()), ("minutes", "plays")
            ),
            "top_albums": _top_items(
                list(music_rollups["albums"].values()), ("minutes", "plays")
            ),
            "top_tracks": _top_items(
                list(music_rollups["tracks"].values()), ("minutes", "plays")
            ),
            "top_genres": _top_items(
                list(music_rollups["genres"].values()), ("minutes", "plays")
            ),
            "top_decades": _top_items(
                list(music_rollups["decades"].values()), ("minutes", "plays")
            ),
            "top_countries": _top_items(
                list(music_rollups["countries"].values()), ("minutes", "plays")
            ),
        }
    )

    podcast_consumption = _build_listened_consumption(
        acc, MediaTypes.PODCAST.value, "Podcast Plays", span
    )
    shows = acc.podcast_rollups["shows"]
    most_played = sorted(
        shows.values(),
        key=lambda x: (x["plays"], x["minutes"]),
        reverse=True,
    )[:STATISTICS_TOP_N]
    most_listened = sorted(
        shows.values(),
        key=lambda x: (x["minutes"], x["plays"]),
        reverse=True,
    )[:STATISTICS_TOP_N]
    longest_episodes = sorted(
        [
            ep
            for ep in acc.podcast_rollups["episodes"].values()
            if ep.get("duration_seconds", 0) > 0
        ],
        key=lambda x: x["duration_seconds"],
        reverse=True,
    )[:STATISTICS_TOP_N]
    for item in most_played + most_listened:
        item["formatted_duration"] = helpers.minutes_to_hhmm(item["minutes"])
    for item in longest_episodes:
        item["formatted_duration"] = helpers.seconds_to_hm(item["duration_seconds"])
    podcast_consumption.update(
        {
            "most_played": most_played,
            "most_listened": most_listened,
            "longest_episodes": longest_episodes,
        }
    )

    video_consumption = _build_listened_consumption(
        acc, MediaTypes.VIDEO.value, "Video Plays", span
    )
    return {
        MediaTypes.TV.value: tv_consumption,
        MediaTypes.MOVIE.value: movie_consumption,
        MediaTypes.ANIME.value: anime_consumption,
        MediaTypes.MUSIC.value: music_consumption,
        MediaTypes.PODCAST.value: podcast_consumption,
        MediaTypes.VIDEO.value: video_consumption,
    }


# --- Games -----------------------------------------------------------------


def _type_hours(acc, media_type):
    total_minutes = acc.minutes_by_type.get(media_type, 0)
    return total_minutes / 60 if total_minutes else 0


def _build_game_hours_charts(game_day_minutes):
    game_hours_by_year = defaultdict(float)
    game_hours_by_month = defaultdict(float)
    for day_str, minutes in game_day_minutes.items():
        try:
            day = datetime.strptime(day_str, "%Y-%m-%d").date()  # noqa: DTZ007  # date-only value; no timezone applies
        except ValueError:
            continue
        game_hours_by_year[day.year] += minutes / 60
        game_hours_by_month[day.month] += minutes / 60

    year_labels = [str(year) for year in sorted(game_hours_by_year)]
    year_values = [game_hours_by_year[year] for year in sorted(game_hours_by_year)]
    month_labels = [calendar.month_abbr[i] for i in range(1, 13)]
    month_values = [game_hours_by_month.get(i, 0) for i in range(1, 13)]
    return {
        "by_year": stats._build_single_series_chart(
            year_labels,
            year_values,
            config.get_stats_color(MediaTypes.GAME.value),
            "Game Hours",
        ),
        "by_month": stats._build_single_series_chart(
            month_labels,
            month_values,
            config.get_stats_color(MediaTypes.GAME.value),
            "Game Hours",
        ),
    }


def _game_rows(game_rollups):
    """Played games with their daily average and total hours."""
    game_data = []
    for item_id_str, payload in game_rollups.items():
        days = payload.get("days") or 0
        minutes_total = payload.get("minutes_total") or 0
        if days <= 0 or minutes_total <= 0:
            continue
        daily_avg_hours = (minutes_total / days) / 60
        game_data.append(
            {
                "item_id": item_id_str,
                "media_id": payload.get("media_id"),
                "daily_average": daily_avg_hours,
                "hours": minutes_total / 60,
                "activity_dt": payload.get("activity_dt"),
            }
        )
    return game_data


def _build_daily_average_chart(user, game_data):
    """Build the daily-average chart and look up what the platform card needs.

    Returns ``(daily_avg_chart, collection_platform_map, item_info_map)``.
    """
    # --- Collect item_ids needed for chart tooltip and platform breakdown ---
    all_item_ids = [
        item["item_id"] for item in game_data if item.get("item_id") is not None
    ]

    # Determine which item_ids are needed for the top-5-per-band tooltip
    bands = stats.DAILY_AVERAGE_BANDS
    band_game_groups = {label: [] for _, _, label in bands}
    for item in game_data:
        idx = stats._get_daily_average_band_index(item["daily_average"])
        if 0 <= idx < len(bands):
            band_game_groups[bands[idx][2]].append(item)
    tooltip_item_ids = set()
    for items in band_game_groups.values():
        for g in sorted(items, key=lambda x: x["daily_average"], reverse=True)[:5]:
            if g.get("item_id") is not None:
                tooltip_item_ids.add(g["item_id"])

    # Fetch Item metadata (platforms for breakdown + title/image for tooltip)
    item_info_map = {}
    if all_item_ids:
        item_info_map = {
            row["id"]: row
            for row in Item.objects.filter(id__in=all_item_ids).values(
                "id", "title", "image", "platforms"
            )
        }

    # Fetch CollectionEntry resolution for platform breakdown
    CollectionEntry = apps.get_model("app", "CollectionEntry")
    collection_platform_map = {}
    if all_item_ids:
        for ce in CollectionEntry.objects.filter(
            user=user, item_id__in=all_item_ids
        ).values("item_id", "resolution"):
            resolution = (ce.get("resolution") or "").strip()
            if resolution:
                collection_platform_map[ce["item_id"]] = resolution

    # Enrich game_data with title/image for the chart tooltip
    enriched_game_data = []
    for item in game_data:
        info = item_info_map.get(item.get("item_id"), {})
        enriched_game_data.append(
            {
                **item,
                "title": info.get("title", ""),
                "image": info.get("image", ""),
            }
        )

    daily_avg_chart = stats._build_daily_average_distribution_chart(
        enriched_game_data,
        config.get_stats_color(MediaTypes.GAME.value),
        "Games",
    )
    return daily_avg_chart, collection_platform_map, item_info_map


def _top_game_rollup(rollup, label_key):
    """Rank a game genre or decade rollup, counted in distinct games."""
    items = []
    for key, payload in rollup.items():
        items.append(
            {
                "minutes": payload["minutes"],
                "games": len(payload["game_ids"]),
                "plays": len(payload["game_ids"]),
                label_key: key,
                "formatted_duration": helpers.minutes_to_hhmm(payload["minutes"]),
            }
        )
    return sorted(items, key=lambda x: (x["minutes"], x["games"]), reverse=True)[
        :STATISTICS_TOP_N
    ]


def _build_top_daily_average_games(game_data, row_memo):
    top_daily_avg_games = sorted(
        game_data, key=lambda x: x["daily_average"], reverse=True
    )[:STATISTICS_TOP_N]
    game_media_map = _shared_media_objects(
        row_memo,
        {(MediaTypes.GAME.value, item["media_id"]) for item in top_daily_avg_games},
    )
    top_daily_avg_payload = []
    for item in top_daily_avg_games:
        media = game_media_map.get((MediaTypes.GAME.value, item["media_id"]))
        if not media:
            continue
        daily_avg_minutes = item["daily_average"] * 60
        top_daily_avg_payload.append(
            {
                "game": media,
                "daily_average_hours": item["daily_average"],
                "daily_average_minutes": daily_avg_minutes,
                "formatted_daily_average": helpers.minutes_to_hhmm(daily_avg_minutes)
                + "/day",
                "total_hours": item["hours"],
                "formatted_total": helpers.minutes_to_hhmm(item["hours"] * 60),
            }
        )
    return top_daily_avg_payload


def _build_game_consumptions(acc, user, start_date, end_date, row_memo):
    """Build the game and board game sections. Returns ``(game, boardgame)``."""
    game_total_hours = _type_hours(acc, MediaTypes.GAME.value)
    game_hours_charts = _build_game_hours_charts(
        acc.day_minutes_by_type.get(MediaTypes.GAME.value, {})
    )
    game_data = _game_rows(acc.game_rollups)
    daily_avg_chart, collection_platform_map, item_info_map = (
        _build_daily_average_chart(user, game_data)
    )
    game_genre_items = _top_game_rollup(acc.game_genres, "name")
    game_decade_items = _top_game_rollup(acc.game_decades, "label")
    top_daily_avg_payload = _build_top_daily_average_games(game_data, row_memo)
    platform_breakdown = _build_platform_breakdown(
        game_data, collection_platform_map, item_info_map
    )

    game_consumption = {
        "hours": _compute_metric_breakdown_for_range(
            game_total_hours, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            _sum_top_played_plays(acc.top_played_by_type, MediaTypes.GAME.value),
            start_date,
            end_date,
        ),
        "average_daily_playtime_hours": _avg_daily_playtime_hours(
            game_total_hours,
            acc.day_minutes_by_type.get(MediaTypes.GAME.value, {}),
        ),
        "charts": {
            "by_year": game_hours_charts["by_year"],
            "by_month": game_hours_charts["by_month"],
            "by_daily_average": daily_avg_chart,
        },
        "has_data": bool(game_data) or game_total_hours > 0,
        "top_genres": game_genre_items,
        "top_decades": game_decade_items,
        "top_daily_average_games": top_daily_avg_payload,
        "platform_breakdown": platform_breakdown,
    }

    boardgame_total_minutes = acc.minutes_by_type.get(MediaTypes.BOARDGAME.value, 0)
    boardgame_total_hours = _type_hours(acc, MediaTypes.BOARDGAME.value)
    boardgame_consumption = {
        "hours": _compute_metric_breakdown_for_range(
            boardgame_total_hours, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            _sum_top_played_plays(acc.top_played_by_type, MediaTypes.BOARDGAME.value),
            start_date,
            end_date,
        ),
        "average_daily_playtime_hours": _avg_daily_playtime_hours(
            boardgame_total_hours,
            acc.day_minutes_by_type.get(MediaTypes.BOARDGAME.value, {}),
        ),
        "has_data": boardgame_total_minutes > 0
        or bool(acc.top_played_by_type.get(MediaTypes.BOARDGAME.value)),
    }
    return game_consumption, boardgame_consumption


# --- Reading ---------------------------------------------------------------

_READING_COMPLETION_LABELS = {
    MediaTypes.BOOK.value: "Books Finished",
    MediaTypes.COMIC.value: "Comics Finished",
    MediaTypes.MANGA.value: "Manga Finished",
}
_READING_RELEASE_LABELS = {
    MediaTypes.BOOK.value: "Books Released",
    MediaTypes.COMIC.value: "Comics Released",
    MediaTypes.MANGA.value: "Manga Released",
}


def _reading_completions(user, media_type, start_date, end_date):
    """Completed entries in the range: their lengths, and one date per title.

    Returns ``(completed_lengths, completed_datetimes)``.
    """
    # One finished title per item, dated by its latest completed entry
    # (same rule as get_reading_consumption_stats).
    completed_lengths = []
    latest_completed_by_item = {}
    model = apps.get_model("app", media_type)
    completed_queryset = model.objects.filter(user=user, status=Status.COMPLETED.value)
    # This scans a large library for every range; let the database drop the
    # entries the date predicate would reject instead of loading them.
    range_filter = stats._reading_entry_range_filter(start_date, end_date)
    if range_filter is not None:
        completed_queryset = completed_queryset.filter(range_filter)
    # Only these columns are read. Loading the tracker and its item as
    # models built two instances, wide item columns included, per row.
    completed_rows = completed_queryset.values_list(
        "item_id",
        "progress",
        "start_date",
        "end_date",
        "created_at",
        "item__format",
        "item__number_of_pages",
        named=True,
    )
    for entry in completed_rows.iterator(chunk_size=500):
        if not stats._reading_entry_in_range(entry, start_date, end_date):
            continue
        # An audiobook's progress is minutes, so its length is not a page
        # count and stays out of the page-length distribution.
        is_audiobook = (
            media_type == MediaTypes.BOOK.value and entry.item__format == "audiobook"
        )
        completed_length = (
            0 if is_audiobook else entry.progress or entry.item__number_of_pages or 0
        )
        if completed_length > 0:
            completed_lengths.append(completed_length)
        completed_dt = stats._get_activity_datetime(entry) or entry.created_at
        previous = latest_completed_by_item.get(entry.item_id)
        if previous is None or completed_dt > previous:
            latest_completed_by_item[entry.item_id] = completed_dt
    completed_datetimes = [
        stats._localize_datetime(value) for value in latest_completed_by_item.values()
    ]
    return completed_lengths, completed_datetimes


def _format_units(units, unit_name):
    return f"{round(units)} {unit_name.lower()}{'' if round(units) == 1 else 's'}"


def _reading_top_items(media_type, top_entries, unit_name, row_memo):
    top_media_refs = {
        (media_type, entry.get("media_id"))
        for entry in top_entries
        if entry.get("media_id")
    }
    top_media_map = _shared_media_objects(row_memo, top_media_refs)
    top_items = []
    for entry in top_entries:
        media = top_media_map.get((media_type, entry.get("media_id")))
        if not media:
            continue
        units = entry.get("minutes", 0)
        top_items.append(
            {
                "media": media,
                "units": units,
                "entry_count": entry.get("plays", 0),
                "formatted_units": _format_units(units, unit_name),
            }
        )
    return top_items


def _reading_top_genres(genres, unit_name):
    genre_items = []
    for payload in genres.values():
        units = payload.get("units", 0)
        if units <= 0:
            continue
        titles = payload.get("titles", 0)
        genre_items.append(
            {
                "name": payload.get("name") or "",
                "units": units,
                "titles": titles,
                "formatted_units": _format_units(units, unit_name),
            }
        )
    return sorted(
        genre_items, key=lambda item: (item["units"], item["titles"]), reverse=True
    )[:STATISTICS_TOP_N]


def _reading_highlights(top_items, average_completed_length):
    item_lengths = []
    scored_values = []
    longest_item = None
    shortest_item = None
    for entry in top_items:
        media = entry["media"]
        pages = getattr(getattr(media, "item", None), "number_of_pages", None)
        if pages and pages > 0:
            item_lengths.append(pages)
            if longest_item is None or pages > longest_item["value"]:
                longest_item = {"media": media, "value": pages}
            if shortest_item is None or pages < shortest_item["value"]:
                shortest_item = {"media": media, "value": pages}
        score_value = getattr(media, "aggregated_score", None)
        if score_value is None:
            score_value = getattr(media, "score", None)
        if score_value is not None:
            scored_values.append(float(score_value))

    average_rating = (
        round(sum(scored_values) / len(scored_values), 2) if scored_values else None
    )
    return {
        "longest_item": longest_item,
        "shortest_item": shortest_item,
        "average_length": average_completed_length,
        "average_rating": average_rating,
    }


def _build_reading_consumption(acc, user, media_type, start_date, end_date, row_memo):
    """One reading type's section (book, comic or manga)."""
    unit_name = config.get_unit(media_type, short=False) or "Unit"
    chart_label = f"{unit_name}s Read"
    completion_label = _READING_COMPLETION_LABELS.get(media_type, "Items Finished")
    release_label = _READING_RELEASE_LABELS.get(media_type, "Items Released")
    color = config.get_stats_color(media_type)
    units_by_day = acc.day_minutes_by_type.get(media_type, {})
    unit_total = sum(units_by_day.values()) if units_by_day else 0

    completed_lengths, completed_datetimes = _reading_completions(
        user, media_type, start_date, end_date
    )
    completion_total = len(completed_datetimes)
    item_ids = [
        meta.get("item_id")
        for meta in acc.items_by_type.get(media_type, {}).values()
        if meta.get("item_id")
    ]
    top_entries = list(acc.top_played_by_type.get(media_type, {}).values())
    listening_minutes = (
        acc.listening_minutes_total if media_type == MediaTypes.BOOK.value else 0
    )
    has_any_reading_data = bool(
        unit_total or completion_total or item_ids or top_entries or listening_minutes
    )
    if not has_any_reading_data:
        return _empty_reading_consumption(
            unit_name=unit_name,
            completion_label=completion_label,
        )

    charts = _build_media_charts_from_counts(
        units_by_day,
        acc.hour_counts.get(media_type, {}),
        color,
        chart_label,
    )
    completion_charts = stats._build_media_charts(
        completed_datetimes, color, completion_label
    )

    release_datetimes = []
    items_with_authors = stats._fetch_reading_items_with_authors(item_ids)
    if items_with_authors:
        release_datetimes = [
            item.release_datetime
            for item in items_with_authors.values()
            if item.release_datetime
        ]
    average_completed_length = (
        round(sum(completed_lengths) / len(completed_lengths), 1)
        if completed_lengths
        else 0
    )

    release_chart = stats._build_release_year_chart(
        release_datetimes, color, release_label
    )
    completed_length_chart = stats._build_completed_length_distribution_chart(
        completed_lengths, unit_name, color
    )

    baseline_dt = datetime(1970, 1, 1, tzinfo=timezone.get_current_timezone())
    top_entries.sort(
        key=lambda entry: (
            entry.get("minutes", 0),
            entry.get("activity_dt") or baseline_dt,
        ),
        reverse=True,
    )
    top_authors = stats._build_reading_top_authors(
        [
            (
                items_with_authors.get(entry.get("item_id")),
                entry.get("minutes", 0),
            )
            for entry in top_entries
            if entry.get("minutes", 0) > 0
        ],
        unit_name,
        limit=STATISTICS_TOP_N,
    )
    top_entries = top_entries[:STATISTICS_TOP_N]
    top_items = _reading_top_items(media_type, top_entries, unit_name, row_memo)
    genre_items = _reading_top_genres(acc.reading_genres.get(media_type, {}), unit_name)

    return {
        "units": _compute_metric_breakdown_for_range(unit_total, start_date, end_date),
        "completions": _compute_metric_breakdown_for_range(
            completion_total, start_date, end_date
        ),
        "charts": charts,
        "completion_charts": {
            "by_year": completion_charts["by_year"],
            "by_month": completion_charts["by_month"],
        },
        "completed_length_chart": completed_length_chart,
        "release_chart": release_chart,
        "completed_lengths": completed_lengths,
        "listening": _listening_payload(listening_minutes),
        "has_data": unit_total > 0 or completion_total > 0 or listening_minutes > 0,
        "unit_name": unit_name,
        "unit_label": chart_label,
        "completion_label": completion_label,
        "top_items": top_items,
        "top_authors": top_authors,
        "top_genres": genre_items,
        "highlights": _reading_highlights(top_items, average_completed_length),
    }


def _build_reading_consumptions(
    acc, user, active_types, start_date, end_date, row_memo
):
    """Book, comic and manga sections; empty ones for types with nothing."""
    reading_types_with_data = {
        media_type
        for media_type in _READING_MEDIA_TYPES
        if media_type in active_types
        and (
            acc.minutes_by_type.get(media_type)
            or acc.items_by_type.get(media_type)
            or acc.top_played_by_type.get(media_type)
            or (media_type == MediaTypes.BOOK.value and acc.listening_minutes_total)
        )
    }
    consumptions = {}
    for media_type in _READING_MEDIA_TYPES:
        if media_type in reading_types_with_data:
            consumptions[media_type] = _build_reading_consumption(
                acc, user, media_type, start_date, end_date, row_memo
            )
        else:
            consumptions[media_type] = _empty_reading_consumption(
                "Page", _READING_COMPLETION_LABELS[media_type]
            )
    return consumptions


# --- History highlights ----------------------------------------------------


def _build_history_highlights(acc, user, start_date, end_date, history_memo):
    """First and last play and "today in history", overall and per type.

    Returns ``(history_highlights, history_highlights_by_type)``.
    """
    first_play = None
    last_play = None
    first_highlight_day, last_highlight_day = _get_range_history_boundary_days(
        user,
        start_date,
        end_date,
        history_memo,
    )
    if first_highlight_day and last_highlight_day:
        first_day_payload = _get_history_day_payload(
            user, first_highlight_day, history_memo
        )
        last_day_payload = _get_history_day_payload(
            user, last_highlight_day, history_memo
        )
        first_play = _select_history_entry_for_day(
            first_day_payload, pick_earliest=True
        )
        last_play = _select_history_entry_for_day(last_day_payload, pick_latest=True)

    today_in_user_history, today_in_user_history_year = _get_today_history_entries(
        user, memo=history_memo
    )
    today_in_history, today_in_history_year = _get_today_release_entry(user)
    today = timezone.localdate()
    history_highlights = {
        "first_play": first_play,
        "last_play": last_play,
        "today_card": _build_today_card(
            today_in_history,
            today_in_history_year,
            today_in_user_history,
            today_in_user_history_year,
        ),
        "today_month": today.month,
        "today_day": today.day,
    }

    # Per-media-type highlight cards (same structure as history_highlights)
    history_highlights_by_type = {}
    for media_type, day_map in acc.day_minutes_by_type.items():
        active_days = sorted(d for d, m in day_map.items() if m > 0)
        if not active_days:
            continue
        first_day = date_type.fromisoformat(active_days[0])
        last_day = date_type.fromisoformat(active_days[-1])
        first_payload = _get_history_day_payload(user, first_day, history_memo)
        last_payload = _get_history_day_payload(user, last_day, history_memo)
        type_today_user_h, type_today_user_year = _get_today_history_entries(
            user, media_type_filter=media_type, memo=history_memo
        )
        type_today_h, type_today_year = _get_today_release_entry(
            user, media_type_filter=media_type
        )
        history_highlights_by_type[media_type] = {
            "first_play": _select_history_entry_for_day(
                first_payload, pick_earliest=True, media_type_filter=media_type
            ),
            "last_play": _select_history_entry_for_day(
                last_payload, pick_latest=True, media_type_filter=media_type
            ),
            "today_card": _build_today_card(
                type_today_h,
                type_today_year,
                type_today_user_h,
                type_today_user_year,
            ),
            "today_month": today.month,
            "today_day": today.day,
        }
    return history_highlights, history_highlights_by_type


# --- Summary cards ---------------------------------------------------------


def _score_totals_by_type(items_by_type, active_types, score_scale_max):
    """Per-media-type average, count and sum of scores.

    Returns ``(average_by_type, count_by_type, sum_by_type)``.
    """
    average_score_by_type = {}
    score_count_by_type = {}
    score_sum_by_type = {}
    for media_type in active_types:
        type_sum = 0.0
        type_count = 0
        for meta in items_by_type.get(media_type, {}).values():
            score = meta.get("score")
            if score is None:
                continue
            score_value = float(score)
            if score_scale_max == SCORE_SCALE_FIVE_POINT:
                score_value = score_value / 2
            type_sum += score_value
            type_count += 1
        average_score_by_type[media_type] = (
            round(type_sum / type_count, 2) if type_count > 0 else None
        )
        score_count_by_type[media_type] = type_count
        score_sum_by_type[media_type] = type_sum
    return average_score_by_type, score_count_by_type, score_sum_by_type


def _build_summary_stats_by_type(
    acc,
    *,
    active_types,
    day_list,
    end_date,
    score_scale_max,
    media_count,
    status_distribution,
    status_distribution_payload,
    average_score,
    total_scored,
    activity_data,
):
    """Build the summary cards: one for all types, one per active type."""
    average_score_by_type, score_count_by_type, score_sum_by_type = (
        _score_totals_by_type(acc.items_by_type, active_types, score_scale_max)
    )

    end_date_for_streak = end_date.date() if hasattr(end_date, "date") else end_date
    end_epoch_day = (
        (end_date_for_streak - _EPOCH_DAY).days
        if end_date_for_streak is not None
        else None
    )
    day_list_iso = {day.isoformat() for day in day_list}

    all_total_minutes = sum(acc.minutes_by_type.values())
    activity_stats = activity_data["stats"]

    # "all" bucket mirrors the already-computed activity_data.stats values
    summary_stats_by_type = {
        "all": {
            "completed": status_distribution_payload["total_completed"],
            "total": media_count.get("total", 0),
            "total_minutes": all_total_minutes,
            "average_score": average_score,
            "has_score": bool(total_scored),
            "most_active_day": activity_stats.get("most_active_day"),
            "most_active_day_percentage": activity_stats.get(
                "most_active_day_percentage", 0
            ),
            "current_streak": activity_stats.get("current_streak", 0),
            "longest_streak": activity_stats.get("longest_streak", 0),
            "longest_streak_start": _date_to_iso(
                activity_stats.get("longest_streak_start")
            ),
            "longest_streak_end": _date_to_iso(
                activity_stats.get("longest_streak_end")
            ),
        }
    }
    for media_type, day_map in acc.day_minutes_by_type.items():
        type_date_counts = {
            date_type.fromisoformat(d): 1 for d, m in day_map.items() if m > 0
        }
        type_most_active, type_day_pct = stats.calculate_most_active_weekday(
            {media_type: day_map}, day_list
        )
        type_streaks = stats.calculate_streak_details(
            type_date_counts, end_date_for_streak
        )
        summary_stats_by_type[media_type] = {
            "completed": status_distribution.get(media_type, {}).get(
                Status.COMPLETED.value, 0
            ),
            "total": media_count.get(media_type, 0),
            "total_minutes": acc.minutes_by_type.get(media_type, 0),
            "average_score": average_score_by_type.get(media_type),
            "has_score": average_score_by_type.get(media_type) is not None,
            "most_active_day": type_most_active,
            "most_active_day_percentage": type_day_pct,
            "current_streak": type_streaks.get("current_streak", 0),
            "longest_streak": type_streaks.get("longest_streak", 0),
            "longest_streak_start": _date_to_iso(
                type_streaks.get("longest_streak_start")
            ),
            "longest_streak_end": _date_to_iso(type_streaks.get("longest_streak_end")),
            # Raw pieces so the page can merge several media types exactly.
            "score_count": score_count_by_type.get(media_type, 0),
            "score_sum": round(score_sum_by_type.get(media_type, 0.0), 4),
            "weekday_minutes": _weekday_minutes(day_map, day_list_iso),
            "active_runs": _active_day_runs(day_map),
            "streak_end_day": end_epoch_day,
        }
    return summary_stats_by_type


# --- Top talent ------------------------------------------------------------


def _build_top_talent(
    acc, user, start_date, end_date, *, is_all_time_query, credit_backfill_hints
):
    has_movie_tv_activity = bool(
        acc.plays_by_type.get(MediaTypes.MOVIE.value, 0)
        or acc.plays_by_type.get(MediaTypes.TV.value, 0)
    )
    if (
        not has_movie_tv_activity
        and is_all_time_query
        and _has_dateless_movie_or_episode_activity(user)
    ):
        has_movie_tv_activity = True
    # start_date/end_date may have been narrowed to day_list's bounds (for
    # chart axes); `is_all_time_query` (captured before that narrowing)
    # tells top talent to still skip date filtering for a genuine all-time
    # query, so dateless entries are included despite the concrete bounds.
    if not has_movie_tv_activity:
        return _empty_top_talent_payload(
            sort_by=getattr(user, "top_talent_sort_by", "plays"),
        )
    return _aggregate_top_talent(
        user,
        start_date,
        end_date,
        schedule_missing_backfill=credit_backfill_hints <= 0,
        is_all_time=is_all_time_query,
    )


# --- Packing the per-type metric cards -------------------------------------


def _pack_metric(breakdown, label, unit, icon, *, total_decimals=1):
    if not breakdown:
        return None
    return {
        "label": label,
        "unit": unit,
        "icon": icon,
        "total_decimals": total_decimals,
        **breakdown,
    }


def _minutes_breakdown_to_hours(breakdown):
    return {
        **breakdown,
        **{k: v / 60 for k, v in breakdown.items() if k != "label"},
    }


def _sum_breakdowns(breakdowns):
    keys = ("total", "per_year", "per_month", "per_day")
    return {k: sum(b[k] for b in breakdowns) for k in keys}


def _games_bonus(acc, game_consumption, boardgame_consumption):
    """One "Average Daily Playtime" card across games and board games."""
    combined_games_hours = _type_hours(acc, MediaTypes.GAME.value) + _type_hours(
        acc, MediaTypes.BOARDGAME.value
    )
    combined_games_day_minutes: defaultdict = defaultdict(float)
    for media_type in (MediaTypes.GAME.value, MediaTypes.BOARDGAME.value):
        for day_str, minutes in acc.day_minutes_by_type.get(media_type, {}).items():
            combined_games_day_minutes[day_str] += minutes or 0
    games_avg_daily_playtime = _avg_daily_playtime_hours(
        combined_games_hours, combined_games_day_minutes
    )
    if games_avg_daily_playtime and (
        game_consumption["has_data"] or boardgame_consumption["has_data"]
    ):
        return {
            "kind": "playtime",
            "icon": "gamepad",
            "value": games_avg_daily_playtime,
        }
    return None


def _reading_bonus(reading):
    """One "Average Page Length" card across books, comics and manga."""
    all_reading_lengths = [
        length
        for media_type in _READING_MEDIA_TYPES
        for length in reading[media_type].get("completed_lengths", [])
    ]
    reading_avg_length = (
        round(sum(all_reading_lengths) / len(all_reading_lengths), 1)
        if all_reading_lengths
        else 0
    )
    if reading_avg_length:
        return {"kind": "length", "icon": "book-open", "value": reading_avg_length}
    return None


_PLAY_METRIC_LABELS = (
    # media type, primary label, secondary label, secondary unit, minutes-based
    (MediaTypes.TV.value, "Hours Watched", "Episode Plays", "Episodes", False),
    (MediaTypes.MOVIE.value, "Hours Watched", "Movie Plays", "Movies", False),
    (MediaTypes.ANIME.value, "Hours Watched", "Episode Plays", "Episodes", False),
    (MediaTypes.MUSIC.value, "Hours Listened", "Music Plays", "Plays", True),
    (MediaTypes.PODCAST.value, "Hours Listened", "Podcast Plays", "Plays", True),
    (MediaTypes.VIDEO.value, "Hours Watched", "Video Plays", "Plays", True),
)
_READING_SECONDARY_UNITS = {
    MediaTypes.BOOK.value: "Books",
    MediaTypes.COMIC.value: "Comics",
    MediaTypes.MANGA.value: "Manga",
}


def _build_consumption_stats_by_type(
    acc, plays, game_consumption, boardgame_consumption, reading, start_date, end_date
):
    """Pack the two headline metrics and bonus cards per type and for all.

    Shared bonus cards: one "Average Daily Playtime" across Games + Board
    Games, one "Average Page Length" across Book/Comic/Manga - not per-type,
    so they can appear once each on the "All" filter alongside their own
    type's filter.
    """
    games_bonus = _games_bonus(acc, game_consumption, boardgame_consumption)
    reading_bonus = _reading_bonus(reading)

    consumption_stats_by_type = {}
    for (
        media_type,
        primary_label,
        secondary_label,
        unit,
        minutes,
    ) in _PLAY_METRIC_LABELS:
        consumption = plays[media_type]
        primary = (
            _minutes_breakdown_to_hours(consumption["minutes"])
            if minutes
            else consumption["hours"]
        )
        consumption_stats_by_type[media_type] = {
            "primary": _pack_metric(primary, primary_label, "Hours", "clock"),
            "secondary": _pack_metric(
                consumption["plays"], secondary_label, unit, "repeat"
            ),
            "bonuses": [],
            "has_data": consumption["has_data"],
        }
    for media_type, consumption, secondary_label in (
        (MediaTypes.GAME.value, game_consumption, "Game Plays"),
        (MediaTypes.BOARDGAME.value, boardgame_consumption, "Board Game Plays"),
    ):
        consumption_stats_by_type[media_type] = {
            "primary": _pack_metric(
                consumption["hours"], "Hours Played", "Hours", "clock"
            ),
            "secondary": _pack_metric(
                consumption["plays"], secondary_label, "Plays", "repeat"
            ),
            "bonuses": [games_bonus] if games_bonus else [],
            "has_data": consumption["has_data"],
        }
    for media_type in _READING_MEDIA_TYPES:
        consumption = reading[media_type]
        consumption_stats_by_type[media_type] = {
            "primary": _pack_metric(
                consumption["units"],
                consumption["unit_label"],
                consumption["unit_name"] + "s",
                "book-open",
                total_decimals=0,
            ),
            "secondary": _pack_metric(
                consumption["completions"],
                consumption["completion_label"],
                _READING_SECONDARY_UNITS[media_type],
                "checkmark",
            ),
            "bonuses": [reading_bonus] if reading_bonus else [],
            "has_data": consumption["has_data"],
        }

    all_total_minutes = sum(acc.minutes_by_type.values())
    consumption_stats_by_type["all"] = {
        # Reuses the exact total already shown by the hero ("You've spent X hours") and
        # total_activity_comparison, i.e. the summary's total_minutes - not a second,
        # different "all-media hours" figure. This does fold in the reading types'
        # synthetic 60-min-per-completed-unit proxy (see statistics_day_builder.py),
        # same as the hero already does today.
        "primary": _pack_metric(
            _compute_metric_breakdown_for_range(
                all_total_minutes / 60, start_date, end_date
            ),
            "Hours Logged",
            "Hours",
            "clock",
        ),
        "secondary": _pack_metric(
            _sum_breakdowns(
                [
                    plays[MediaTypes.TV.value]["plays"],
                    plays[MediaTypes.ANIME.value]["plays"],
                    plays[MediaTypes.MOVIE.value]["plays"],
                    plays[MediaTypes.MUSIC.value]["plays"],
                    plays[MediaTypes.PODCAST.value]["plays"],
                    plays[MediaTypes.VIDEO.value]["plays"],
                ]
            ),
            "Total Plays",
            "Plays",
            "repeat",
        ),
        "bonuses": [b for b in [games_bonus, reading_bonus] if b],
        "has_data": all_total_minutes > 0,
    }
    return consumption_stats_by_type


# --- The range aggregate ---------------------------------------------------


def _aggregate_statistics_from_days(
    user,
    day_list,
    start_date,
    end_date,
    build_missing=False,
    credit_backfill_hints: int = 0,
    prebuilt_days=None,
    timings: dict | None = None,
    pass_memo: dict | None = None,
    build_days=None,
    missing_days: list | None = None,
):
    """Build one range's statistics from its cached day payloads.

    The day walk adds the range's day payloads up into one
    ``_RangeAccumulators``; each phase after it reads those and builds its
    part of the payload, in this order (``phases_ms`` times them by these
    names).

    ``pass_memo`` is an in-memory dict the caller keeps for one sync pass so
    its ranges share lookups that do not depend on the range (History index
    and days, and the tracker rows the cards show, loaded and annotated once
    per pass and handed to each range as copies). It is never cached or
    published. ``build_days(days)`` builds and caches a batch of missing days
    and returns their credit hints; with it a missing day is found by the read
    that would have fetched it anyway. ``missing_days`` collects the days that
    still had no payload, so a caller need not read the range again to ask.
    """
    clock = _PhaseClock(timings)
    pass_memo = {} if pass_memo is None else pass_memo
    is_all_time_query = start_date is None and end_date is None
    try:
        credit_backfill_hints = int(credit_backfill_hints or 0)
    except (TypeError, ValueError):
        credit_backfill_hints = 0

    # day_fetch, day_merge
    acc = _RangeAccumulators()
    credit_backfill_hints += _walk_days(
        acc,
        user,
        day_list,
        build_missing=build_missing,
        prebuilt_days=prebuilt_days,
        build_days=build_days,
        clock=clock,
        timings=timings,
        missing_days=missing_days,
    )

    active_types = _active_media_types(user, acc)
    if is_all_time_query:
        _merge_undated_rows(acc, user, active_types)
    _merge_album_trackers(
        acc, user, active_types, start_date, end_date, is_all_time_query
    )
    clock.mark("undated")

    media_count, distribution_types, status_distribution, status_payload = (
        _build_status_distribution(acc, active_types)
    )
    score_scale_max = _user_score_scale(user)
    (
        score_distribution_payload,
        top_rated_media,
        top_rated_by_type_media,
        average_score,
        total_scored,
    ) = _build_scores_and_top_rated(acc, distribution_types, score_scale_max, pass_memo)
    clock.mark("top_rated")

    top_played = _build_top_played(acc, pass_memo)
    clock.mark("top_played")

    hours_per_media_type = _build_hours_per_media_type(acc, user)
    # All Time charts its axes over the days it has; top talent and the
    # undated rows still go by `is_all_time_query`.
    if start_date is None and end_date is None and day_list:
        start_date = _day_boundary_datetime(day_list[0])
        end_date = _day_boundary_datetime(day_list[-1], end_of_day=True)
    (
        activity_data,
        media_type_distribution,
        status_pie_chart_data,
        daily_hours_by_media_type,
    ) = _build_activity_charts(
        acc, user, day_list, start_date, end_date, media_count, status_payload
    )
    plays = _build_play_consumptions(acc, start_date, end_date)
    clock.mark("charts")

    game_consumption, boardgame_consumption = _build_game_consumptions(
        acc, user, start_date, end_date, pass_memo
    )
    clock.mark("games")

    reading = _build_reading_consumptions(
        acc, user, active_types, start_date, end_date, pass_memo
    )
    clock.mark("reading")

    history_highlights, history_highlights_by_type = _build_history_highlights(
        acc, user, start_date, end_date, pass_memo
    )
    clock.mark("highlights")

    summary_stats_by_type = _build_summary_stats_by_type(
        acc,
        active_types=active_types,
        day_list=day_list,
        end_date=end_date,
        score_scale_max=score_scale_max,
        media_count=media_count,
        status_distribution=status_distribution,
        status_distribution_payload=status_payload,
        average_score=average_score,
        total_scored=total_scored,
        activity_data=activity_data,
    )
    clock.mark("summary")

    top_talent = _build_top_talent(
        acc,
        user,
        start_date,
        end_date,
        is_all_time_query=is_all_time_query,
        credit_backfill_hints=credit_backfill_hints,
    )
    clock.mark("top_talent")

    weekday_hour_chart_data = _build_weekday_hour_charts(acc.weekday_hour_counts)
    combined_hours_charts = _build_combined_hours_charts(
        acc.day_minutes_by_type, acc.hour_minutes
    )
    consumption_stats_by_type = _build_consumption_stats_by_type(
        acc,
        plays,
        game_consumption,
        boardgame_consumption,
        reading,
        start_date,
        end_date,
    )
    clock.mark("finish_other")

    return {
        "media_count": media_count,
        "activity_data": activity_data,
        "media_type_distribution": media_type_distribution,
        "score_distribution": score_distribution_payload,
        "top_rated": top_rated_media,
        "top_rated_by_type": top_rated_by_type_media,
        "top_played": top_played,
        "top_talent": top_talent,
        "status_distribution": status_payload,
        "status_pie_chart_data": status_pie_chart_data,
        "minutes_per_media_type": dict(acc.minutes_by_type),
        "hours_per_media_type": hours_per_media_type,
        "tv_consumption": plays[MediaTypes.TV.value],
        "movie_consumption": plays[MediaTypes.MOVIE.value],
        "anime_consumption": plays[MediaTypes.ANIME.value],
        "music_consumption": plays[MediaTypes.MUSIC.value],
        "podcast_consumption": plays[MediaTypes.PODCAST.value],
        "video_consumption": plays[MediaTypes.VIDEO.value],
        "game_consumption": game_consumption,
        "boardgame_consumption": boardgame_consumption,
        "book_consumption": reading[MediaTypes.BOOK.value],
        "comic_consumption": reading[MediaTypes.COMIC.value],
        "manga_consumption": reading[MediaTypes.MANGA.value],
        "daily_hours_by_media_type": daily_hours_by_media_type,
        "history_highlights": history_highlights,
        "history_highlights_by_type": history_highlights_by_type,
        "summary_stats_by_type": summary_stats_by_type,
        "consumption_stats_by_type": consumption_stats_by_type,
        "weekday_hour_chart_data": weekday_hour_chart_data,
        "combined_plays_charts": combined_hours_charts,
    }
