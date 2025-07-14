import logging
from datetime import UTC

import apprise
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import formats, timezone

from app.models import TV, MediaTypes, Season
from app.templatetags import app_tags
from events.models import INACTIVE_TRACKING_STATUSES, Event

logger = logging.getLogger(__name__)


def send_releases():
    """Send notifications for recently released media."""
    now = timezone.now()
    thirty_minutes_ago = now - timezone.timedelta(minutes=30)

    # Get users who should receive notifications
    users = (
        get_user_model()
        .objects.filter(
            ~Q(notification_urls=""),
            release_notifications_enabled=True,
        )
        .prefetch_related("notification_excluded_items")
    )

    if not users.exists():
        return "No users with release notifications enabled"

    # Find events that were released recently and haven't been notified yet
    base_queryset = Event.objects.filter(
        datetime__gte=thirty_minutes_ago,
        datetime__lte=now,
        notification_sent=False,
    ).select_related("item")

    events = Event.objects.sort_with_sentinel_last(base_queryset)

    if not events.exists():
        return "No recent releases found"

    result = send_notifications(
        events=events,
        users=users,
        title="🔔 YamTrack: New Releases Available! 🔔",
    )

    # Mark events as notified
    if result["event_ids"]:
        Event.objects.filter(id__in=result["event_ids"]).update(
            notification_sent=True,
        )
        logger.info("Marked %s events as notified", len(result["event_ids"]))

    return f"{result['event_count']} recent releases processed"


def send_daily_digest():
    """Send daily digest of today's releases to users."""
    # Get current date in the timezone defined in settings
    now_in_current_tz = timezone.localtime()

    # Create start and end of today in the current timezone
    today_start = now_in_current_tz.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    today_end = today_start + timezone.timedelta(days=1)

    # Convert back to UTC for database query
    today_start_utc = today_start.astimezone(UTC)
    today_end_utc = today_end.astimezone(UTC)

    # Get users who have enabled daily digest
    users = (
        get_user_model()
        .objects.filter(
            ~Q(notification_urls=""),
            daily_digest_enabled=True,
        )
        .prefetch_related("notification_excluded_items")
    )

    if not users.exists():
        return "No users with daily digest enabled"

    # Get today's events using the converted UTC times
    base_queryset = Event.objects.filter(
        datetime__gte=today_start_utc,
        datetime__lt=today_end_utc,
    ).select_related("item")

    events = Event.objects.sort_with_sentinel_last(base_queryset)

    if not events.exists():
        return "No releases scheduled for today"

    # Format date for display in local timezone
    message_date = formats.date_format(
        today_start.date(),
        "DATE_FORMAT",
    )
    title = f"📆 YamTrack: Today's Releases ({message_date}) 📆"

    result = send_notifications(
        events=events,
        users=users,
        title=title,
    )

    return f"Daily digest sent for {result['event_count']} releases"


def send_notifications(events, users, title):
    """Process events and send notifications to appropriate users.

    Args:
        events: QuerySet of Event objects
        users: QuerySet of User objects
        title: Notification title

    Returns:
        Dictionary with results information
    """
    event_count = events.count()
    logger.info("Found %s events for notification", event_count)
    logger.info("Found %s eligible users", users.count())

    # Create event lookup for quick access
    events_by_item_and_content = {}
    event_ids = []

    for event in events:
        key = (event.item.id, event.content_number)
        events_by_item_and_content[key] = event
        event_ids.append(event.id)

    user_releases = get_user_releases(
        users=users,
        target_events=events_by_item_and_content,
    )

    deliver_notifications(user_releases, users, title)

    return {
        "event_count": event_count,
        "event_ids": event_ids,
    }


