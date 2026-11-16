"""statistics.py — Core statistics orchestration for Floppy.

The heavy lifting has been extracted into focused submodules:
  - stats_utils.py    shared pure utilities (parse_runtime_to_minutes, etc.)
  - stats_charts.py   Chart.js dataset builders
  - stats_activity.py timeline, activity calendar, streaks
  - stats_time.py     time-calculation pipeline
  - stats_score.py    score distribution & top-rated

Re-exports at the bottom of this file keep all callers that use
`from app import statistics as stats` fully transparent.
"""

import logging

from django.apps import apps
from django.db import models
from django.db.models import (
    Prefetch,
    Q,
)

from app import config
from app.metadata_utils import ANIME_SUPPLEMENT_GENRE, genre_list_has_name
from app.models import (
    TV,
    Episode,
    MediaTypes,
    Season,
    Status,
)
from app.templatetags import app_tags

logger = logging.getLogger(__name__)


def get_user_media(user, start_date, end_date):
    """Get all media items and their counts for a user within date range."""
    media_models = [
        apps.get_model(app_label="app", model_name=media_type)
        for media_type in user.get_active_media_types()
    ]
    user_media = {}
    media_count = {"total": 0}

    # Cache the base episodes query
    base_episodes = None
    _all_time = start_date is None and end_date is None
    if TV in media_models or Season in media_models:
        if _all_time:
            # No date filtering for "All Time"
            base_episodes = Episode.all_objects.filter(
                Episode.watch_q(),
                related_season__user=user,
            )
        else:
            base_episodes = Episode.all_objects.filter(
                Episode.watch_q(),
                related_season__user=user,
                end_date__range=(start_date, end_date),
            )

    # Every episode prefetch below also loads its item: season progress reads
    # each episode's number, and the legacy statistics API serializes that
    # progress for every season, so a bare prefetch costs one query per episode.
    _status_filter = [
        Status.IN_PROGRESS.value,
        Status.COMPLETED.value,
        Status.DROPPED.value,
        Status.PAUSED.value,
    ]

    _tv_ids = None  # saved for grouped-anime pass after the main loop
    _genre_anime_tv_ids = set()
    _split_tv_anime = not getattr(user, "anime_enabled", True) and getattr(
        user, "stats_split_tv_anime", False
    )

    for model in media_models:
        media_type = model.__name__.lower()
        queryset = None

        if model == TV:
            _tv_ids = base_episodes.values_list(
                "related_season__related_tv",
                flat=True,
            ).distinct()
            # "All Time" includes every show regardless of episode history
            # (e.g. a season completed via rating-only import with no
            # per-episode rows); date-ranged views still rely on episode
            # activity since TV/Season have no date fields of their own.
            base_tv_qs = (
                TV.objects.filter(user=user, status__in=_status_filter)
                if _all_time
                else TV.objects.filter(id__in=_tv_ids, status__in=_status_filter)
            )
            # Exclude grouped anime (library_media_type="anime") — they belong in the anime bucket
            queryset = base_tv_qs.exclude(
                item__library_media_type=MediaTypes.ANIME.value,
            )
            if _split_tv_anime:
                _genre_anime_tv_ids = {
                    tv_id
                    for tv_id, genres, library_media_type in queryset.values_list(
                        "id",
                        "item__genres",
                        "item__library_media_type",
                    )
                    if library_media_type != MediaTypes.ANIME.value
                    and genre_list_has_name(genres, ANIME_SUPPLEMENT_GENRE)
                }
                if _genre_anime_tv_ids:
                    queryset = queryset.exclude(id__in=_genre_anime_tv_ids)
            queryset = queryset.prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.filter(
                        status__in=[
                            Status.IN_PROGRESS.value,
                            Status.COMPLETED.value,
                            Status.DROPPED.value,
                            Status.PAUSED.value,
                        ],
                    )
                    .select_related(
                        "item",
                    )
                    .prefetch_related(
                        Prefetch(
                            "episodes",
                            queryset=base_episodes.filter(
                                related_season__related_tv__in=_tv_ids,
                            ).select_related("item"),
                        ),
                    ),
                ),
            )
        elif model == Season:
            season_ids = base_episodes.values_list(
                "related_season",
                flat=True,
            ).distinct()
            base_season_qs = (
                Season.objects.filter(user=user, status__in=_status_filter)
                if _all_time
                else Season.objects.filter(id__in=season_ids, status__in=_status_filter)
            )
            # Grouped anime seasons belong to the anime bucket, which is
            # counted separately below; without this they are counted twice.
            queryset = base_season_qs.exclude(
                related_tv__item__library_media_type=MediaTypes.ANIME.value,
            ).prefetch_related(
                Prefetch("episodes", queryset=base_episodes.select_related("item")),
            )
        # For other models, apply date filtering conditionally
        elif start_date is None and end_date is None:
            # No date filtering for "All Time"
            queryset = model.objects.filter(
                user=user,
                status__in=[
                    Status.IN_PROGRESS.value,
                    Status.COMPLETED.value,
                    Status.DROPPED.value,
                    Status.PAUSED.value,
                ],
            )
        else:
            queryset = model.objects.filter(
                user=user,
                status__in=[
                    Status.IN_PROGRESS.value,
                    Status.COMPLETED.value,
                    Status.DROPPED.value,
                    Status.PAUSED.value,
                ],
            ).filter(
                # Case 1: Media has both start_date and end_date
                # Include if ranges overlap
                # (exclude if media ends before filter start or starts after filter end)
                (
                    Q(start_date__isnull=False)
                    & Q(end_date__isnull=False)
                    & ~(Q(end_date__lt=start_date) | Q(start_date__gt=end_date))
                )
                |
                # Case 2: Media only has start_date (end_date is null)
                # Include if start_date is within filter range
                (
                    Q(start_date__isnull=False)
                    & Q(end_date__isnull=True)
                    & Q(start_date__gte=start_date)
                    & Q(start_date__lte=end_date)
                )
                |
                # Case 3: Media only has end_date (start_date is null)
                # Include if end_date is within filter range
                (
                    Q(start_date__isnull=True)
                    & Q(end_date__isnull=False)
                    & Q(end_date__gte=start_date)
                    & Q(end_date__lte=end_date)
                ),
            )

        queryset = queryset.select_related("item")
        user_media[media_type] = queryset
        count = queryset.count()
        media_count[media_type] = count
        media_count["total"] += count

    # Pull grouped anime (TV-structured, library_media_type="anime") into the anime bucket
    if _tv_ids is not None and base_episodes is not None:
        # Mirror the TV bucket's own base: "All Time" counts every show, even
        # one with no episode rows (a rating-only import). Keying this pass off
        # `_tv_ids` alone dropped those shows from TV *and* anime.
        _grouped_anime_episodes = (
            base_episodes
            if _all_time
            else base_episodes.filter(related_season__related_tv__in=_tv_ids)
        )
        _grouped_anime_qs = (
            (
                TV.objects.filter(user=user)
                if _all_time
                else TV.objects.filter(id__in=_tv_ids)
            )
            .filter(
                item__library_media_type=MediaTypes.ANIME.value,
                status__in=[
                    Status.IN_PROGRESS.value,
                    Status.COMPLETED.value,
                    Status.DROPPED.value,
                    Status.PAUSED.value,
                ],
            )
            .select_related("item")
            .prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.filter(
                        status__in=[
                            Status.IN_PROGRESS.value,
                            Status.COMPLETED.value,
                            Status.DROPPED.value,
                            Status.PAUSED.value,
                        ],
                    )
                    .select_related("item")
                    .prefetch_related(
                        Prefetch(
                            "episodes",
                            queryset=_grouped_anime_episodes.select_related("item"),
                        ),
                    ),
                ),
            )
        )
        _grouped_anime_count = _grouped_anime_qs.count()
        if _grouped_anime_count > 0:
            anime_key = MediaTypes.ANIME.value
            if anime_key in user_media:
                # Combine with flat anime (MAL) queryset already in the bucket
                user_media[anime_key] = _CombinedMediaBucket(
                    user_media[anime_key], _grouped_anime_qs
                )
                media_count[anime_key] = (
                    media_count.get(anime_key, 0) + _grouped_anime_count
                )
            else:
                user_media[anime_key] = _grouped_anime_qs
                media_count[anime_key] = _grouped_anime_count
            media_count["total"] += _grouped_anime_count

    # Pull TV shows tagged with Anime genre (via TVDB) into the anime bucket
    if _split_tv_anime and _genre_anime_tv_ids and base_episodes is not None:
        _genre_anime_qs = (
            TV.objects.filter(
                id__in=_genre_anime_tv_ids,
                status__in=[
                    Status.IN_PROGRESS.value,
                    Status.COMPLETED.value,
                    Status.DROPPED.value,
                    Status.PAUSED.value,
                ],
            )
            .select_related("item")
            .prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.filter(
                        status__in=[
                            Status.IN_PROGRESS.value,
                            Status.COMPLETED.value,
                            Status.DROPPED.value,
                            Status.PAUSED.value,
                        ],
                    )
                    .select_related("item")
                    .prefetch_related(
                        Prefetch(
                            "episodes",
                            queryset=base_episodes.filter(
                                related_season__related_tv__in=_tv_ids,
                            ).select_related("item"),
                        ),
                    ),
                ),
            )
        )
        _genre_anime_count = _genre_anime_qs.count()
        if _genre_anime_count > 0:
            anime_key = MediaTypes.ANIME.value
            if anime_key in user_media:
                user_media[anime_key] = _CombinedMediaBucket(
                    user_media[anime_key], _genre_anime_qs
                )
                media_count[anime_key] = (
                    media_count.get(anime_key, 0) + _genre_anime_count
                )
            else:
                user_media[anime_key] = _genre_anime_qs
                media_count[anime_key] = _genre_anime_count
            media_count["total"] += _genre_anime_count

    logger.info(
        "%s - Retrieved media %s",
        user,
        "for all time" if start_date is None else f"from {start_date} to {end_date}",
    )
    return user_media, media_count


