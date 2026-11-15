"""stats_score.py — Score distribution and top-rated media aggregation.

Extracted from statistics.py.  Self-contained: only depends on the ORM,
heapq, and stats_utils helpers.
"""

import heapq
import itertools
from collections import defaultdict

from app import config
from app.history_cache_utils import (
    HISTORY_UNREAD_ITEM_FIELDS,
    history_deferred_item_fields,
)
from app.models import TV, MediaManager, MediaTypes, Season, Sources
from app.statistics_cache import STATISTICS_TOP_N, STATISTICS_TOP_RATED_OVERALL
from app.stats_utils import _CombinedMediaBucket, _infer_user_from_user_media
from app.templatetags import app_tags

SCORE_SCALE_FIVE_POINT = 5


def _prefetch_activity_dates(queryset, model_cls):
    """Load what a TV show's or season's activity dates read, in a few queries.

    A TV show's and a season's ``end_date`` and ``progressed_at`` are derived
    from their seasons and episodes. Without this every scored show or season
    queries for them separately.
    """
    model = model_cls._meta.concrete_model
    if model is TV:
        return queryset.prefetch_related("seasons__item", "seasons__episodes")
    if model is Season:
        return queryset.prefetch_related("episodes")
    return queryset


def get_score_distribution(user_media):
    """Get score distribution for each media type within date range."""
    distribution = {}
    total_scored = 0
    total_score_sum = 0

    # Global top rated (for backward compatibility with existing "ALL MEDIA" section)
    top_rated = []
    top_rated_count = STATISTICS_TOP_RATED_OVERALL
    # Per-media-type top rated (for the new compact cards)
    top_rated_by_type = {}
    top_rated_per_type_count = STATISTICS_TOP_N

    counter = itertools.count()  # Ensures stable sorting for equal scores

    # Infer user from user_media for fetching all entries
    user = _infer_user_from_user_media(user_media)
    score_scale_max = user.rating_scale_max if user else 10
    score_range = range(score_scale_max + 1)

    for media_type, media_list in user_media.items():
        score_counts = dict.fromkeys(score_range, 0)
        # _all_qs: list of individual querysets; handles combined anime bucket (flat + grouped TV)
        _all_qs = (
            list(media_list._querysets)
            if isinstance(media_list, _CombinedMediaBucket)
            else [media_list]
        )

        # Group media by item to identify which items appear in the date range
        # One pass over each queryset itself, not a clone: an evaluation
        # re-runs its prefetches (a TV bucket loads every season and episode),
        # and the timeline and other callers then reuse the loaded rows.
        # get_user_media already selects each row's item.
        media_by_item = defaultdict(list)
        # Model class per item; handles grouped anime (TV) in the anime bucket.
        _model_classes_by_item_id = {}
        for _qs in _all_qs:
            for media in _qs:
                item = getattr(media, "item", None)
                key = item.id if item else media.id
                media_by_item[key].append(media)
                if item:
                    _model_classes_by_item_id[item.id] = type(media)

        # For each item that appears in the date range, fetch ALL entries (not just date-filtered)
        # to find the aggregated score, even if the score was set outside the date range
        deduped_scored = {}
        if user:
            # Get all unique item IDs from items that appear in the date range
            item_ids_in_range = set()
            item_id_to_key_map = {}  # Map item.id -> key used in media_by_item
            for key, entries in media_by_item.items():
                for entry in entries:
                    item = getattr(entry, "item", None)
                    if item:
                        item_ids_in_range.add(item.id)
                        item_id_to_key_map[item.id] = key

            # Fetch ALL entries for these items (not just date-filtered ones),
            # querying each model class separately in case the bucket is mixed (flat + grouped anime)
            if item_ids_in_range:
                _ids_by_model = defaultdict(set)
                for item_id, model_cls in _model_classes_by_item_id.items():
                    if item_id in item_ids_in_range:
                        _ids_by_model[model_cls].add(item_id)

                all_entries_by_item_id: dict = defaultdict(list)
                for model_cls, item_ids_for_model in _ids_by_model.items():
                    _entries_q = (
                        model_cls.objects.filter(
                            user=user,
                            item_id__in=item_ids_for_model,
                        )
                        .select_related("item")
                        .order_by("-created_at")
                    )
                    _entries_q = _prefetch_activity_dates(_entries_q, model_cls)
                    for entry in _entries_q:
                        _item = getattr(entry, "item", None)
                        if _item:
                            all_entries_by_item_id[_item.id].append(entry)

                # Now aggregate scores from ALL entries (not just date-filtered ones)
                for item_id in item_ids_in_range:
                    # Get the key used in media_by_item for this item
                    key = item_id_to_key_map.get(item_id)
                    if key is None:
                        continue

                    # Use entries from date range as display media (for activity date calculation)
                    display_entries = media_by_item.get(key, [])
                    if not display_entries:
                        continue

                    # Use ALL entries to find aggregated score
                    all_entries = all_entries_by_item_id.get(item_id, [])
                    if not all_entries:
                        continue

                    display_media = display_entries[
                        0
                    ]  # Use first entry from date range as display

                    # Aggregate score from ALL entries (regardless of date)
                    latest_rating = None
                    latest_activity = None

                    for entry in all_entries:
                        if entry.score is not None:
                            # Determine the most recent activity for this entry
                            entry_activity = None
                            if entry.end_date:
                                entry_activity = entry.end_date
                            elif entry.progressed_at:
                                entry_activity = entry.progressed_at
                            else:
                                entry_activity = entry.created_at

                            # If this entry has more recent activity, use its rating
                            if (
                                latest_activity is None
                                or entry_activity > latest_activity
                            ):
                                latest_activity = entry_activity
                                latest_rating = entry.score

                    score_to_use = latest_rating
                    # Set aggregated_score for consistency with other code paths
                    if score_to_use is not None:
                        display_media.aggregated_score = score_to_use

                    # Only include if there's a score
                    if score_to_use is not None:
                        dates = [
                            d
                            for d in (display_media.end_date, display_media.start_date)
                            if d
                        ]
                        activity_date = (
                            max(dates) if dates else display_media.created_at
                        )
                        deduped_scored[key] = {
                            "media": display_media,
                            "activity_date": activity_date,
                            "score": score_to_use,
                        }
            else:
                # Fallback: no items with item_id, use original logic
                for item_id, entries in media_by_item.items():
                    if len(entries) == 1:
                        # Single entry - use it directly
                        media = entries[0]
                        score_to_use = media.score
                        display_media = media
                    else:
                        # Multiple entries - aggregate to find most recent score
                        display_media = entries[0]  # Use first entry as display
                        latest_rating = None
                        latest_activity = None

                        for entry in entries:
                            if entry.score is not None:
                                # Determine the most recent activity for this entry
                                entry_activity = None
                                if entry.end_date:
                                    entry_activity = entry.end_date
                                elif entry.progressed_at:
                                    entry_activity = entry.progressed_at
                                else:
                                    entry_activity = entry.created_at

                                # If this entry has more recent activity, use its rating
                                if (
                                    latest_activity is None
                                    or entry_activity > latest_activity
                                ):
                                    latest_activity = entry_activity
                                    latest_rating = entry.score

                        score_to_use = latest_rating
                        # Set aggregated_score for consistency with other code paths
                        if score_to_use is not None:
                            display_media.aggregated_score = score_to_use

                    # Only include if there's a score
                    if score_to_use is not None:
                        dates = [
                            d
                            for d in (display_media.end_date, display_media.start_date)
                            if d
                        ]
                        activity_date = (
                            max(dates) if dates else display_media.created_at
                        )
                        deduped_scored[item_id] = {
                            "media": display_media,
                            "activity_date": activity_date,
                            "score": score_to_use,
                        }
        else:
            # Fallback: no user available, use original logic
            for item_id, entries in media_by_item.items():
                if len(entries) == 1:
                    # Single entry - use it directly
                    media = entries[0]
                    score_to_use = media.score
                    display_media = media
                else:
                    # Multiple entries - aggregate to find most recent score
                    display_media = entries[0]  # Use first entry as display
                    latest_rating = None
                    latest_activity = None

                    for entry in entries:
                        if entry.score is not None:
                            # Determine the most recent activity for this entry
                            entry_activity = None
                            if entry.end_date:
                                entry_activity = entry.end_date
                            elif entry.progressed_at:
                                entry_activity = entry.progressed_at
                            else:
                                entry_activity = entry.created_at

                            # If this entry has more recent activity, use its rating
                            if (
                                latest_activity is None
                                or entry_activity > latest_activity
                            ):
                                latest_activity = entry_activity
                                latest_rating = entry.score

                    score_to_use = latest_rating
                    # Set aggregated_score for consistency with other code paths
                    if score_to_use is not None:
                        display_media.aggregated_score = score_to_use

                # Only include if there's a score
                if score_to_use is not None:
                    dates = [
                        d
                        for d in (display_media.end_date, display_media.start_date)
                        if d
                    ]
                    activity_date = max(dates) if dates else display_media.created_at
                    deduped_scored[item_id] = {
                        "media": display_media,
                        "activity_date": activity_date,
                        "score": score_to_use,
                    }

        deduped_media = [entry["media"] for entry in deduped_scored.values()]  # noqa: F841 (kept for symmetry)

        # Initialize per-type heap for this media type
        type_top_rated = []
        type_counter = itertools.count()

        for entry_data in deduped_scored.values():
            media = entry_data["media"]
            score_value = entry_data["score"]
            score_value_scaled = float(score_value)
            if score_scale_max == SCORE_SCALE_FIVE_POINT:
                score_value_scaled = score_value_scaled / 2

            # Add to global top rated (for backward compatibility)
            if len(top_rated) < top_rated_count:
                heapq.heappush(
                    top_rated,
                    (float(score_value), next(counter), media),
                )
            else:
                heapq.heappushpop(
                    top_rated,
                    (float(score_value), next(counter), media),
                )

            # Add to per-type top rated
            if len(type_top_rated) < top_rated_per_type_count:
                heapq.heappush(
                    type_top_rated,
                    (float(score_value), next(type_counter), media),
                )
            else:
                heapq.heappushpop(
                    type_top_rated,
                    (float(score_value), next(type_counter), media),
                )

            binned_score = int(score_value_scaled)
            binned_score = min(binned_score, score_scale_max)
            score_counts[binned_score] += 1
            total_scored += 1
            total_score_sum += score_value_scaled

        distribution[media_type] = score_counts

        # Sort and annotate per-type top rated
        type_top_rated_sorted = [
            media for _, _, media in sorted(type_top_rated, key=lambda x: (-x[0], x[1]))
        ]
        top_rated_by_type[media_type] = _annotate_top_rated_media(type_top_rated_sorted)

    average_score = (
        round(total_score_sum / total_scored, 2) if total_scored > 0 else None
    )

    top_rated_media = [
        media for _, _, media in sorted(top_rated, key=lambda x: (-x[0], x[1]))
    ]

    top_rated_media = _annotate_top_rated_media(top_rated_media)

    return (
        {
            "labels": [str(score) for score in score_range],
            "datasets": [
                {
                    "label": app_tags.media_type_readable(media_type),
                    "data": [distribution[media_type][score] for score in score_range],
                    "background_color": config.get_stats_color(media_type),
                }
                for media_type in distribution
            ],
            "average_score": average_score,
            "total_scored": total_scored,
            "scale_max": score_scale_max,
        },
        top_rated_media,
        top_rated_by_type,
    )