def get_user_releases(users, target_events):
    """Get user releases with optimized queries that avoid N+1 problems."""
    user_exclusions = {}
    for user in users:
        user_exclusions[user.id] = set(
            user.notification_excluded_items.values_list("id", flat=True),
        )

    user_enabled_types = {}
    for user in users:
        user_enabled_types[user.id] = user.get_enabled_media_types()

    user_tracking_data = get_all_user_tracking_data(
        users,
        target_events,
        user_exclusions,
    )
    user_releases = {}
    for user in users:
        user_events = []
        enabled_types = user_enabled_types[user.id]
        excluded_items = user_exclusions.get(user.id, set())

        for event in target_events.values():
            # Check if user has excluded this item
            if event.item.media_type != Season and event.item.id in excluded_items:
                continue

            # Check if user is tracking this media type
            if event.item.media_type not in enabled_types:
                continue

            # Check if user is tracking this item
            if is_user_tracking_item(
                user,
                event.item,
                user_tracking_data,
            ):
                user_events.append(event)

        if user_events:
            user_releases[user.id] = user_events

    return user_releases


def get_all_user_tracking_data(users, target_events, user_exclusions):
    """Get all user tracking data for the specified users and events."""
    user_ids = [user.id for user in users]

    # Group items by media type
    items_by_type = {}
    season_items = []
    for event in target_events.values():
        media_type = event.item.media_type

        if media_type == MediaTypes.SEASON.value:
            season_items.append(event.item)
            continue

        if media_type not in items_by_type:
            items_by_type[media_type] = []

        items_by_type[media_type].append(event.item.id)

    # Pre-fetch all tracking data for each media type
    tracking_data = {}

    for media_type, item_ids_for_type in items_by_type.items():
        media_model = apps.get_model(
            app_label="app",
            model_name=media_type.capitalize(),
        )

        # Get all user-item combinations for this media type
        media_objects = media_model.objects.filter(
            user_id__in=user_ids,
            item_id__in=item_ids_for_type,
        ).select_related("item")

        # Store in lookup format: (user_id, item_id) -> media_object
        for media_obj in media_objects:
            key = (media_obj.user_id, media_obj.item_id)
            tracking_data[key] = media_obj

    # Handle TV seasons separately
    tv_tracking_data = get_tv_tracking_data(users, season_items, user_exclusions)
    tracking_data.update(tv_tracking_data)

    return tracking_data


def get_tv_tracking_data(users, season_items, user_exclusions):
    """Get tracking data for TV shows and seasons."""
    user_ids = [user.id for user in users]

    if not season_items:
        return {}

    media_ids = list({item.media_id for item in season_items})

    # Pre-fetch all TV shows and seasons
    tv_lookup, season_lookup = build_tv_lookups(
        user_ids,
        media_ids,
        user_exclusions,
    )

    return determine_season_tracking_status(
        season_items,
        user_ids,
        tv_lookup,
        season_lookup,
    )


def build_tv_lookups(user_ids, media_ids, user_exclusions):
    """Build lookup structures for TV shows and seasons."""
    tv_shows = TV.objects.filter(
        user_id__in=user_ids,
        item__media_id__in=media_ids,
    ).select_related("item")

    seasons = Season.objects.filter(
        user_id__in=user_ids,
        item__media_id__in=media_ids,
    ).select_related("item")

    tv_lookup = {}  # (user_id, media_id) -> TV object
    season_lookup = {}  # (user_id, media_id) -> list of Season objects

    # Build TV lookup
    for tv in tv_shows:
        if tv.item.id not in user_exclusions.get(tv.user.id, set()):
            key = (tv.user_id, tv.item.media_id)
            tv_lookup[key] = tv

    # Build season lookup
    for season in seasons:
        if season.item.id not in user_exclusions.get(season.user.id, set()):
            key = (season.user_id, season.item.media_id)
            if key not in season_lookup:
                season_lookup[key] = []
            season_lookup[key].append(season)

    return tv_lookup, season_lookup


def determine_season_tracking_status(season_items, user_ids, tv_lookup, season_lookup):
    """Determine tracking status for each season item."""
    tracking_data = {}

    for season_item in season_items:
        for user_id in user_ids:
            is_tracking = check_user_season_tracking(
                user_id,
                season_item,
                tv_lookup,
                season_lookup,
            )

            if is_tracking is not None:
                item_key = (user_id, season_item.id)
                tracking_data[item_key] = is_tracking

    return tracking_data