def get_media_type_distribution(media_count, minutes_per_type=None):
    """Get data formatted for Chart.js pie chart."""
    # Define colors for each media type
    # Format for Chart.js
    chart_data = {
        "labels": [],
        "datasets": [
            {
                "data": [],
                "backgroundColor": [],
            },
        ],
    }

    dataset = chart_data["datasets"][0]

    if minutes_per_type is not None:
        dataset["value_label"] = "Hours"
        dataset["value_suffix"] = "h"
        dataset["value_decimals"] = 1

        ordered_types = list(MEDIA_TYPE_HOURS_ORDER)
        ordered_types.extend(
            [
                media_type
                for media_type in minutes_per_type
                if media_type not in ordered_types
            ],
        )

        for media_type in ordered_types:
            total_minutes = minutes_per_type.get(media_type, 0) or 0
            if total_minutes <= 0:
                continue
            hours = round(total_minutes / 60, 2)
            if hours <= 0:
                continue
            label = app_tags.media_type_readable(media_type)
            chart_data["labels"].append(label)
            dataset["data"].append(hours)
            dataset["backgroundColor"].append(
                config.get_stats_color(media_type),
            )
        return chart_data

    # Only include media types with counts > 0
    for media_type, count in media_count.items():
        if media_type != "total" and count > 0:
            # Format label with first letter capitalized
            label = app_tags.media_type_readable(media_type)
            chart_data["labels"].append(label)
            dataset["data"].append(count)
            dataset["backgroundColor"].append(
                config.get_stats_color(media_type),
            )
    return chart_data