# annotate_max_progress reads a manual item's custom details for these types
# (manual_metadata, then item columns such as authors or studios), so a manual
# item of these types is loaded whole rather than one late query per column.
_MANUAL_DETAIL_PROGRESS_TYPES = frozenset(
    {MediaTypes.ANIME.value, MediaTypes.MANGA.value, MediaTypes.COMIC.value}
)


def _annotate_top_rated_media(top_rated_media):
    """Apply prefetch_related and annotate max_progress for top rated media."""
    if not top_rated_media:
        return top_rated_media

    # The item type can differ from the table containing the tracking row.
    media_by_model = {}
    for media in top_rated_media:
        media_by_model.setdefault(media._meta.concrete_model, []).append(media)

    media_manager = MediaManager()

    replacements = {}
    for model, media_list in media_by_model.items():
        media_type = model._meta.model_name
        media_ids = [media.id for media in media_list]

        # Fetch fresh instances with proper relationships and annotations.
        # The cached range payload keeps these rows and the season and episode
        # items prefetched under them, so the wide item columns no card reads
        # stay out.
        item_paths = ["item"]
        if media_type == MediaTypes.SEASON.value:
            item_paths.append("related_tv__item")
        whole_ids = set()
        if media_type in _MANUAL_DETAIL_PROGRESS_TYPES:
            whole_ids = {
                media.id
                for media in media_list
                if media.item.source == Sources.MANUAL.value
            }
        rows = []
        for ids, deferred in (
            (set(media_ids) - whole_ids, history_deferred_item_fields(*item_paths)),
            (whole_ids, ()),
        ):
            if not ids:
                continue
            queryset = (
                model.objects.filter(id__in=ids)
                .select_related(*item_paths)
                .defer(*deferred)
            )
            rows.extend(
                media_manager._apply_prefetch_related(
                    queryset, media_type, defer_item_fields=HISTORY_UNREAD_ITEM_FIELDS
                )
            )
        media_manager.annotate_max_progress(rows, media_type)

        prefetched_media_map = {media.id: media for media in rows}

        # Replace original instances with enhanced ones, keeping any route
        # override (e.g. grouped anime fetched as a TV row) that was set on
        # the original instance so links keep using the anime route.
        for media in media_list:
            replacement = prefetched_media_map.get(media.id)
            if replacement is None:
                continue
            route_media_type = getattr(media, "route_media_type", None) or (
                getattr(media.item, "route_media_type", None)
            )
            if route_media_type:
                replacement.route_media_type = route_media_type
                replacement.item.route_media_type = route_media_type
            if hasattr(media, "aggregated_score"):
                replacement.aggregated_score = media.aggregated_score
            replacements[(model, media.pk)] = replacement

    return [
        replacements[key]
        for media in top_rated_media
        if (key := (media._meta.concrete_model, media.pk)) in replacements
    ]