def check_user_season_tracking(user_id, season_item, tv_lookup, season_lookup):
    """Check if a user is tracking a specific season.

    Returns:
        bool: True if tracking, False if not tracking, None if no TV show found
    """
    tv_key = (user_id, season_item.media_id)
    season_key = (user_id, season_item.media_id)

    # Check if user has the TV show and it's active
    tv_show = tv_lookup.get(tv_key)
    if not tv_show or tv_show.status in INACTIVE_TRACKING_STATUSES:
        return None

    # Check for dropped seasons
    user_seasons = season_lookup.get(season_key, [])
    dropped_seasons = [
        s
        for s in user_seasons
        if s.status in INACTIVE_TRACKING_STATUSES
        and s.item.season_number <= season_item.season_number
    ]

    if dropped_seasons:
        first_dropped = min(dropped_seasons, key=lambda s: s.item.season_number)
        return season_item.season_number < first_dropped.item.season_number

    return True


def is_user_tracking_item(user, item, user_tracking_data):
    """Check if user is tracking item using pre-fetched data."""
    media_type = item.media_type

    # Handle TV seasons
    if media_type == MediaTypes.SEASON.value:
        key = (user.id, item.id)
        return user_tracking_data.get(key, False)

    key = (user.id, item.id)
    media_obj = user_tracking_data.get(key)

    if not media_obj:
        return False

    return media_obj.status not in INACTIVE_TRACKING_STATUSES


def deliver_notifications(user_releases, users, title):
    """Deliver notifications to users using calendar logic.

    Args:
        user_releases: Dictionary mapping user IDs to lists of events
        users: QuerySet of User objects
        title: Notification title
    """
    # Create user lookup
    users_by_id = {user.id: user for user in users}

    for user_id, releases in user_releases.items():
        if not releases:
            continue

        user = users_by_id.get(user_id)
        if not user:
            logger.error("User %s not found", user_id)
            continue

        # Get notification URLs for this user
        urls = [
            url.strip() for url in user.notification_urls.splitlines() if url.strip()
        ]
        if not urls:
            continue

        # Format notification
        notification_body = format_notification(releases=releases)

        # Send notification
        send_user_notification(user, urls, title, notification_body)


def format_notification(releases):
    """Format notification text for releases.

    Args:
        releases: List of Event objects to include in the notification

    Returns:
        Formatted notification text as a string
    """
    # Group releases by media type
    releases_by_type = {}
    for event in releases:
        media_type = event.item.media_type
        if media_type not in releases_by_type:
            releases_by_type[media_type] = []
        releases_by_type[media_type].append(event)

    # Format the notification body
    notification_body = []

    notification_body.append("--------------------------------------------")

    # Add releases grouped by media type
    for media_type, media_events in releases_by_type.items():
        icon = app_tags.unicode_icon(media_type)

        # Add a header for each media type with icon
        if media_type == MediaTypes.SEASON.value:
            notification_body.append(f"{icon}  TV Shows")
        else:
            notification_body.append(f"{icon}  {media_type.upper()}")

        for event in media_events:
            if event.is_sentinel_time:
                # Don't show time for sentinel times
                notification_body.append(f"  • {event}")
            else:
                # Convert to local timezone and format
                local_dt = timezone.localtime(event.datetime)
                time_str = local_dt.strftime("%H:%M")
                notification_body.append(f"  • {event} ({time_str})")

        # Add a blank line between media types
        notification_body.append("")

    notification_body.append("Enjoy your media!")

    return "\n".join(notification_body)


def send_user_notification(user, urls, title, body):
    """Send a notification to a specific user.

    Args:
        user: User object
        urls: List of notification URLs
        title: Notification title
        body: Notification body
    """
    apobj = apprise.Apprise()
    for url in urls:
        apobj.add(url)

    try:
        result = apobj.notify(title=title, body=body)

        if result:
            logger.info(
                "Notification sent to %s",
                user.username,
            )
        else:
            logger.error(
                "Failed to send notification to %s",
                user.username,
            )
    except Exception:
        logger.exception("Error sending notification to %s", user.username)