def get_status_distribution(user_media):
    """Get status distribution for each media type within date range."""
    distribution = {}
    total_completed = 0
    # Define status order to ensure consistent stacking
    status_order = list(Status.values)
    for media_type, media_list in user_media.items():
        status_counts = dict.fromkeys(status_order, 0)
        counts = media_list.values("status").annotate(count=models.Count("id"))
        for count_data in counts:
            status_counts[count_data["status"]] = (
                status_counts.get(count_data["status"], 0) + count_data["count"]
            )
            if count_data["status"] == Status.COMPLETED.value:
                total_completed += count_data["count"]

        distribution[media_type] = status_counts

    # Format the response for charting
    return {
        "labels": [app_tags.media_type_readable(x) for x in distribution],
        "datasets": [
            {
                "label": status,
                "data": [
                    distribution[media_type][status] for media_type in distribution
                ],
                "background_color": get_status_color(status),
                "total": sum(
                    distribution[media_type][status] for media_type in distribution
                ),
            }
            for status in status_order
        ],
        "total_completed": total_completed,
    }


def get_status_pie_chart_data(status_distribution):
    """Get status distribution as a pie chart."""
    # Format for Chart.js pie chart
    chart_data = {
        "labels": [],
        "datasets": [
            {
                "data": [],
                "backgroundColor": [],
            },
        ],
    }

    # Process each status dataset
    for dataset in status_distribution["datasets"]:
        status_label = dataset["label"]
        status_count = dataset["total"]
        status_color = dataset["background_color"]

        # Only include statuses with counts > 0
        if status_count > 0:
            chart_data["labels"].append(status_label)
            chart_data["datasets"][0]["data"].append(status_count)
            chart_data["datasets"][0]["backgroundColor"].append(status_color)

    return chart_data


