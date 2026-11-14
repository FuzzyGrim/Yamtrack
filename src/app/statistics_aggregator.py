"""Range-level statistics aggregator — extracted from statistics_cache.py."""

import calendar
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
from django.utils import timezone
from django.utils.translation import gettext_noop

from app import config, helpers
from app import statistics as stats
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
        queryset = model.objects.filter(id__in=media_ids).select_related("item")
        if media_type == MediaTypes.SEASON.value:
            queryset = queryset.select_related("item", "related_tv__item")
        elif media_type == MediaTypes.EPISODE.value:
            queryset = queryset.select_related(
                "item",
                "related_season__item",
                "related_season__related_tv__item",
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
                for tv in TV.objects.filter(id__in=missing_ids).select_related("item"):
                    # Grouped anime is stored on TV rows; without the route
                    # annotation the shared media card links it to /tv/.
                    tv.route_media_type = MediaTypes.ANIME.value
                    if getattr(tv, "item", None) is not None:
                        tv.item.route_media_type = MediaTypes.ANIME.value
                    media_objects[(media_type, tv.id)] = tv

    return media_objects


def _aggregate_minutes_per_media_type_from_days(user, day_list, *, build_missing=False):
    minutes_by_type = defaultdict(float)
    if not day_list:
        return {}

    chunk_size = 50
    for offset in range(0, len(day_list), chunk_size):
        chunk = day_list[offset : offset + chunk_size]
        key_map = {day: _day_cache_key(user.id, day) for day in chunk}
        cached = cache.get_many(key_map.values())
        for day in chunk:
            cache_key = key_map[day]
            day_stats = cached.pop(cache_key, None)
            if not day_stats and build_missing:
                day_stats = build_stats_for_day(user.id, day)
            if not day_stats:
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
        related_season__user=user, end_date__isnull=True
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
):
    """Build one range's statistics from its cached day payloads.

    ``pass_memo`` is an in-memory dict the caller keeps for one sync pass so
    its ranges share lookups that do not depend on the range (History index
    and days). It is never cached or published. ``build_days(days)`` builds
    and caches a batch of missing days and returns their credit hints; with it
    a missing day is found by the read that would have fetched it anyway.
    """
    clock = _PhaseClock(timings)
    history_memo = {} if pass_memo is None else pass_memo
    is_all_time_query = start_date is None and end_date is None
    items_by_type = defaultdict(dict)
    top_played_by_type = defaultdict(dict)
    minutes_by_type = defaultdict(float)
    plays_by_type = defaultdict(int)
    hour_counts = defaultdict(lambda: defaultdict(int))
    hour_minutes = defaultdict(lambda: defaultdict(float))
    weekday_hour_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    day_play_counts = defaultdict(dict)
    day_minutes_by_type = defaultdict(dict)
    listening_minutes_total = 0
    movie_genres = defaultdict(lambda: {"minutes": 0, "plays": 0, "name": ""})
    tv_genres = defaultdict(lambda: {"minutes": 0, "plays": 0, "name": ""})
    anime_genres = defaultdict(lambda: {"minutes": 0, "plays": 0, "name": ""})
    game_genres = defaultdict(
        lambda: {"minutes": 0, "plays": 0, "name": "", "game_ids": set()}
    )
    movie_decades = defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""})
    tv_decades = defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""})
    anime_decades = defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""})
    game_decades = defaultdict(lambda: {"minutes": 0, "label": "", "game_ids": set()})
    reading_genres = {
        MediaTypes.BOOK.value: defaultdict(
            lambda: {"units": 0, "titles": 0, "name": ""}
        ),
        MediaTypes.COMIC.value: defaultdict(
            lambda: {"units": 0, "titles": 0, "name": ""}
        ),
        MediaTypes.MANGA.value: defaultdict(
            lambda: {"units": 0, "titles": 0, "name": ""}
        ),
    }
    music_rollups = {
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
    podcast_rollups = {
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
    game_rollups = {}
    activity_counts = {}
    activity_counts_by_type = {}
    try:
        credit_backfill_hints = int(credit_backfill_hints or 0)
    except (TypeError, ValueError):
        credit_backfill_hints = 0
    non_play_activity_types = {
        MediaTypes.ANIME.value,
        MediaTypes.GAME.value,
        MediaTypes.BOARDGAME.value,
        MediaTypes.MANGA.value,
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
    }

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
                continue

            for media_type, items in day_stats.get("items", {}).items():
                for item_id_str, meta in items.items():
                    try:
                        item_id = int(item_id_str)
                    except (TypeError, ValueError):
                        continue
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
                        continue

                    existing_activity = existing.get("activity_dt")
                    if activity_dt and (
                        not existing_activity or activity_dt > existing_activity
                    ):
                        existing["media_id"] = meta.get("media_id") or existing.get(
                            "media_id"
                        )
                        existing["status"] = meta.get("status")
                        existing["activity_dt"] = activity_dt

                    if meta.get("score") is not None:
                        existing_score_dt = existing.get("score_dt")
                        if existing_score_dt is None or (
                            score_dt and score_dt > existing_score_dt
                        ):
                            existing["score"] = meta.get("score")
                            existing["score_dt"] = score_dt

                    items_by_type[media_type][item_id] = existing

            for media_type, items in day_stats.get("top_played", {}).items():
                for item_id_str, entry in items.items():
                    try:
                        item_id = int(item_id_str)
                    except (TypeError, ValueError):
                        continue
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
                        aggregate["activity_dt"] is None
                        or activity_dt > aggregate["activity_dt"]
                    ):
                        aggregate["activity_dt"] = activity_dt
                        if entry.get("media_id"):
                            aggregate["media_id"] = entry.get("media_id")

            for media_type, minutes in (
                day_stats.get("totals", {}).get("minutes_by_type", {}).items()
            ):
                minutes_by_type[media_type] += minutes or 0
            listening_minutes_total += (
                day_stats.get("totals", {}).get("listening_minutes") or 0
            )
            for media_type, plays in (
                day_stats.get("totals", {}).get("plays_by_type", {}).items()
            ):
                plays_by_type[media_type] += plays or 0
                day_play_counts[media_type][day.isoformat()] = plays

            for media_type, hours in day_stats.get("hour_counts", {}).items():
                for hour, count in hours.items():
                    hour_counts[media_type][hour] += count
                    weekday_hour_counts[media_type][day.weekday()][int(hour)] += count

            for media_type, hours in day_stats.get("hour_minutes", {}).items():
                for hour, minutes in hours.items():
                    hour_minutes[media_type][hour] += minutes or 0

            for media_type, minutes in day_stats.get(
                "daily_minutes_by_type", {}
            ).items():
                day_minutes_by_type[media_type][day.isoformat()] = minutes

            for genre, payload in day_stats.get("genres", {}).get("movie", {}).items():
                movie_genres[genre]["minutes"] += payload.get("minutes", 0)
                movie_genres[genre]["plays"] += payload.get("plays", 0)
                movie_genres[genre]["name"] = payload.get("name") or genre

            for genre, payload in day_stats.get("genres", {}).get("tv", {}).items():
                tv_genres[genre]["minutes"] += payload.get("minutes", 0)
                tv_genres[genre]["plays"] += payload.get("plays", 0)
                tv_genres[genre]["name"] = payload.get("name") or genre

            for genre, payload in day_stats.get("genres", {}).get("anime", {}).items():
                anime_genres[genre]["minutes"] += payload.get("minutes", 0)
                anime_genres[genre]["plays"] += payload.get("plays", 0)
                anime_genres[genre]["name"] = payload.get("name") or genre

            for genre, payload in day_stats.get("genres", {}).get("game", {}).items():
                game_genres[genre]["minutes"] += payload.get("minutes", 0)
                game_genres[genre]["plays"] += payload.get("plays", 0)
                game_genres[genre]["name"] = payload.get("name") or genre
                for game_id in payload.get("game_ids", []):
                    game_genres[genre]["game_ids"].add(str(game_id))

            for label, payload in day_stats.get("decades", {}).get("movie", {}).items():
                movie_decades[label]["minutes"] += payload.get("minutes", 0)
                movie_decades[label]["plays"] += payload.get("plays", 0)
                movie_decades[label]["label"] = payload.get("label") or label

            for label, payload in day_stats.get("decades", {}).get("tv", {}).items():
                tv_decades[label]["minutes"] += payload.get("minutes", 0)
                tv_decades[label]["plays"] += payload.get("plays", 0)
                tv_decades[label]["label"] = payload.get("label") or label

            for label, payload in day_stats.get("decades", {}).get("anime", {}).items():
                anime_decades[label]["minutes"] += payload.get("minutes", 0)
                anime_decades[label]["plays"] += payload.get("plays", 0)
                anime_decades[label]["label"] = payload.get("label") or label

            for label, payload in day_stats.get("decades", {}).get("game", {}).items():
                game_decades[label]["minutes"] += payload.get("minutes", 0)
                game_decades[label]["label"] = payload.get("label") or label
                for game_id in payload.get("game_ids", []):
                    game_decades[label]["game_ids"].add(str(game_id))

            for reading_type in (
                MediaTypes.BOOK.value,
                MediaTypes.COMIC.value,
                MediaTypes.MANGA.value,
            ):
                for genre, payload in (
                    day_stats.get("genres", {}).get(reading_type, {}).items()
                ):
                    reading_genres[reading_type][genre]["units"] += payload.get(
                        "units", 0
                    )
                    reading_genres[reading_type][genre]["titles"] += payload.get(
                        "titles", 0
                    )
                    reading_genres[reading_type][genre]["name"] = (
                        payload.get("name") or genre
                    )

            for key, value in day_stats.get("music", {}).items():
                for item_id_str, payload in value.items():
                    existing = music_rollups[key][item_id_str]
                    existing["minutes"] += payload.get("minutes", 0)
                    existing["plays"] += payload.get("plays", 0)
                    for field in (
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
                    ):
                        if payload.get(field) and not existing.get(field):
                            existing[field] = payload.get(field)

            for key, value in day_stats.get("podcast", {}).items():
                for item_id_str, payload in value.items():
                    existing = podcast_rollups[key][item_id_str]
                    if key == "shows":
                        existing["minutes"] += payload.get("minutes", 0)
                        existing["plays"] += payload.get("plays", 0)
                        for field in (
                            "title",
                            "show",
                            "show_id",
                            "podcast_uuid",
                            "slug",
                            "image",
                        ):
                            if payload.get(field) and not existing.get(field):
                                existing[field] = payload.get(field)
                    else:
                        duration = payload.get("duration_seconds", 0) or 0
                        if duration > existing.get("duration_seconds", 0):
                            existing["duration_seconds"] = duration
                        for field in (
                            "title",
                            "show",
                            "show_id",
                            "episode_id",
                            "podcast_uuid",
                            "slug",
                            "image",
                        ):
                            if payload.get(field) and not existing.get(field):
                                existing[field] = payload.get(field)

            for item_id_str, payload in (
                day_stats.get("game", {}).get("by_game", {}).items()
            ):
                try:
                    item_id = int(item_id_str)
                except (TypeError, ValueError):
                    continue
                existing = game_rollups.get(item_id)
                if not existing:
                    existing = {
                        "minutes_total": 0,
                        "days": 0,
                        "activity_dt": None,
                        "media_id": payload.get("media_id"),
                    }
                    game_rollups[item_id] = existing
                existing["minutes_total"] += payload.get("minutes_total", 0)
                existing["days"] += payload.get("days", 0)
                activity_dt = _parse_activity_dt(payload.get("activity_dt"))
                if activity_dt and (
                    existing["activity_dt"] is None
                    or activity_dt > existing["activity_dt"]
                ):
                    existing["activity_dt"] = activity_dt
                    if payload.get("media_id"):
                        existing["media_id"] = payload.get("media_id")

            daily_minutes = day_stats.get("daily_minutes_by_type", {}) or {}
            plays_total = sum(
                day_stats.get("totals", {}).get("plays_by_type", {}).values()
            )
            activity_total = plays_total
            # Same rule as the total, kept per media type so the heatmap can
            # follow the media-type filter.
            by_type = {
                media_type: plays
                for media_type, plays in day_stats.get("totals", {})
                .get("plays_by_type", {})
                .items()
                if plays
            }
            for media_type in non_play_activity_types:
                if daily_minutes.get(media_type, 0):
                    activity_total += 1
                    by_type[media_type] = by_type.get(media_type, 0) + 1
            if activity_total == 0 and sum(daily_minutes.values()) > 0:
                activity_total = 1
                busiest = max(daily_minutes, key=daily_minutes.get)
                by_type[busiest] = 1
            activity_counts[day] = activity_total
            if by_type:
                activity_counts_by_type[day] = by_type
        clock.mark("day_merge")

    active_types = list(getattr(user, "get_active_media_types", list)())
    if not active_types:
        active_types = list(MediaTypes.values)

    if not user.season_enabled and MediaTypes.SEASON.value in active_types:
        active_types = [mt for mt in active_types if mt != MediaTypes.SEASON.value]
        items_by_type.pop(MediaTypes.SEASON.value, None)
        top_played_by_type.pop(MediaTypes.SEASON.value, None)

    # Entries without any start/end date can never land in a calendar-day
    # bucket, so the day-builder pipeline above never sees them. Pick them up
    # here directly so undated items (e.g. Planning-status items that haven't
    # been started, or imports missing dates) still count towards status,
    # score and media-count aggregates. They belong to All Time only: a date
    # range holds just the activity that happened inside it (#1521).
    # TV is excluded here: it (and Season) override start_date/end_date as
    # computed properties derived from Episode rows, not real DB columns, so
    # they can't be filtered on directly and raise FieldError if added back.
    undated_models = [
        MediaTypes.MOVIE.value,
        MediaTypes.ANIME.value,
        MediaTypes.MANGA.value,
        MediaTypes.GAME.value,
        MediaTypes.BOARDGAME.value,
        MediaTypes.MUSIC.value,
        MediaTypes.PODCAST.value,
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
    ]
    for media_type in undated_models:
        if not is_all_time_query or media_type not in active_types:
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

    # Album trackers are separate from Music track plays. Include them in
    # library/score statistics without inventing plays or listening time.
    if MediaTypes.MUSIC.value in active_types:
        AlbumTracker = apps.get_model("app", "AlbumTracker")
        albums = list(AlbumTracker.objects.filter(user=user).select_related("album").order_by("-updated_at", "-pk"))
        release_ids = {row.album.musicbrainz_release_id for row in albums if row.album.musicbrainz_release_id}
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
            identity = album.musicbrainz_release_group_id or release_groups.get(album.musicbrainz_release_id) or album.musicbrainz_release_id or f"local:{album.pk}"
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

    clock.mark("undated")
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
        items = dict(items_by_type.get(media_type, {}))
        if media_type == MediaTypes.TV.value:
            items.update(_tv_season_items(items_by_type))
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

    score_distribution = {}
    total_scored = 0
    total_score_sum = 0
    top_rated_heap = []
    top_rated_by_type = {}
    global_counter = itertools.count()
    score_scale_max = getattr(user, "rating_scale_max", 10)
    try:
        score_scale_max = int(score_scale_max)
    except (TypeError, ValueError):
        score_scale_max = 10
    if score_scale_max not in (5, 10):
        score_scale_max = 10
    score_range = range(score_scale_max + 1)

    for media_type in distribution_types:
        items = dict(items_by_type.get(media_type, {}))
        if media_type == MediaTypes.TV.value:
            items.update(_tv_season_items(items_by_type))
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

    def top_rated_ref(meta):
        return (meta.get("media_type"), meta.get("media_id"), meta.get("item_id"))

    media_refs = [top_rated_ref(meta) for meta in top_rated_meta]
    for metas in top_rated_by_type.values():
        media_refs.extend(
            top_rated_ref(meta) for meta in metas
        )

    fetched = _fetch_media_objects({(kind, pk) for kind, pk, _ in media_refs})
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
        TV = apps.get_model("app", MediaTypes.TV.value)
        grouped = {
            (tv.pk, tv.item_id): tv
            for tv in TV.objects.filter(pk__in=[ref[1] for ref in grouped_refs])
            .select_related("item")
        }
        for ref in grouped_refs:
            tv = grouped.get((ref[1], ref[2]))
            if tv:
                tv.route_media_type = MediaTypes.ANIME.value
                tv.item.route_media_type = MediaTypes.ANIME.value
                media_map[ref] = tv
    # The overall list and the per-type lists mostly name the same rows.
    # Annotate each row once (the annotation re-reads the row and looks up
    # its progress total) and let both lists share the result.
    annotated_refs = [ref for ref in dict.fromkeys(media_refs) if media_map.get(ref)]
    annotated = stats._annotate_top_rated_media([media_map[ref] for ref in annotated_refs])
    annotated_by_identity = {
        (media._meta.concrete_model, media.pk): media for media in annotated
    }
    annotated_map = {
        ref: annotated_by_identity[identity]
        for ref in annotated_refs
        if (identity := (media_map[ref]._meta.concrete_model, media_map[ref].pk))
        in annotated_by_identity
    }
    top_rated_media = [
        annotated_map[ref]
        for ref in (
            top_rated_ref(meta) for meta in top_rated_meta
        )
        if ref in annotated_map
    ]

    top_rated_by_type_media = {}
    for media_type, metas in top_rated_by_type.items():
        top_rated_by_type_media[media_type] = [
            annotated_map[ref]
            for ref in (
                top_rated_ref(meta) for meta in metas
            )
            if ref in annotated_map
        ]

    top_rated_score_map = {}
    for meta in top_rated_meta:
        ref = top_rated_ref(meta)
        score = meta.get("score")
        if ref[0] and ref[1] and score is not None:
            top_rated_score_map[ref] = score
    for metas in top_rated_by_type.values():
        for meta in metas:
            ref = top_rated_ref(meta)
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

    clock.mark("top_rated")
    top_played = {}
    target_media_types = ["movie", "tv", "game", "boardgame", "anime", "music"]
    media_refs = []
    for media_type in target_media_types:
        entries = list(top_played_by_type.get(media_type, {}).values())
        entries = [entry for entry in entries if entry.get("minutes", 0) > 0]
        baseline_dt = datetime(1970, 1, 1, tzinfo=timezone.get_current_timezone())
        entries.sort(
            key=lambda entry: (
                entry.get("minutes", 0),
                entry.get("activity_dt") or baseline_dt,
            ),
            reverse=True,
        )
        limit = STATISTICS_TOP_N
        entries = entries[:limit]
        top_played[media_type] = entries
        for entry in entries:
            media_refs.append((media_type, entry.get("media_id")))

    media_map = _fetch_media_objects(set(media_refs))
    for media_type, entries in top_played.items():
        enriched = []
        for entry in entries:
            media = media_map.get((media_type, entry.get("media_id")))
            if not media:
                continue
            total_minutes = entry.get("minutes", 0)
            formatted_duration = helpers.minutes_to_hhmm(total_minutes)
            if media_type == "boardgame":
                formatted_duration = (
                    f"{int(total_minutes)} play{'s' if int(total_minutes) != 1 else ''}"
                )
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

    clock.mark("top_played")
    hours_per_media_type = {}
    for media_type, total_minutes in minutes_by_type.items():
        if media_type == MediaTypes.BOARDGAME.value:
            hours_per_media_type[media_type] = (
                f"{int(total_minutes)} play{'s' if int(total_minutes) != 1 else ''}"
            )
        else:
            hours_per_media_type[media_type] = stats._format_hours_minutes(
                total_minutes, user.duration_format
            )

    if start_date is None and end_date is None and day_list:
        start_date = _day_boundary_datetime(day_list[0])
        end_date = _day_boundary_datetime(day_list[-1], end_of_day=True)

    activity_counts_by_date = {day: activity_counts.get(day, 0) for day in day_list}
    from users.models import (
        WeekStartDayChoices,
    )

    week_start_sunday = user.week_start_day == WeekStartDayChoices.SUNDAY
    activity_data = _build_activity_data(
        activity_counts_by_date,
        activity_counts_by_type,
        day_minutes_by_type,
        day_list,
        start_date,
        end_date,
        week_start_sunday=week_start_sunday,
    )

    media_type_distribution = stats.get_media_type_distribution(
        media_count,
        minutes_by_type,
    )
    status_pie_chart_data = stats.get_status_pie_chart_data(status_distribution_payload)

    daily_hours_by_media_type = _build_daily_hours_chart(day_minutes_by_type, day_list)

    movie_chart = _build_media_charts_from_counts(
        day_play_counts.get(MediaTypes.MOVIE.value, {}),
        hour_counts.get(MediaTypes.MOVIE.value, {}),
        config.get_stats_color(MediaTypes.MOVIE.value),
        "Movie Plays",
    )
    tv_chart = _build_media_charts_from_counts(
        day_play_counts.get(MediaTypes.TV.value, {}),
        hour_counts.get(MediaTypes.TV.value, {}),
        config.get_stats_color(MediaTypes.TV.value),
        "Episode Plays",
    )
    anime_chart = _build_media_charts_from_counts(
        day_play_counts.get(MediaTypes.ANIME.value, {}),
        hour_counts.get(MediaTypes.ANIME.value, {}),
        config.get_stats_color(MediaTypes.ANIME.value),
        "Anime Plays",
    )
    music_chart = _build_media_charts_from_counts(
        day_play_counts.get(MediaTypes.MUSIC.value, {}),
        hour_counts.get(MediaTypes.MUSIC.value, {}),
        config.get_stats_color(MediaTypes.MUSIC.value),
        "Music Plays",
    )
    podcast_chart = _build_media_charts_from_counts(
        day_play_counts.get(MediaTypes.PODCAST.value, {}),
        hour_counts.get(MediaTypes.PODCAST.value, {}),
        config.get_stats_color(MediaTypes.PODCAST.value),
        "Podcast Plays",
    )
    video_chart = _build_media_charts_from_counts(
        day_play_counts.get(MediaTypes.VIDEO.value, {}),
        hour_counts.get(MediaTypes.VIDEO.value, {}),
        config.get_stats_color(MediaTypes.VIDEO.value),
        "Video Plays",
    )

    tv_total_minutes = minutes_by_type.get(MediaTypes.TV.value, 0)
    anime_total_minutes = minutes_by_type.get(MediaTypes.ANIME.value, 0)
    movie_total_minutes = minutes_by_type.get(MediaTypes.MOVIE.value, 0)
    music_total_minutes = minutes_by_type.get(MediaTypes.MUSIC.value, 0)
    podcast_total_minutes = minutes_by_type.get(MediaTypes.PODCAST.value, 0)
    video_total_minutes = minutes_by_type.get(MediaTypes.VIDEO.value, 0)
    game_total_minutes = minutes_by_type.get(MediaTypes.GAME.value, 0)

    tv_total_hours = tv_total_minutes / 60 if tv_total_minutes else 0
    anime_total_hours = anime_total_minutes / 60 if anime_total_minutes else 0
    movie_total_hours = movie_total_minutes / 60 if movie_total_minutes else 0
    game_total_hours = game_total_minutes / 60 if game_total_minutes else 0

    tv_consumption = {
        "hours": _compute_metric_breakdown_for_range(
            tv_total_hours, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            plays_by_type.get(MediaTypes.TV.value, 0), start_date, end_date
        ),
        "charts": tv_chart,
        "has_data": plays_by_type.get(MediaTypes.TV.value, 0) > 0,
        "top_genres": [
            {**item, "formatted_duration": helpers.minutes_to_hhmm(item["minutes"])}
            for item in sorted(
                tv_genres.values(),
                key=lambda x: (x["minutes"], x["plays"]),
                reverse=True,
            )[:STATISTICS_TOP_N]
        ],
        "top_decades": [
            {**item, "formatted_duration": helpers.minutes_to_hhmm(item["minutes"])}
            for item in sorted(
                tv_decades.values(),
                key=lambda x: (x["minutes"], x["plays"]),
                reverse=True,
            )[:STATISTICS_TOP_N]
        ],
    }

    movie_consumption = {
        "hours": _compute_metric_breakdown_for_range(
            movie_total_hours, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            plays_by_type.get(MediaTypes.MOVIE.value, 0), start_date, end_date
        ),
        "charts": movie_chart,
        "has_data": plays_by_type.get(MediaTypes.MOVIE.value, 0) > 0,
        "top_genres": [
            {**item, "formatted_duration": helpers.minutes_to_hhmm(item["minutes"])}
            for item in sorted(
                movie_genres.values(),
                key=lambda x: (x["minutes"], x["plays"]),
                reverse=True,
            )[:STATISTICS_TOP_N]
        ],
        "top_decades": [
            {**item, "formatted_duration": helpers.minutes_to_hhmm(item["minutes"])}
            for item in sorted(
                movie_decades.values(),
                key=lambda x: (x["minutes"], x["plays"]),
                reverse=True,
            )[:STATISTICS_TOP_N]
        ],
    }

    anime_consumption = {
        "hours": _compute_metric_breakdown_for_range(
            anime_total_hours, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            plays_by_type.get(MediaTypes.ANIME.value, 0), start_date, end_date
        ),
        "charts": anime_chart,
        "has_data": plays_by_type.get(MediaTypes.ANIME.value, 0) > 0,
        "top_genres": [
            {**item, "formatted_duration": helpers.minutes_to_hhmm(item["minutes"])}
            for item in sorted(
                anime_genres.values(),
                key=lambda x: (x["minutes"], x["plays"]),
                reverse=True,
            )[:STATISTICS_TOP_N]
        ],
        "top_decades": [
            {**item, "formatted_duration": helpers.minutes_to_hhmm(item["minutes"])}
            for item in sorted(
                anime_decades.values(),
                key=lambda x: (x["minutes"], x["plays"]),
                reverse=True,
            )[:STATISTICS_TOP_N]
        ],
    }

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

    music_consumption = {
        "minutes": _compute_metric_breakdown_for_range(
            music_total_minutes, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            plays_by_type.get(MediaTypes.MUSIC.value, 0), start_date, end_date
        ),
        "charts": music_chart,
        "has_data": plays_by_type.get(MediaTypes.MUSIC.value, 0) > 0,
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

    podcast_consumption = {
        "minutes": _compute_metric_breakdown_for_range(
            podcast_total_minutes, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            plays_by_type.get(MediaTypes.PODCAST.value, 0), start_date, end_date
        ),
        "charts": podcast_chart,
        "has_data": plays_by_type.get(MediaTypes.PODCAST.value, 0) > 0,
    }

    most_played = sorted(
        podcast_rollups["shows"].values(),
        key=lambda x: (x["plays"], x["minutes"]),
        reverse=True,
    )[:STATISTICS_TOP_N]
    most_listened = sorted(
        podcast_rollups["shows"].values(),
        key=lambda x: (x["minutes"], x["plays"]),
        reverse=True,
    )[:STATISTICS_TOP_N]
    longest_episodes = sorted(
        [
            ep
            for ep in podcast_rollups["episodes"].values()
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

    video_consumption = {
        "minutes": _compute_metric_breakdown_for_range(
            video_total_minutes, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            plays_by_type.get(MediaTypes.VIDEO.value, 0), start_date, end_date
        ),
        "charts": video_chart,
        "has_data": plays_by_type.get(MediaTypes.VIDEO.value, 0) > 0,
    }

    clock.mark("charts")
    game_hours_by_year = defaultdict(float)
    game_hours_by_month = defaultdict(float)
    game_day_minutes = day_minutes_by_type.get(MediaTypes.GAME.value, {})
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
    game_hours_charts = {
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

    game_genre_items = []
    for genre, payload in game_genres.items():
        game_genre_items.append(
            {
                "minutes": payload["minutes"],
                "games": len(payload["game_ids"]),
                "plays": len(payload["game_ids"]),
                "name": genre,
                "formatted_duration": helpers.minutes_to_hhmm(payload["minutes"]),
            }
        )
    game_genre_items = sorted(
        game_genre_items, key=lambda x: (x["minutes"], x["games"]), reverse=True
    )[:STATISTICS_TOP_N]

    game_decade_items = []
    for label, payload in game_decades.items():
        game_decade_items.append(
            {
                "minutes": payload["minutes"],
                "games": len(payload["game_ids"]),
                "plays": len(payload["game_ids"]),
                "label": label,
                "formatted_duration": helpers.minutes_to_hhmm(payload["minutes"]),
            }
        )
    game_decade_items = sorted(
        game_decade_items, key=lambda x: (x["minutes"], x["games"]), reverse=True
    )[:STATISTICS_TOP_N]

    top_daily_avg_games = sorted(
        game_data, key=lambda x: x["daily_average"], reverse=True
    )[:STATISTICS_TOP_N]
    game_media_map = _fetch_media_objects(
        {(MediaTypes.GAME.value, item["media_id"]) for item in top_daily_avg_games}
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

    platform_breakdown = _build_platform_breakdown(
        game_data, collection_platform_map, item_info_map
    )

    game_consumption = {
        "hours": _compute_metric_breakdown_for_range(
            game_total_hours, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            _sum_top_played_plays(top_played_by_type, MediaTypes.GAME.value),
            start_date,
            end_date,
        ),
        "average_daily_playtime_hours": _avg_daily_playtime_hours(
            game_total_hours,
            day_minutes_by_type.get(MediaTypes.GAME.value, {}),
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

    boardgame_total_minutes = minutes_by_type.get(MediaTypes.BOARDGAME.value, 0)
    boardgame_total_hours = (
        boardgame_total_minutes / 60 if boardgame_total_minutes else 0
    )
    boardgame_consumption = {
        "hours": _compute_metric_breakdown_for_range(
            boardgame_total_hours, start_date, end_date
        ),
        "plays": _compute_metric_breakdown_for_range(
            _sum_top_played_plays(top_played_by_type, MediaTypes.BOARDGAME.value),
            start_date,
            end_date,
        ),
        "average_daily_playtime_hours": _avg_daily_playtime_hours(
            boardgame_total_hours,
            day_minutes_by_type.get(MediaTypes.BOARDGAME.value, {}),
        ),
        "has_data": boardgame_total_minutes > 0
        or bool(top_played_by_type.get(MediaTypes.BOARDGAME.value)),
    }

    clock.mark("games")

    def _build_cached_reading_consumption(media_type):
        unit_name = config.get_unit(media_type, short=False) or "Unit"
        chart_label = f"{unit_name}s Read"
        completion_label_map = {
            MediaTypes.BOOK.value: "Books Finished",
            MediaTypes.COMIC.value: "Comics Finished",
            MediaTypes.MANGA.value: "Manga Finished",
        }
        completion_label = completion_label_map.get(media_type, "Items Finished")
        release_label_map = {
            MediaTypes.BOOK.value: "Books Released",
            MediaTypes.COMIC.value: "Comics Released",
            MediaTypes.MANGA.value: "Manga Released",
        }
        release_label = release_label_map.get(media_type, "Items Released")
        color = config.get_stats_color(media_type)
        units_by_day = day_minutes_by_type.get(media_type, {})
        unit_total = sum(units_by_day.values()) if units_by_day else 0

        # One finished title per item, dated by its latest completed entry
        # (same rule as get_reading_consumption_stats).
        completed_lengths = []
        latest_completed_by_item = {}
        model = apps.get_model("app", media_type)
        completed_queryset = model.objects.filter(
            user=user, status=Status.COMPLETED.value
        )
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
                media_type == MediaTypes.BOOK.value
                and entry.item__format == "audiobook"
            )
            completed_length = (
                0
                if is_audiobook
                else entry.progress or entry.item__number_of_pages or 0
            )
            if completed_length > 0:
                completed_lengths.append(completed_length)
            completed_dt = stats._get_activity_datetime(entry) or entry.created_at
            previous = latest_completed_by_item.get(entry.item_id)
            if previous is None or completed_dt > previous:
                latest_completed_by_item[entry.item_id] = completed_dt
        completed_datetimes = [
            stats._localize_datetime(value)
            for value in latest_completed_by_item.values()
        ]
        completion_total = len(completed_datetimes)
        item_ids = [
            meta.get("item_id")
            for meta in items_by_type.get(media_type, {}).values()
            if meta.get("item_id")
        ]
        top_entries = list(top_played_by_type.get(media_type, {}).values())
        listening_minutes = (
            listening_minutes_total if media_type == MediaTypes.BOOK.value else 0
        )
        has_any_reading_data = bool(
            unit_total
            or completion_total
            or item_ids
            or top_entries
            or listening_minutes
        )
        if not has_any_reading_data:
            return _empty_reading_consumption(
                unit_name=unit_name,
                completion_label=completion_label,
            )

        charts = _build_media_charts_from_counts(
            units_by_day,
            hour_counts.get(media_type, {}),
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
        top_media_refs = {
            (media_type, entry.get("media_id"))
            for entry in top_entries
            if entry.get("media_id")
        }
        top_media_map = _fetch_media_objects(top_media_refs)
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
                    "formatted_units": f"{round(units)} {unit_name.lower()}{'' if round(units) == 1 else 's'}",
                }
            )

        genre_items = []
        for payload in reading_genres.get(media_type, {}).values():
            units = payload.get("units", 0)
            if units <= 0:
                continue
            titles = payload.get("titles", 0)
            genre_items.append(
                {
                    "name": payload.get("name") or "",
                    "units": units,
                    "titles": titles,
                    "formatted_units": f"{round(units)} {unit_name.lower()}{'' if round(units) == 1 else 's'}",
                }
            )
        genre_items = sorted(
            genre_items, key=lambda item: (item["units"], item["titles"]), reverse=True
        )[:STATISTICS_TOP_N]

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

        average_length = average_completed_length
        average_rating = (
            round(sum(scored_values) / len(scored_values), 2) if scored_values else None
        )

        return {
            "units": _compute_metric_breakdown_for_range(
                unit_total, start_date, end_date
            ),
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
            "highlights": {
                "longest_item": longest_item,
                "shortest_item": shortest_item,
                "average_length": average_length,
                "average_rating": average_rating,
            },
        }

    reading_media_types = {
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
        MediaTypes.MANGA.value,
    }
    reading_types_with_data = {
        media_type
        for media_type in reading_media_types
        if media_type in active_types
        and (
            minutes_by_type.get(media_type)
            or items_by_type.get(media_type)
            or top_played_by_type.get(media_type)
            or (media_type == MediaTypes.BOOK.value and listening_minutes_total)
        )
    }
    book_consumption = (
        _build_cached_reading_consumption(MediaTypes.BOOK.value)
        if MediaTypes.BOOK.value in reading_types_with_data
        else _empty_reading_consumption("Page", "Books Finished")
    )
    comic_consumption = (
        _build_cached_reading_consumption(MediaTypes.COMIC.value)
        if MediaTypes.COMIC.value in reading_types_with_data
        else _empty_reading_consumption("Page", "Comics Finished")
    )
    manga_consumption = (
        _build_cached_reading_consumption(MediaTypes.MANGA.value)
        if MediaTypes.MANGA.value in reading_types_with_data
        else _empty_reading_consumption("Page", "Manga Finished")
    )

    clock.mark("reading")
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
    for _mt, _day_map in day_minutes_by_type.items():
        _active_days = sorted(d for d, m in _day_map.items() if m > 0)
        if not _active_days:
            continue
        _first_day = date_type.fromisoformat(_active_days[0])
        _last_day = date_type.fromisoformat(_active_days[-1])
        _first_payload = _get_history_day_payload(user, _first_day, history_memo)
        _last_payload = _get_history_day_payload(user, _last_day, history_memo)
        _type_today_user_h, _type_today_user_year = _get_today_history_entries(
            user, media_type_filter=_mt, memo=history_memo
        )
        _type_today_h, _type_today_year = _get_today_release_entry(
            user, media_type_filter=_mt
        )
        history_highlights_by_type[_mt] = {
            "first_play": _select_history_entry_for_day(
                _first_payload, pick_earliest=True, media_type_filter=_mt
            ),
            "last_play": _select_history_entry_for_day(
                _last_payload, pick_latest=True, media_type_filter=_mt
            ),
            "today_card": _build_today_card(
                _type_today_h,
                _type_today_year,
                _type_today_user_h,
                _type_today_user_year,
            ),
            "today_month": today.month,
            "today_day": today.day,
        }

    clock.mark("highlights")
    # Per-media-type average scores (uses items_by_type already in memory)
    _average_score_by_type = {}
    _score_count_by_type = {}
    _score_sum_by_type = {}
    for _mt in active_types:
        _mt_items = items_by_type.get(_mt, {})
        _mt_sum = 0.0
        _mt_count = 0
        for _meta in _mt_items.values():
            _score = _meta.get("score")
            if _score is None:
                continue
            _sv = float(_score)
            if score_scale_max == SCORE_SCALE_FIVE_POINT:
                _sv = _sv / 2
            _mt_sum += _sv
            _mt_count += 1
        _average_score_by_type[_mt] = (
            round(_mt_sum / _mt_count, 2) if _mt_count > 0 else None
        )
        _score_count_by_type[_mt] = _mt_count
        _score_sum_by_type[_mt] = _mt_sum

    _end_date_for_streak = end_date.date() if hasattr(end_date, "date") else end_date
    _end_epoch_day = (
        (_end_date_for_streak - _EPOCH_DAY).days
        if _end_date_for_streak is not None
        else None
    )
    _day_list_iso = {day.isoformat() for day in day_list}

    _all_total_minutes = sum(minutes_by_type.values())

    # "all" bucket mirrors the already-computed activity_data.stats values
    summary_stats_by_type = {
        "all": {
            "completed": status_distribution_payload["total_completed"],
            "total": media_count.get("total", 0),
            "total_minutes": _all_total_minutes,
            "average_score": average_score,
            "has_score": bool(total_scored),
            "most_active_day": activity_data["stats"].get("most_active_day"),
            "most_active_day_percentage": activity_data["stats"].get(
                "most_active_day_percentage", 0
            ),
            "current_streak": activity_data["stats"].get("current_streak", 0),
            "longest_streak": activity_data["stats"].get("longest_streak", 0),
            "longest_streak_start": _date_to_iso(
                activity_data["stats"].get("longest_streak_start")
            ),
            "longest_streak_end": _date_to_iso(
                activity_data["stats"].get("longest_streak_end")
            ),
        }
    }
    for _mt, _day_map in day_minutes_by_type.items():
        _mt_date_counts = {
            date_type.fromisoformat(d): 1 for d, m in _day_map.items() if m > 0
        }
        _mt_most_active, _mt_day_pct = stats.calculate_most_active_weekday(
            {_mt: _day_map}, day_list
        )
        _mt_streaks = stats.calculate_streak_details(
            _mt_date_counts, _end_date_for_streak
        )
        _mt_completed = status_distribution.get(_mt, {}).get(Status.COMPLETED.value, 0)
        _mt_total = media_count.get(_mt, 0)
        _mt_minutes = minutes_by_type.get(_mt, 0)
        summary_stats_by_type[_mt] = {
            "completed": _mt_completed,
            "total": _mt_total,
            "total_minutes": _mt_minutes,
            "average_score": _average_score_by_type.get(_mt),
            "has_score": _average_score_by_type.get(_mt) is not None,
            "most_active_day": _mt_most_active,
            "most_active_day_percentage": _mt_day_pct,
            "current_streak": _mt_streaks.get("current_streak", 0),
            "longest_streak": _mt_streaks.get("longest_streak", 0),
            "longest_streak_start": _date_to_iso(
                _mt_streaks.get("longest_streak_start")
            ),
            "longest_streak_end": _date_to_iso(_mt_streaks.get("longest_streak_end")),
            # Raw pieces so the page can merge several media types exactly.
            "score_count": _score_count_by_type.get(_mt, 0),
            "score_sum": round(_score_sum_by_type.get(_mt, 0.0), 4),
            "weekday_minutes": _weekday_minutes(_day_map, _day_list_iso),
            "active_runs": _active_day_runs(_day_map),
            "streak_end_day": _end_epoch_day,
        }

    clock.mark("summary")
    has_movie_tv_activity = bool(
        plays_by_type.get(MediaTypes.MOVIE.value, 0)
        or plays_by_type.get(MediaTypes.TV.value, 0)
    )
    if (
        not has_movie_tv_activity
        and is_all_time_query
        and _has_dateless_movie_or_episode_activity(user)
    ):
        has_movie_tv_activity = True
    # start_date/end_date may have been narrowed to day_list's bounds above
    # (for chart axes); `is_all_time_query` (captured before that narrowing)
    # tells top talent to still skip date filtering for a genuine all-time
    # query, so dateless entries are included despite the concrete bounds.
    top_talent = (
        _aggregate_top_talent(
            user,
            start_date,
            end_date,
            schedule_missing_backfill=credit_backfill_hints <= 0,
            is_all_time=is_all_time_query,
        )
        if has_movie_tv_activity
        else _empty_top_talent_payload(
            sort_by=getattr(user, "top_talent_sort_by", "plays"),
        )
    )

    clock.mark("top_talent")
    weekday_hour_chart_data = _build_weekday_hour_charts(weekday_hour_counts)

    combined_hours_charts = _build_combined_hours_charts(
        day_minutes_by_type, hour_minutes
    )

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

    # Shared bonus cards: one "Average Daily Playtime" across Games + Board Games,
    # one "Average Page Length" across Book/Comic/Manga - not per-type, so they can
    # appear once each on the "All" filter alongside their own type's filter.
    combined_games_hours = game_total_hours + boardgame_total_hours
    combined_games_day_minutes: defaultdict = defaultdict(float)
    for day_str, minutes in day_minutes_by_type.get(MediaTypes.GAME.value, {}).items():
        combined_games_day_minutes[day_str] += minutes or 0
    for day_str, minutes in day_minutes_by_type.get(
        MediaTypes.BOARDGAME.value, {}
    ).items():
        combined_games_day_minutes[day_str] += minutes or 0
    games_avg_daily_playtime = _avg_daily_playtime_hours(
        combined_games_hours, combined_games_day_minutes
    )
    games_bonus = (
        {"kind": "playtime", "icon": "gamepad", "value": games_avg_daily_playtime}
        if games_avg_daily_playtime
        and (game_consumption["has_data"] or boardgame_consumption["has_data"])
        else None
    )

    all_reading_lengths = (
        book_consumption.get("completed_lengths", [])
        + comic_consumption.get("completed_lengths", [])
        + manga_consumption.get("completed_lengths", [])
    )
    reading_avg_length = (
        round(sum(all_reading_lengths) / len(all_reading_lengths), 1)
        if all_reading_lengths
        else 0
    )
    reading_bonus = (
        {"kind": "length", "icon": "book-open", "value": reading_avg_length}
        if reading_avg_length
        else None
    )

    consumption_stats_by_type = {
        MediaTypes.TV.value: {
            "primary": _pack_metric(
                tv_consumption["hours"], "Hours Watched", "Hours", "clock"
            ),
            "secondary": _pack_metric(
                tv_consumption["plays"], "Episode Plays", "Episodes", "repeat"
            ),
            "bonuses": [],
            "has_data": tv_consumption["has_data"],
        },
        MediaTypes.MOVIE.value: {
            "primary": _pack_metric(
                movie_consumption["hours"], "Hours Watched", "Hours", "clock"
            ),
            "secondary": _pack_metric(
                movie_consumption["plays"], "Movie Plays", "Movies", "repeat"
            ),
            "bonuses": [],
            "has_data": movie_consumption["has_data"],
        },
        MediaTypes.ANIME.value: {
            "primary": _pack_metric(
                anime_consumption["hours"], "Hours Watched", "Hours", "clock"
            ),
            "secondary": _pack_metric(
                anime_consumption["plays"], "Episode Plays", "Episodes", "repeat"
            ),
            "bonuses": [],
            "has_data": anime_consumption["has_data"],
        },
        MediaTypes.MUSIC.value: {
            "primary": _pack_metric(
                _minutes_breakdown_to_hours(music_consumption["minutes"]),
                "Hours Listened",
                "Hours",
                "clock",
            ),
            "secondary": _pack_metric(
                music_consumption["plays"], "Music Plays", "Plays", "repeat"
            ),
            "bonuses": [],
            "has_data": music_consumption["has_data"],
        },
        MediaTypes.PODCAST.value: {
            "primary": _pack_metric(
                _minutes_breakdown_to_hours(podcast_consumption["minutes"]),
                "Hours Listened",
                "Hours",
                "clock",
            ),
            "secondary": _pack_metric(
                podcast_consumption["plays"], "Podcast Plays", "Plays", "repeat"
            ),
            "bonuses": [],
            "has_data": podcast_consumption["has_data"],
        },
        MediaTypes.VIDEO.value: {
            "primary": _pack_metric(
                _minutes_breakdown_to_hours(video_consumption["minutes"]),
                "Hours Watched",
                "Hours",
                "clock",
            ),
            "secondary": _pack_metric(
                video_consumption["plays"], "Video Plays", "Plays", "repeat"
            ),
            "bonuses": [],
            "has_data": video_consumption["has_data"],
        },
        MediaTypes.GAME.value: {
            "primary": _pack_metric(
                game_consumption["hours"], "Hours Played", "Hours", "clock"
            ),
            "secondary": _pack_metric(
                game_consumption["plays"], "Game Plays", "Plays", "repeat"
            ),
            "bonuses": [games_bonus] if games_bonus else [],
            "has_data": game_consumption["has_data"],
        },
        MediaTypes.BOARDGAME.value: {
            "primary": _pack_metric(
                boardgame_consumption["hours"], "Hours Played", "Hours", "clock"
            ),
            "secondary": _pack_metric(
                boardgame_consumption["plays"], "Board Game Plays", "Plays", "repeat"
            ),
            "bonuses": [games_bonus] if games_bonus else [],
            "has_data": boardgame_consumption["has_data"],
        },
        MediaTypes.BOOK.value: {
            "primary": _pack_metric(
                book_consumption["units"],
                book_consumption["unit_label"],
                book_consumption["unit_name"] + "s",
                "book-open",
                total_decimals=0,
            ),
            "secondary": _pack_metric(
                book_consumption["completions"],
                book_consumption["completion_label"],
                "Books",
                "checkmark",
            ),
            "bonuses": [reading_bonus] if reading_bonus else [],
            "has_data": book_consumption["has_data"],
        },
        MediaTypes.COMIC.value: {
            "primary": _pack_metric(
                comic_consumption["units"],
                comic_consumption["unit_label"],
                comic_consumption["unit_name"] + "s",
                "book-open",
                total_decimals=0,
            ),
            "secondary": _pack_metric(
                comic_consumption["completions"],
                comic_consumption["completion_label"],
                "Comics",
                "checkmark",
            ),
            "bonuses": [reading_bonus] if reading_bonus else [],
            "has_data": comic_consumption["has_data"],
        },
        MediaTypes.MANGA.value: {
            "primary": _pack_metric(
                manga_consumption["units"],
                manga_consumption["unit_label"],
                manga_consumption["unit_name"] + "s",
                "book-open",
                total_decimals=0,
            ),
            "secondary": _pack_metric(
                manga_consumption["completions"],
                manga_consumption["completion_label"],
                "Manga",
                "checkmark",
            ),
            "bonuses": [reading_bonus] if reading_bonus else [],
            "has_data": manga_consumption["has_data"],
        },
    }

    consumption_stats_by_type["all"] = {
        # Reuses the exact total already shown by the hero ("You've spent X hours") and
        # total_activity_comparison, i.e. _all_total_minutes - not a second, different
        # "all-media hours" figure. This does fold in the reading types' synthetic
        # 60-min-per-completed-unit proxy (see statistics_day_builder.py), same as the
        # hero already does today.
        "primary": _pack_metric(
            _compute_metric_breakdown_for_range(
                _all_total_minutes / 60, start_date, end_date
            ),
            "Hours Logged",
            "Hours",
            "clock",
        ),
        "secondary": _pack_metric(
            _sum_breakdowns(
                [
                    tv_consumption["plays"],
                    anime_consumption["plays"],
                    movie_consumption["plays"],
                    music_consumption["plays"],
                    podcast_consumption["plays"],
                    video_consumption["plays"],
                ]
            ),
            "Total Plays",
            "Plays",
            "repeat",
        ),
        "bonuses": [b for b in [games_bonus, reading_bonus] if b],
        "has_data": _all_total_minutes > 0,
    }
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
        "status_distribution": status_distribution_payload,
        "status_pie_chart_data": status_pie_chart_data,
        "minutes_per_media_type": dict(minutes_by_type),
        "hours_per_media_type": hours_per_media_type,
        "tv_consumption": tv_consumption,
        "movie_consumption": movie_consumption,
        "anime_consumption": anime_consumption,
        "music_consumption": music_consumption,
        "podcast_consumption": podcast_consumption,
        "video_consumption": video_consumption,
        "game_consumption": game_consumption,
        "boardgame_consumption": boardgame_consumption,
        "book_consumption": book_consumption,
        "comic_consumption": comic_consumption,
        "manga_consumption": manga_consumption,
        "daily_hours_by_media_type": daily_hours_by_media_type,
        "history_highlights": history_highlights,
        "history_highlights_by_type": history_highlights_by_type,
        "summary_stats_by_type": summary_stats_by_type,
        "consumption_stats_by_type": consumption_stats_by_type,
        "weekday_hour_chart_data": weekday_hour_chart_data,
        "combined_plays_charts": combined_hours_charts,
    }
