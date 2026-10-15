import calendar
import datetime
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
from django.utils.dateparse import parse_date

from app import config
from app.models import TV, BasicMedia, Episode, MediaManager, MediaTypes, Season, Status
from app.templatetags import app_tags
from users.models import WeekStartDayChoices

logger = logging.getLogger(__name__)


def parse_activity_date_range(request):
    """Parse ``start-date``/``end-date`` params into an aware datetime range.

    Defaults to the last year. ``all`` for both means no range (``None``).
    """
    timeformat = "%Y-%m-%d"
    today = timezone.localdate()
    # relativedelta clamps Feb 29 to Feb 28 instead of raising ValueError.
    one_year_ago = today - relativedelta(years=1)

    start_date_str = request.GET.get("start-date") or one_year_ago.strftime(timeformat)
    end_date_str = request.GET.get("end-date") or today.strftime(timeformat)

    if start_date_str == "all" and end_date_str == "all":
        return None, None

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    if start_date and end_date:
        start_date = timezone.make_aware(
            datetime.datetime.combine(start_date, datetime.datetime.min.time()),
        )
        end_date = timezone.make_aware(
            datetime.datetime.combine(end_date, datetime.datetime.max.time()),
        )

    return start_date, end_date


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


def get_status_total(status_distribution, status):
    """Return the total count of a single status across all media types."""
    for dataset in status_distribution["datasets"]:
        if dataset["label"] == status:
            return dataset["total"]
    return 0


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


def _top_rated_group_key(media_type, media):
    """Return the key used to dedupe repeated entries of the same item.

    A single item (e.g. a rewatched movie) can have multiple scored rows, so
    without this they'd surface as separate "duplicate" entries for what a
    user perceives as a single item. Seasons and their parent TV show are
    kept separate since they represent distinct ratings.
    """
    return (media_type, media.item_id)