def get_status_color(status):
    """Get the color for the status of the media."""
    try:
        return config.get_status_stats_color(status)
    except KeyError:
        return "rgba(201, 203, 207)"


# ---------------------------------------------------------------------------
# Re-exports from extracted submodules — keeps all callers using
# `from app import statistics as stats` fully transparent.
# ---------------------------------------------------------------------------
from app.stats_activity import (  # noqa: E402,F401
    _convert_chart_to_day_minutes,
    calculate_day_of_week_stats,
    calculate_most_active_weekday,
    calculate_streak_details,
    calculate_streaks,
    get_activity_data,
    get_aligned_week_start,
    get_filtered_historical_data,
    get_level,
    get_timeline,
    time_line_sort_key,
)
from app.stats_charts import (  # noqa: E402,F401
    _build_completed_length_distribution_chart,
    _build_media_charts,
    _build_release_year_chart,
    _build_single_series_chart,
    _compute_metric_breakdown,
    _format_hour_label,
)
from app.stats_daily_hours import get_daily_hours_by_media_type  # noqa: E402,F401
from app.stats_game import (  # noqa: E402,F401
    DAILY_AVERAGE_BANDS,
    _build_daily_average_band_top_games,
    _build_daily_average_distribution_chart,
    _build_game_hours_charts,
    _collect_game_data,
    _collect_game_play_data,
    _compute_game_platform_breakdown,
    _compute_game_top_daily_average,
    _compute_game_top_genres,
    _game_entry_in_range,
    _get_daily_average_band_index,
    get_game_consumption_stats,
)
from app.stats_music import (  # noqa: E402,F401
    COUNTRY_NAME_MAP,
    _collect_music_play_data,
    _compute_music_top_lists,
    _compute_music_top_rollups,
    _country_name_from_code,
    _hydrate_music_metadata_for_rollups,
    _parse_release_date_str,
    get_music_consumption_stats,
)
from app.stats_podcast import (  # noqa: E402,F401
    _collect_podcast_play_data,
    _compute_podcast_top_lists,
    _get_podcast_history_data,
    _get_podcast_runtime_minutes,
    get_podcast_consumption_stats,
)
from app.stats_reading import (  # noqa: E402,F401
    _build_reading_top_authors,
    _build_weighted_media_charts,
    _extract_cached_item_authors,
    _extract_item_authors,
    _fetch_reading_items_with_authors,
    _format_reading_unit,
    _normalize_item_author_names,
    _reading_entry_in_range,
    _reading_entry_range_filter,
    get_reading_consumption_stats,
)
from app.stats_score import (  # noqa: E402,F401
    _annotate_top_rated_media,
    get_score_distribution,
)
from app.stats_time import (  # noqa: E402,F401
    _calculate_anime_time,
    _calculate_episode_time_from_cache,
    _calculate_episode_time_from_data,
    _calculate_game_time_in_range,
    _calculate_movie_time,
    _calculate_music_time,
    _calculate_tv_time,
    _get_anime_runtime_from_cache,
    _get_media_metadata_for_statistics,
    _get_media_runtime_from_cache,
    _get_music_runtime_minutes,
    _get_season_metadata,
    _get_season_metadata_with_episodes,
    _is_episode_in_range,
    calculate_minutes_per_media_type,
    get_hours_per_media_type,
)
from app.stats_top_played import get_top_played_media  # noqa: E402,F401
from app.stats_utils import (  # noqa: E402,F401
    MEDIA_TYPE_HOURS_ORDER,
    _coerce_genre_list,
    _CombinedMediaBucket,
    _CombinedValuesResult,
    _format_hours_minutes,
    _format_long_units,
    _get_activity_datetime,
    _get_entry_play_dates,
    _infer_user_from_user_media,
    _is_media_in_date_range,
    _iter_media_list,
    _localize_datetime,
    parse_runtime_to_minutes,
)
from app.stats_video import (  # noqa: E402,F401
    _collect_episode_datetimes,
    _collect_movie_datetimes,
    _collect_movie_play_data,
    _collect_tv_play_data,
    _compute_movie_tv_top_genres,
    get_anime_consumption_stats,
    get_movie_consumption_stats,
    get_tv_consumption_stats,
)
