import calendar
import datetime
import heapq
import itertools
import logging
from collections import defaultdict

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db import models
from django.db.models import (
    Prefetch,
    Q,
)
from django.utils import timezone

from app import media_type_config, providers
from app.models import TV, BasicMedia, Episode, MediaManager, MediaTypes, Season, Status
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
    if TV in media_models or Season in media_models:
        if start_date is None and end_date is None:
            # No date filtering for "All Time"
            base_episodes = Episode.objects.filter(
                related_season__user=user,
            )
        else:
            base_episodes = Episode.objects.filter(
                related_season__user=user,
                end_date__range=(start_date, end_date),
            )

    for model in media_models:
        media_type = model.__name__.lower()
        queryset = None

        if model == TV:
            tv_ids = base_episodes.values_list(
                "related_season__related_tv",
                flat=True,
            ).distinct()
            queryset = TV.objects.filter(id__in=tv_ids).prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.select_related(
                        "item",
                    ).prefetch_related(
                        Prefetch(
                            "episodes",
                            queryset=base_episodes.filter(
                                related_season__related_tv__in=tv_ids,
                            ),
                        ),
                    ),
                ),
            )
        elif model == Season:
            season_ids = base_episodes.values_list(
                "related_season",
                flat=True,
            ).distinct()
            queryset = Season.objects.filter(
                id__in=season_ids,
            ).prefetch_related(
                Prefetch("episodes", queryset=base_episodes),
            )
        # For other models, apply date filtering conditionally
        elif start_date is None and end_date is None:
            # No date filtering for "All Time"
            queryset = model.objects.filter(user=user)
        else:
            queryset = model.objects.filter(user=user).filter(
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

    logger.info(
        "%s - Retrieved media %s",
        user,
        "for all time" if start_date is None else f"from {start_date} to {end_date}",
    )
    return user_media, media_count


def get_media_type_distribution(media_count):
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

    # Only include media types with counts > 0
    for media_type, count in media_count.items():
        if media_type != "total" and count > 0:
            # Format label with first letter capitalized
            label = app_tags.media_type_readable(media_type)
            chart_data["labels"].append(label)
            chart_data["datasets"][0]["data"].append(count)
            chart_data["datasets"][0]["backgroundColor"].append(
                media_type_config.get_stats_color(media_type),
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
            status_counts[count_data["status"]] = count_data["count"]
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


def get_score_distribution(user_media):
    """Get score distribution for each media type within date range."""
    distribution = {}
    total_scored = 0
    total_score_sum = 0

    top_rated = []
    top_rated_count = 14
    counter = itertools.count()  # Ensures stable sorting for equal scores
    score_range = range(11)

    for media_type, media_list in user_media.items():
        score_counts = dict.fromkeys(score_range, 0)
        scored_media = media_list.exclude(score__isnull=True).select_related("item")

        for media in scored_media:
            if len(top_rated) < top_rated_count:
                heapq.heappush(
                    top_rated,
                    (float(media.score), next(counter), media),
                )
            else:
                heapq.heappushpop(
                    top_rated,
                    (float(media.score), next(counter), media),
                )

            binned_score = int(media.score)
            score_counts[binned_score] += 1
            total_scored += 1
            total_score_sum += media.score

        distribution[media_type] = score_counts

    average_score = (
        round(total_score_sum / total_scored, 2) if total_scored > 0 else None
    )

    top_rated_media = [
        media for _, _, media in sorted(top_rated, key=lambda x: (-x[0], x[1]))
    ]

    top_rated_media = _annotate_top_rated_media(top_rated_media)

    return {
        "labels": [str(score) for score in score_range],
        "datasets": [
            {
                "label": app_tags.media_type_readable(media_type),
                "data": [distribution[media_type][score] for score in score_range],
                "background_color": media_type_config.get_stats_color(media_type),
            }
            for media_type in distribution
        ],
        "average_score": average_score,
        "total_scored": total_scored,
    }, top_rated_media


def _annotate_top_rated_media(top_rated_media):
    """Apply prefetch_related and annotate max_progress for top rated media."""
    if not top_rated_media:
        return top_rated_media

    # Group by media type to batch database operations
    media_by_type = {}
    for media in top_rated_media:
        media_type = media.item.media_type
        if media_type not in media_by_type:
            media_by_type[media_type] = []
        media_by_type[media_type].append(media)

    media_manager = MediaManager()

    for media_type, media_list in media_by_type.items():
        model = apps.get_model(app_label="app", model_name=media_type)
        media_ids = [media.id for media in media_list]

        # Fetch fresh instances with proper relationships and annotations
        queryset = model.objects.filter(id__in=media_ids)
        queryset = media_manager._apply_prefetch_related(queryset, media_type)
        media_manager.annotate_max_progress(queryset, media_type)

        prefetched_media_map = {media.id: media for media in queryset}

        # Replace original instances with enhanced ones
        for i, media in enumerate(top_rated_media):
            if media.item.media_type == media_type:
                top_rated_media[i] = prefetched_media_map[media.id]

    return top_rated_media


def get_status_color(status):
    """Get the color for the status of the media."""
    colors = {
        Status.IN_PROGRESS.value: media_type_config.get_stats_color(
            MediaTypes.EPISODE.value,
        ),
        Status.COMPLETED.value: media_type_config.get_stats_color(
            MediaTypes.TV.value,
        ),
        Status.PLANNING.value: media_type_config.get_stats_color(
            MediaTypes.ANIME.value,
        ),
        Status.PAUSED.value: media_type_config.get_stats_color(
            MediaTypes.MOVIE.value,
        ),
        Status.DROPPED.value: media_type_config.get_stats_color(
            MediaTypes.MANGA.value,
        ),
    }
    return colors.get(status, "rgba(201, 203, 207)")


def get_timeline(user_media):
    """Build a timeline of media consumption organized by month-year."""
    timeline = defaultdict(list)

    # Process each media type
    for media_type, queryset in user_media.items():
        if media_type == MediaTypes.TV.value:
            continue
        for media in queryset:
            local_start_date = timezone.localdate(media.start_date)
            local_end_date = timezone.localdate(media.end_date)

            if media.start_date and media.end_date:
                # add media to all months between start and end
                current_date = local_start_date
                while current_date <= local_end_date:
                    year = current_date.year
                    month = current_date.month
                    month_name = calendar.month_name[month]
                    month_year = f"{month_name} {year}"

                    timeline[month_year].append(media)

                    # Move to next month
                    current_date += relativedelta(months=1)
                    current_date = current_date.replace(day=1)
            elif media.start_date:
                # If only start date, add to the start month
                year = local_start_date.year
                month = local_start_date.month
                month_name = calendar.month_name[month]
                month_year = f"{month_name} {year}"

                timeline[month_year].append(media)
            elif media.end_date:
                # If only end date, add to the end month
                year = local_end_date.year
                month = local_end_date.month
                month_name = calendar.month_name[month]
                month_year = f"{month_name} {year}"

                timeline[month_year].append(media)

    # Convert to sorted dictionary with media sorted by start date
    # Create a list sorted by year and month in reverse order
    sorted_items = []
    for month_year, media_list in timeline.items():
        month_name, year_str = month_year.split()
        year = int(year_str)
        month = list(calendar.month_name).index(month_name)
        sorted_items.append((month_year, media_list, year, month))

    # Sort by year and month in reverse chronological order
    sorted_items.sort(key=lambda x: (x[2], x[3]), reverse=True)

    # Create the final result dictionary
    result = {}
    for month_year, media_list, _, _ in sorted_items:
        # Sort the media list using our custom sort key
        result[month_year] = sorted(media_list, key=time_line_sort_key, reverse=True)
    return result


def time_line_sort_key(media):
    """Sort media items in the timeline."""
    if media.end_date is not None:
        return timezone.localdate(media.end_date)
    return timezone.localdate(media.start_date)


def get_activity_data(user, start_date, end_date):
    """Get daily activity counts for the last year."""
    if end_date is None:
        end_date = timezone.localtime()

    start_date_aligned = get_aligned_monday(start_date)

    combined_data = get_filtered_historical_data(start_date_aligned, end_date, user)

    # update start_date values from historical records if not provided
    if start_date is None:
        dates = [item["date"] for item in combined_data]
        start_date = datetime.datetime.combine(
            min(dates) if dates else timezone.localdate(),
            datetime.time.min,
        )
        start_date_aligned = get_aligned_monday(start_date)

    # Aggregate counts by date
    date_counts = {}
    for item in combined_data:
        date = item["date"]
        date_counts[date] = date_counts.get(date, 0) + item["count"]

    date_range = [
        start_date_aligned.date() + datetime.timedelta(days=x)
        for x in range((end_date.date() - start_date_aligned.date()).days + 1)
    ]

    # Calculate activity statistics
    most_active_day, day_percentage = calculate_day_of_week_stats(
        date_counts,
        start_date.date(),
    )
    current_streak, longest_streak = calculate_streaks(
        date_counts,
        end_date.date(),
    )

    # Create complete date range including padding days
    activity_data = [
        {
            "date": current_date.strftime("%Y-%m-%d"),
            "count": date_counts.get(current_date, 0),
            "level": get_level(date_counts.get(current_date, 0)),
        }
        for current_date in date_range
    ]

    # Format data into calendar weeks
    calendar_weeks = [activity_data[i : i + 7] for i in range(0, len(activity_data), 7)]

    # Generate months list with their Monday counts
    months = []
    mondays_per_month = []
    current_month = date_range[0].strftime("%b")
    monday_count = 0

    for current_date in date_range:
        if current_date.weekday() == 0:  # Monday
            month = current_date.strftime("%b")

            if current_month != month:
                if current_month is not None:
                    if monday_count > 1:
                        months.append(current_month)
                        mondays_per_month.append(monday_count)
                    else:
                        months.append("")
                        mondays_per_month.append(monday_count)
                current_month = month
                monday_count = 0

            monday_count += 1
    # For the last month
    if monday_count > 1:
        months.append(current_month)
        mondays_per_month.append(monday_count)

    return {
        "calendar_weeks": calendar_weeks,
        "months": list(zip(months, mondays_per_month, strict=False)),
        "stats": {
            "most_active_day": most_active_day,
            "most_active_day_percentage": day_percentage,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        },
    }


def get_aligned_monday(datetime_obj):
    """Get the Monday of the week containing the given date."""
    if datetime_obj is None:
        return None

    days_to_subtract = datetime_obj.weekday()  # 0=Monday, 6=Sunday
    return datetime_obj - datetime.timedelta(days=days_to_subtract)


def get_level(count):
    """Calculate intensity level (0-4) based on count."""
    thresholds = [0, 3, 6, 9]
    for i, threshold in enumerate(thresholds):
        if count <= threshold:
            return i
    return 4


def get_filtered_historical_data(start_date, end_date, user):
    """Return [{"date": datetime.date, "count": int}]."""
    historical_models = BasicMedia.objects.get_historical_models()
    local_tz = timezone.get_current_timezone()

    day_buckets = defaultdict(int)

    for model_name in historical_models:
        model = apps.get_model("app", model_name)

        qs = model.objects.filter(history_user_id=user)

        if start_date:
            qs = qs.filter(history_date__gte=start_date)
        if end_date:
            qs = qs.filter(history_date__lte=end_date)

        # We only need the timestamp, stream results to keep memory usage flat
        for ts in qs.values_list("history_date", flat=True).iterator(chunk_size=2_000):
            aware_ts = timezone.localtime(ts, local_tz)

            day_buckets[aware_ts.date()] += 1

    combined_data = [
        {"date": day, "count": count} for day, count in day_buckets.items()
    ]

    logger.info("%s - built historical data (%s rows)", user, len(combined_data))
    return combined_data


def calculate_day_of_week_stats(date_counts, start_date):
    """Calculate the most active day of the week based on activity frequency.

    Returns the day name and its percentage of total activity.
    """
    # Initialize counters for each day of the week
    day_counts = defaultdict(int)
    total_active_days = 0

    # Count occurrences of each day of the week where activity happened
    for date in date_counts:
        if date < start_date:
            continue
        if date_counts[date] > 0:
            day_name = date.strftime("%A")  # Get full day name
            day_counts[day_name] += 1
            total_active_days += 1

    if not total_active_days:
        return None, 0

    # Find the most active day
    most_active_day = max(day_counts.items(), key=lambda x: x[1])
    percentage = (most_active_day[1] / total_active_days) * 100

    return most_active_day[0], round(percentage)


def calculate_streaks(date_counts, end_date):
    """Calculate current and longest activity streaks."""
    # Get active dates and sort them in descending order (newest first)
    active_dates = sorted(
        [date for date, count in date_counts.items() if count > 0],
        reverse=True,
    )

    if not active_dates:
        return 0, 0

    longest_streak = 1
    streak_count = 1

    # Check if the most recent active date is today/end_date
    is_current = active_dates[0] == end_date

    current_streak = 1 if is_current else 0

    for i in range(1, len(active_dates)):
        # Check if this date is consecutive with the previous one
        if (active_dates[i - 1] - active_dates[i]).days == 1:
            streak_count += 1

            if is_current:
                current_streak += 1
        else:
            longest_streak = max(longest_streak, streak_count)
            streak_count = 1

            if is_current:
                is_current = False

    # Check final streak for longest calculation
    # needed if the last date is today/end_date
    longest_streak = max(longest_streak, streak_count)

    return current_streak, longest_streak


def parse_runtime_to_minutes(runtime_str):
    """Parse runtime string (e.g., '45m', '1h 30m', '2h', '12 min') to total minutes."""
    if not runtime_str:
        return None
    
    try:
        # Handle MAL format: "12 min" (note the space before "min")
        if "h" in runtime_str and "min" in runtime_str:
            # Format like "1h 30min" or "2h 15min"
            parts = runtime_str.split()
            if len(parts) == 2:  # "1h 30min"
                hours = int(parts[0].replace("h", ""))
                minutes = int(parts[1].replace("min", ""))
                return hours * 60 + minutes
            else:
                return None
        elif "h" in runtime_str and "m" in runtime_str:
            # Format like "1h 30m" or "2h 15m" (TMDB format)
            parts = runtime_str.split()
            if len(parts) == 2:  # "1h 30m"
                hours = int(parts[0].replace("h", ""))
                minutes = int(parts[1].replace("m", ""))
                return hours * 60 + minutes
            else:
                return None
        elif "h" in runtime_str:
            # Format like "2h"
            hours = int(runtime_str.replace("h", ""))
            return hours * 60
        elif "min" in runtime_str:
            # Format like "45min" or "12 min" (MAL format)
            minutes = int(runtime_str.replace("min", "").replace(" ", ""))
            return minutes
        elif "m" in runtime_str:
            # Format like "45m" (TMDB format)
            minutes = int(runtime_str.replace("m", ""))
            return minutes
        else:
            return None
    except (ValueError, AttributeError):
        return None


def _is_media_in_date_range(media, start_date, end_date):
    """Check if media is within the specified date range."""
    if not start_date or not end_date:
        return True
    
    if hasattr(media, 'end_date') and media.end_date:
        return start_date <= media.end_date <= end_date
    elif hasattr(media, 'start_date') and media.start_date:
        return start_date <= media.start_date <= end_date
    
    return False


def _calculate_tv_hours(media, total_minutes):
    """Calculate hours for TV shows."""
    try:
        if hasattr(media, 'seasons'):
            for season in media.seasons.all():
                if hasattr(season, 'episodes'):
                    episode_count = season.episodes.count()
                    # Get season runtime from metadata
                    try:
                        season_metadata = providers.services.get_media_metadata(
                            'tv',
                            media.item.media_id,
                            media.item.source,
                            [season.item.season_number]
                        )
                        if season_metadata and season_metadata.get("details", {}).get("runtime"):
                            runtime_str = season_metadata["details"]["runtime"]
                            episode_minutes = parse_runtime_to_minutes(runtime_str)
                            if episode_minutes:
                                total_minutes += episode_count * episode_minutes
                            else:
                                # Fallback: assume 45 minutes per episode
                                total_minutes += episode_count * 45
                        else:
                            # Fallback: assume 45 minutes per episode
                            total_minutes += episode_count * 45
                    except Exception:
                        # Fallback: assume 45 minutes per episode
                        total_minutes += episode_count * 45
    except Exception:
        # Fallback: assume 45 minutes per episode
        total_minutes += 1 * 45
    
    return total_minutes


def _calculate_movie_hours(media, total_minutes):
    """Calculate hours for movies."""
    try:
        media_metadata = providers.services.get_media_metadata(
            'movie',
            media.item.media_id,
            media.item.source,
        )
        if media_metadata and media_metadata.get("details", {}).get("runtime"):
            runtime_str = media_metadata["details"]["runtime"]
            movie_minutes = parse_runtime_to_minutes(runtime_str)
            if movie_minutes:
                total_minutes += movie_minutes
            else:
                # Fallback: assume 120 minutes per movie
                total_minutes += 120
        else:
            # Fallback: assume 120 minutes per movie
            total_minutes += 120
    except Exception:
        # Fallback: assume 120 minutes per movie
        total_minutes += 120
    
    return total_minutes


def _calculate_anime_hours(media, total_minutes):
    """Calculate hours for anime."""
    try:
        anime_metadata = providers.services.get_media_metadata(
            'anime',
            media.item.media_id,
            media.item.source,
        )
        
        if anime_metadata and anime_metadata.get("details", {}).get("runtime"):
            runtime_str = anime_metadata["details"]["runtime"]
            episode_minutes = parse_runtime_to_minutes(runtime_str)
            if episode_minutes:
                # Use the progress field which tracks episodes watched
                episode_count = getattr(media, 'progress', 1)
                total_minutes += episode_count * episode_minutes
            else:
                # Fallback: assume 24 minutes per episode
                episode_count = getattr(media, 'progress', 1)
                total_minutes += episode_count * 24
        else:
            # Fallback: assume 24 minutes per episode
            episode_count = getattr(media, 'progress', 1)
            total_minutes += episode_count * 24
    except Exception as e:
        # Fallback: assume 24 minutes per episode
        episode_count = getattr(media, 'progress', 1)
        total_minutes += episode_count * 24
    
    return total_minutes


def _format_hours_minutes(total_minutes):
    """Format total minutes into hours and minutes string."""
    if total_minutes > 0:
        hours = total_minutes // 60
        remaining_minutes = total_minutes % 60
        
        # Always show both hours and minutes for consistency
        return f"{hours}h {remaining_minutes}min"
    else:
        return "0h 0min"


def get_hours_per_media_type(user_media, start_date, end_date):
    """Calculate total hours watched per media type within the date range."""
    hours_per_type = {}
    
    for media_type, media_list in user_media.items():
        total_minutes = 0
        
        for media_data in media_list:
            if hasattr(media_data, 'media'):
                media = media_data.media
            else:
                media = media_data
            
            # Check if media is within date range
            if not _is_media_in_date_range(media, start_date, end_date):
                continue
            
            # Calculate time based on media type
            if media_type == 'tv':
                total_minutes = _calculate_tv_hours(media, total_minutes)
            elif media_type == 'movie':
                total_minutes = _calculate_movie_hours(media, total_minutes)
            elif media_type == 'anime':
                total_minutes = _calculate_anime_hours(media, total_minutes)
            elif media_type == 'game':
                # For games, assume 1 hour per game (or could be based on play time if available)
                total_minutes += 60
            else:
                # For other media types, assume 1 hour
                total_minutes += 60
        
        # Convert to formatted time string (e.g., "17h 30min")
        hours_per_type[media_type] = _format_hours_minutes(total_minutes)
    
    return hours_per_type


def _get_season_metadata(media, season, season_metadata_cache, logger):
    """Get season metadata, using cache if available."""
    if season.item.season_number not in season_metadata_cache:
        try:
            season_metadata = providers.services.get_media_metadata(
                "season",
                media.item.media_id,
                media.item.source,
                [season.item.season_number]  # Note: season_numbers is a list
            )
            season_metadata_cache[season.item.season_number] = season_metadata
        except Exception as e:
            logger.warning(f"Failed to get season {season.item.season_number} metadata for {media.item.title}: {e}")
            season_metadata_cache[season.item.season_number] = None
    
    return season_metadata_cache[season.item.season_number]


def _is_episode_in_range(episode, start_date, end_date):
    """Check if episode is within the specified date range."""
    if episode.end_date and start_date and end_date:
        return start_date <= episode.end_date <= end_date
    elif not start_date and not end_date:
        # All time - include all episodes
        return True
    return False


def _calculate_episode_time(episode, season_metadata, media, season, logger):
    """Calculate time for a single episode."""
    if season_metadata and season_metadata.get("details", {}).get("runtime"):
        # Parse the runtime string (e.g., "45m", "1h 30m")
        runtime_str = season_metadata["details"]["runtime"]
        episode_minutes = parse_runtime_to_minutes(runtime_str)
        if episode_minutes:
            return episode_minutes
        else:
            # Fallback: assume 45 minutes per episode for TV
            logger.warning(f"Failed to parse runtime '{runtime_str}' for {media.item.title} S{season.item.season_number}, using fallback 45 minutes")
            return 45
    else:
        # Fallback: assume 45 minutes per episode for TV
        logger.warning(f"No runtime in season metadata for {media.item.title} S{season.item.season_number}, using fallback 45 minutes")
        return 45


def _calculate_tv_time(media, start_date, end_date, logger):
    """Calculate total time for TV shows."""
    total_time_minutes = 0
    episode_count = 0
    
    if not hasattr(media, 'seasons'):
        return total_time_minutes, episode_count
    
    # Cache season metadata to avoid repeated API calls
    season_metadata_cache = {}
    
    for season in media.seasons.all():
        if not hasattr(season, 'episodes'):
            continue
            
        # Get season metadata once per season
        season_metadata = _get_season_metadata(media, season, season_metadata_cache, logger)
        
        for episode in season.episodes.all():
            # Check if episode is within date range
            if not _is_episode_in_range(episode, start_date, end_date):
                continue
                
            episode_count += 1
            total_time_minutes += _calculate_episode_time(episode, season_metadata, media, season, logger)
    
    return total_time_minutes, episode_count


def _calculate_anime_time(media, start_date, end_date, logger):
    """Calculate total time for anime."""
    total_time_minutes = 0
    episode_count = 0
    
    # Check if anime is within date range
    if media.end_date and start_date and end_date:
        if start_date <= media.end_date <= end_date:
            episode_count = media.progress
            total_time_minutes = _get_anime_runtime_minutes(media, episode_count, logger, "(date range)")
    elif not start_date and not end_date:
        # All time
        episode_count = media.progress
        total_time_minutes = _get_anime_runtime_minutes(media, episode_count, logger, "(all time)")
    
    return total_time_minutes, episode_count


def _get_anime_runtime_minutes(media, episode_count, logger, context=""):
    """Get anime runtime in minutes from metadata."""
    try:
        anime_metadata = providers.services.get_media_metadata(
            "anime",
            media.item.media_id,
            media.item.source,
        )
        
        if anime_metadata and anime_metadata.get("details", {}).get("runtime"):
            # Parse the runtime string (e.g., "24m", "1h 30m", "12 min")
            runtime_str = anime_metadata["details"]["runtime"]
            logger.info(f"Anime '{media.item.title}' {context}: runtime '{runtime_str}' -> {parse_runtime_to_minutes(runtime_str)} minutes")
            episode_minutes = parse_runtime_to_minutes(runtime_str)
            if episode_minutes:
                return episode_count * episode_minutes
            else:
                # Fallback: assume 24 minutes per episode for anime
                logger.warning(f"Failed to parse runtime '{runtime_str}' for {media.item.title} {context}, using fallback 24 minutes per episode")
                return episode_count * 24
        else:
            # Fallback: assume 24 minutes per episode for anime
            logger.warning(f"No runtime in metadata for {media.item.title} {context}, using fallback 24 minutes per episode")
            return episode_count * 24
    except Exception as e:
        # Log the error for debugging
        logger.warning(f"Failed to get metadata for {media.item.title} {context}: {e}")
        # Fallback: assume 24 minutes per episode for anime
        return episode_count * 24


def _calculate_movie_time(media, start_date, end_date, normalized_type, logger):
    """Calculate total time for movies and other media types."""
    total_time_minutes = 0
    
    # Check if media is within date range
    if media.end_date and start_date and end_date:
        if start_date <= media.end_date <= end_date:
            total_time_minutes = _get_media_runtime_minutes(media, normalized_type, logger, "(date range)")
    elif not start_date and not end_date:
        # All time
        total_time_minutes = _get_media_runtime_minutes(media, normalized_type, logger, "(all time)")
    
    return total_time_minutes


def _get_media_runtime_minutes(media, normalized_type, logger, context=""):
    """Get media runtime in minutes from metadata."""
    try:
        media_metadata = providers.services.get_media_metadata(
            normalized_type,
            media.item.media_id,
            media.item.source,
        )
        
        if media_metadata and media_metadata.get("details", {}).get("runtime"):
            # Parse the runtime string (e.g., "2h 15m")
            runtime_str = media_metadata["details"]["runtime"]
            logger.info(f"Movie '{media.item.title}' {context}: runtime '{runtime_str}' -> {parse_runtime_to_minutes(runtime_str)} minutes")
            movie_minutes = parse_runtime_to_minutes(runtime_str)
            if movie_minutes:
                return movie_minutes
            else:
                # Fallback: assume 120 minutes per movie
                logger.warning(f"Failed to parse runtime '{runtime_str}' for {media.item.title} {context}, using fallback 120 minutes")
                return 120
        else:
            # Fallback: assume 120 minutes per movie
            logger.warning(f"No runtime in metadata for {media.item.title} {context}, using fallback 120 minutes")
            return 120
    except Exception as e:
        # Log the error for debugging
        logger.warning(f"Failed to get metadata for {media.item.title} {context}: {e}")
        # Fallback: assume 120 minutes per movie
        return 120


def get_top_played_media(user_media, start_date, end_date):
    """Get top played media by total time spent within date range.
    
    Returns a dictionary with media types as keys and lists of top media items.
    Each media item includes total_time_minutes, formatted_duration, and episode_count.
    """
    from app.helpers import minutes_to_hhmm
    import logging
    
    logger = logging.getLogger(__name__)
    top_played = {}
    
    # Define the media types we want to show
    target_media_types = ['movie', 'tv', 'game', 'anime']
    
    for media_type, media_list in user_media.items():
        # Normalize media type to match our target types
        normalized_type = media_type.lower()
        if normalized_type not in target_media_types:
            continue
            
        if not media_list.exists():
            continue
            
        # Get media items with their progress and metadata
        media_with_progress = []
        
        for media in media_list:
            total_time_minutes = 0
            episode_count = 0
            
            if normalized_type == "tv":
                total_time_minutes, episode_count = _calculate_tv_time(media, start_date, end_date, logger)
            elif normalized_type == "anime":
                total_time_minutes, episode_count = _calculate_anime_time(media, start_date, end_date, logger)
            elif normalized_type == "game":
                # For games, use progress field (stored in minutes)
                if media.end_date and start_date and end_date:
                    if start_date <= media.end_date <= end_date:
                        total_time_minutes += media.progress
                elif not start_date and not end_date:
                    # All time
                    total_time_minutes += media.progress
            else:
                # For movies and other media types, get runtime from metadata
                total_time_minutes = _calculate_movie_time(media, start_date, end_date, normalized_type, logger)
            
            if total_time_minutes > 0:
                media_with_progress.append({
                    'media': media,
                    'total_time_minutes': total_time_minutes,
                    'formatted_duration': minutes_to_hhmm(total_time_minutes),
                    'episode_count': episode_count,
                    'last_activity': media.end_date or media.start_date or media.created_at
                })
        
        # Sort by total time, then by most recent activity
        media_with_progress.sort(
            key=lambda x: (x['total_time_minutes'], x['last_activity']), 
            reverse=True
        )
        
        # Take top 10
        top_played[normalized_type] = media_with_progress[:10]
    
    return top_played