def get_score_distribution(user_media):
    """Get score distribution for each media type within date range."""
    distribution = {}
    total_scored = 0
    total_score_sum = 0

    top_rated_by_key = {}
    score_range = range(11)

    for media_type, media_list in user_media.items():
        score_counts = dict.fromkeys(score_range, 0)
        # Only item + score are read here, so drop any prefetch (e.g. the TV
        # seasons/episodes graph) that get_user_media attached — surviving
        # top-rated rows are re-fetched with the right prefetch afterwards.
        scored_media = (
            media_list.exclude(score__isnull=True)
            .select_related("item")
            .prefetch_related(None)
        )

        for media in scored_media:
            key = _top_rated_group_key(media_type, media)
            existing = top_rated_by_key.get(key)
            # Keep the score from the most recently created entry so repeated
            # or imported ratings for the same item aren't all listed.
            if existing is None or media.created_at > existing.created_at:
                top_rated_by_key[key] = media

            binned_score = int(media.score)
            score_counts[binned_score] += 1
            total_scored += 1
            total_score_sum += media.score

        distribution[media_type] = score_counts

    average_score = (
        round(total_score_sum / total_scored, 2) if total_scored > 0 else None
    )

    top_rated_count = 14
    top_rated_media = sorted(
        top_rated_by_key.values(),
        key=lambda media: (-float(media.score), -media.created_at.timestamp()),
    )[:top_rated_count]

    top_rated_media = _annotate_top_rated_media(top_rated_media)

    return {
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
    try:
        return config.get_status_stats_color(status)
    except KeyError:
        return "rgba(201, 203, 207)"


def _consumed_value_and_unit(media_type, queryset, item_count):
    """Return the consumed amount and its sub-unit noun for one media type.

    The sub-unit is ``None`` when the media type is counted as whole items
    (movies). Returns ``None`` when nothing has been consumed. TV is excluded
    by the caller: its episodes are tallied through seasons and its
    ``progress`` is a computed property rather than an aggregatable column.
    """
    if media_type == MediaTypes.SEASON.value:
        # ``episodes`` are prefetched in get_user_media, so this reuses the
        # cache instead of issuing a query per season.
        value = sum(len(season.episodes.all()) for season in queryset)
    elif media_type == MediaTypes.MOVIE.value:
        # Whole movies are counted; reuse the count get_user_media already ran.
        value = item_count
    else:
        value = queryset.aggregate(total=models.Sum("progress"))["total"] or 0

    if not value:
        return None

    if media_type == MediaTypes.GAME.value:
        # ``progress`` is stored in minutes for games; "hour" is a unit of
        # time here, not a media-type name.
        value = round(value / 60)
        if not value:
            return None
        return value, "hour"

    # Sub-unit (Episode, Chapter, Page, ...) when the media type has one;
    # otherwise it is counted as whole items (movies).
    unit = config.get_config(media_type).get("unit")
    return value, unit[1].lower() if unit else None


def _consumption_label(media_type):
    """Human media type name for a consumption card (season data is TV)."""
    if media_type == MediaTypes.SEASON.value:
        # Season rows aggregate TV episode watches; "TV" reads better than the
        # "TV Season" label and is derived from the enum rather than hardcoded.
        return MediaTypes.TV.value.upper()
    return MediaTypes(media_type).label


def _consumption_descriptor(media_type, value, unit_noun):
    """Build a descriptor like "TV episodes watched" or "Movies watched"."""
    label = _consumption_label(media_type)
    verb = config.get_verb(media_type, past_tense=True)
    plural = "" if value == 1 else "s"

    if unit_noun is None:
        # Whole items are counted, so the media type name is the noun.
        return f"{label}{plural} {verb}"
    if verb.startswith(unit_noun):
        # Avoid stutter such as "plays played".
        return f"{label} {unit_noun}{plural}"
    return f"{label} {unit_noun}{plural} {verb}"


def get_consumption_stats(user_media, media_count):
    """Aggregate how much of each media type the user has consumed.

    Returns one entry per media type with a real total (episodes watched,
    chapters/pages read, hours played, ...), ready for a card grid. Each
    descriptor names the media type so, e.g., TV and anime episodes read
    distinctly. ``media_count`` supplies per-type counts already computed by
    get_user_media so movies aren't counted a second time.
    """
    results = []
    for media_type, queryset in user_media.items():
        if media_type == MediaTypes.TV.value:
            continue
        computed = _consumed_value_and_unit(
            media_type,
            queryset,
            media_count[media_type],
        )
        if computed is None:
            continue
        value, unit_noun = computed
        results.append(
            {
                "media_type": media_type,
                "value": value,
                "descriptor": _consumption_descriptor(media_type, value, unit_noun),
                "color": config.get_stats_color(media_type),
            },
        )

    return results


def _build_month_labels(date_range, week_start_weekday):
    """Build month labels and their corresponding week counts for the activity grid."""
    months = []
    weeks_per_month = []
    current_month = date_range[0].strftime("%b")
    week_count = 0

    for current_date in date_range:
        if current_date.weekday() == week_start_weekday:
            month = current_date.strftime("%b")

            if current_month != month:
                if current_month is not None:
                    if week_count > 1:
                        months.append(current_month)
                        weeks_per_month.append(week_count)
                    else:
                        months.append("")
                        weeks_per_month.append(week_count)
                current_month = month
                week_count = 0

            week_count += 1
    # For the last month
    if week_count > 1:
        months.append(current_month)
        weeks_per_month.append(week_count)

    return months, weeks_per_month


def get_activity_data(user, start_date, end_date):
    """Get daily activity counts for the last year."""
    if end_date is None:
        end_date = timezone.localtime()

    week_start_sunday = user.week_start_day == WeekStartDayChoices.SUNDAY
    start_date_aligned = get_aligned_week_start(
        start_date,
        week_start_sunday=week_start_sunday,
    )

    combined_data = get_filtered_historical_data(start_date_aligned, end_date, user)

    # update start_date values from historical records if not provided
    if start_date is None:
        dates = [item["date"] for item in combined_data]
        start_date = datetime.datetime.combine(
            min(dates) if dates else timezone.localdate(),
            datetime.time.min,
        )
        start_date_aligned = get_aligned_week_start(
            start_date,
            week_start_sunday=week_start_sunday,
        )

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

    # Generate months list with their week-start-day counts
    # The first day of each week column corresponds to the user's chosen week start day
    week_start_weekday = 6 if week_start_sunday else 0  # 0=Monday, 6=Sunday
    months, weeks_per_month = _build_month_labels(date_range, week_start_weekday)

    # Weekday labels depend on week start day
    days = list(calendar.day_abbr)
    weekday_labels = [days[6], *days[0:6]] if week_start_sunday else days

    return {
        "calendar_weeks": calendar_weeks,
        "months": list(zip(months, weeks_per_month, strict=False)),
        "weekday_labels": weekday_labels,
        "stats": {
            "most_active_day": most_active_day,
            "most_active_day_percentage": day_percentage,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        },
    }


def get_aligned_week_start(datetime_obj, *, week_start_sunday=False):
    """Get the first day of the week containing the given date."""
    if datetime_obj is None:
        return None

    if week_start_sunday:
        # Sunday=weekday 6; if Sunday, subtract 0; else subtract (weekday+1)
        days_to_subtract = (datetime_obj.weekday() + 1) % 7
    else:
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
