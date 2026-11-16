import calendar as cal
import logging
from collections import defaultdict
from datetime import UTC, date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import (
    condition,
    require_GET,
    require_http_methods,
    require_POST,
)

from app import backdrops, feed_validation
from app.icon_specs import file_icon
from app.models import Item, MediaTypes, PodcastEpisode, Status
from app.templatetags.app_tags import (
    image_url,
    media_status_readable,
    media_type_readable,
    media_type_readable_plural,
    user_event_time,
)
from events import release_marker, tasks
from events.history_feed import (
    DEFAULT_MEDIA_TYPES,
    HISTORY_MEDIA_TYPES,
    get_history_plays,
    selected_history_media_types,
)
from events.models import INACTIVE_TRACKING_STATUSES, Event, ReleaseTypes
from events.templatetags.events_tags import calendar_event_url
from users.card_metadata import card_lines
from users.models import User, WeekStartDayChoices

logger = logging.getLogger(__name__)

ICS_DATETIME_FORMAT = "%Y%m%dT%H%M%SZ"
ICS_LINE_OCTETS = 75


def _escape_ics_text(value):
    """Escape a TEXT value (RFC 5545 section 3.3.11)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold_ics_line(line):
    """Fold a content line at 75 octets without splitting a UTF-8 character."""
    if len(line) * 4 <= ICS_LINE_OCTETS:
        return line
    folded = []
    current = ""
    limit = ICS_LINE_OCTETS
    for char in line:
        if len((current + char).encode()) > limit:
            folded.append(current)
            current = char
            limit = ICS_LINE_OCTETS - 1  # continuation lines start with a space
        else:
            current += char
    folded.append(current)
    return "\r\n ".join(folded)


def _user_for_token(request, token):
    """Return the user a calendar link belongs to, or None for an unknown one.

    Looked up once per request: the validator and the view both need it.
    """
    if not hasattr(request, "_calendar_feed_user"):
        try:
            request._calendar_feed_user = User.objects.get(token=token)
        except ObjectDoesNotExist:
            logger.warning(
                "Could not process Calendar request: Invalid token: %s",
                token,
            )
            request._calendar_feed_user = None
    return request._calendar_feed_user


def _upcoming_feed_etag(request, token):
    """Return the upcoming-releases link validator, computed without the body.

    The body depends on the user's tracked media and statuses (the user change
    marker), the stored release dates (the release marker), the day (the
    window moves daily), the link's filters and the user's enabled types and
    region. Dates are written in UTC, so time zone is not an input.
    """
    if hasattr(request, "_calendar_feed_etag"):
        return request._calendar_feed_etag
    user = _user_for_token(request, token)
    request._calendar_feed_etag = user and feed_validation.feed_etag(
        "upcoming",
        user.id,
        token,
        feed_validation.user_change_marker(user.id),
        release_marker.current(),
        timezone.now().date().isoformat(),
        request.GET.urlencode(),
        user.get_enabled_media_types(),
        user.watch_provider_region,
    )
    return request._calendar_feed_etag


def _history_feed_etag(request, token):
    """Return the watch-history link validator: plays, the day and the types."""
    if hasattr(request, "_calendar_feed_etag"):
        return request._calendar_feed_etag
    user = _user_for_token(request, token)
    request._calendar_feed_etag = user and feed_validation.feed_etag(
        "history",
        user.id,
        token,
        feed_validation.user_change_marker(user.id),
        timezone.now().date().isoformat(),
        sorted(selected_history_media_types(request.GET.getlist("media_types"))),
    )
    return request._calendar_feed_etag


@require_GET
def calendar(request):
    """Display the calendar page."""
    # Handle view type
    view_type = request.user.update_preference(
        "calendar_layout",
        request.GET.get("view"),
    )

    month = request.GET.get("month")
    year = request.GET.get("year")

    try:
        current_date = (
            date(int(year), int(month), 1) if month and year else timezone.localdate()
        )
        month, year = current_date.month, current_date.year
    except (ValueError, TypeError):
        logger.warning("Invalid month or year provided: %s, %s", month, year)
        current_date = timezone.localdate()
        month, year = current_date.month, current_date.year

    # Calculate navigation dates
    is_december = month == 12  # noqa: PLR2004
    is_january = month == 1

    prev_month = 12 if is_january else month - 1
    prev_year = year - 1 if is_january else year

    next_month = 1 if is_december else month + 1
    next_year = year + 1 if is_december else year

    # Calculate date range for events
    first_day = date(year, month, 1)
    last_day = date(
        year + 1 if is_december else year,
        1 if is_december else month + 1,
        1,
    ) - timedelta(days=1)

    # Get calendar data
    week_start_sunday = request.user.week_start_day == WeekStartDayChoices.SUNDAY
    first_weekday = 6 if week_start_sunday else 0
    calendar_format = cal.Calendar(firstweekday=first_weekday).monthdayscalendar(
        year, month
    )
    month_name = formats.date_format(current_date, "F")
    base_weekdays = [
        formats.date_format(date(2024, 1, day), "D") for day in range(1, 8)
    ]
    weekday_headers = (
        [base_weekdays[6], *base_weekdays[:6]] if week_start_sunday else base_weekdays
    )

    # Get events and organize by day
    releases = Event.objects.get_user_events(request.user, first_day, last_day)

    podcast_media_ids = [
        release.item.media_id
        for release in releases
        if release.item.media_type == MediaTypes.PODCAST.value
    ]
    podcast_art_by_episode_uuid = {}
    if podcast_media_ids:
        podcast_art_by_episode_uuid = {
            episode.episode_uuid: episode.show.image
            for episode in PodcastEpisode.objects.filter(
                episode_uuid__in=podcast_media_ids,
            ).select_related("show")
            if episode.show and episode.show.image
        }

    event_status_values = [
        status.value
        for status in Status
        if status.value not in INACTIVE_TRACKING_STATUSES
    ]

    filter_media_types = set(request.user.get_enabled_media_types())
    if MediaTypes.TV.value in filter_media_types:
        filter_media_types.add(MediaTypes.SEASON.value)
    filter_media_types = sorted(
        filter_media_types,
        key=lambda media_type: MediaTypes(media_type).label,
    )

    release_media_types = {
        release.item.media_type
        for release in releases
        if release.item and release.item.media_type
    }
    available_media_types = sorted(
        release_media_types,
        key=lambda media_type: MediaTypes(media_type).label,
    )

    available_release_types = sorted(
        {release.release_type for release in releases if release.release_type},
    )

    item_ids_by_type = defaultdict(list)
    for release in releases:
        item_ids_by_type[release.item.media_type].append(release.item_id)

    status_by_item_id = {}
    for media_type, item_ids in item_ids_by_type.items():
        status_by_item_id.update(
            Item.objects.filter(
                id__in=item_ids,
                **{f"{media_type}__user": request.user},
            ).values_list("id", f"{media_type}__status"),
        )

    available_statuses_by_type = {}
    for media_type, item_ids in item_ids_by_type.items():
        statuses = {
            status_by_item_id[item_id]
            for item_id in item_ids
            if status_by_item_id.get(item_id)
        }
        if statuses:
            available_statuses_by_type[media_type] = sorted(
                statuses,
                key=lambda status: Status(status).label,
            )

    filter_statuses_by_type = {
        media_type: event_status_values
        for media_type in filter_media_types
        if media_type not in {MediaTypes.TV.value, MediaTypes.EPISODE.value}
    }

    season_identities = {
        (release.item.source, release.item.media_id)
        for release in releases
        if release.item.media_type == MediaTypes.SEASON.value
    }
    show_titles = {}
    if season_identities:
        show_titles = {
            (source, media_id): title
            for source, media_id, title in Item.objects.filter(
                tv__user=request.user,
                media_type=MediaTypes.TV.value,
                source__in={source for source, _ in season_identities},
                media_id__in={media_id for _, media_id in season_identities},
            ).values_list("source", "media_id", "title")
        }

    release_dict = {}
    backdrop_by_item = {}
    backdrop_misses = []
    for release in releases:
        release.status = status_by_item_id.get(release.item_id)
        release.calendar_title = (
            _show_title(
                show_titles.get((release.item.source, release.item.media_id))
                or release.item.title,
                release.item.season_number,
            )
            if release.item.media_type == MediaTypes.SEASON.value
            else str(release.item)
        )
        if release.item_id not in backdrop_by_item:
            backdrop_by_item[release.item_id] = backdrops.cached_backdrop(release.item)
            if not backdrop_by_item[release.item_id]:
                backdrop_misses.append(release.item)
        release.calendar_backdrop = backdrop_by_item[release.item_id]

        if (
            release.item.media_type == MediaTypes.PODCAST.value
            and release.item.image in {"", settings.IMG_NONE}
        ):
            release.item.image = podcast_art_by_episode_uuid.get(
                release.item.media_id,
                settings.IMG_NONE,
            )

        # Convert UTC datetime to user's timezone and extract day
        local_datetime = timezone.localtime(release.datetime)
        day = local_datetime.day
        if day not in release_dict:
            release_dict[day] = []
        release_dict[day].append(release)

    if backdrop_misses:
        backdrops.schedule_backdrop_warm(backdrop_misses)

    # Get today's date for highlighting
    today = timezone.localdate()
    days_in_month = range(1, last_day.day + 1)
    selected_day = (
        today.day
        if month == today.month and year == today.year
        else next(iter(sorted(release_dict.keys())), 1)
    )

    context = {
        "user": request.user,
        "media_types": [
            media_type.value
            for media_type in MediaTypes
            if media_type != MediaTypes.EPISODE
        ],
        "event_statuses": event_status_values,
        "history_media_types": HISTORY_MEDIA_TYPES,
        "history_default_media_types": DEFAULT_MEDIA_TYPES,
        "calendar": calendar_format,
        "month": month,
        "month_name": month_name,
        "year": year,
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
        "release_dict": release_dict,
        "today": today,
        "view_type": view_type,
        "available_media_types": available_media_types,
        "release_type_choices": ReleaseTypes.choices,
        "available_release_types": available_release_types,
        "region_unset": request.user.watch_provider_region == "UNSET",
        "available_statuses_by_type": available_statuses_by_type,
        "filter_media_types": filter_media_types,
        "filter_statuses_by_type": filter_statuses_by_type,
        "days_in_month": days_in_month,
        "selected_day": selected_day,
        "weekday_headers": weekday_headers,
        "calendar_menu": _calendar_menu_props(request, today),
    }
    context["calendar_props"] = _release_calendar_props(request, context)
    context["calendar_links_props"] = _calendar_links_props(request, context)
    return render(request, "events/calendar.html", context)


def _show_title(title, season_number):
    """Drop a trailing "Season N" so the season only appears in its own label."""
    suffix = f" Season {season_number}"
    if season_number is not None and title.endswith(suffix) and title != suffix.strip():
        return title.removesuffix(suffix)
    return title


def _calendar_menu_props(request, today):
    """Return the props the shared React calendar menu needs."""
    return {
        "csrf": get_token(request),
        "icon": file_icon("settings"),
        "weekStart": request.user.week_start_day,
        "labels": {
            "menu": gettext("Calendar Actions"),
            "today": gettext("Go to Today"),
            "fetch": gettext("Fetch New Releases"),
            "download": gettext("Download Calendar"),
            "weekStart": gettext("Week starts on"),
            "monday": gettext("Monday"),
            "sunday": gettext("Sunday"),
        },
        "urls": {
            "today": f"{reverse('calendar')}?month={today.month}&year={today.year}",
            "fetch": reverse("reload_calendar"),
            "download": reverse(
                "download_calendar", kwargs={"token": request.user.token}
            ),
            "preferences": reverse("update_statistics_preferences"),
        },
    }


def _calendar_links_props(request, context):
    """Return the serialisable props for the shared calendar links panel."""
    media_types = context["media_types"]
    enabled_media_types = set(request.user.get_enabled_media_types())
    release_type_choices = context["release_type_choices"]
    return {
        "releaseBaseUrl": request.build_absolute_uri(
            reverse("download_calendar", kwargs={"token": request.user.token}),
        ),
        "historyBaseUrl": request.build_absolute_uri(
            reverse("download_history_calendar", kwargs={"token": request.user.token}),
        ),
        "releaseMediaTypes": [
            {
                "value": media_type,
                "label": media_type_readable_plural(media_type),
            }
            for media_type in media_types
        ],
        "historyMediaTypes": [
            {
                "value": media_type,
                "label": media_type_readable_plural(media_type),
            }
            for media_type in HISTORY_MEDIA_TYPES
        ],
        "statuses": [
            {
                "value": status,
                "label": media_status_readable(status),
            }
            for status in context["event_statuses"]
        ],
        "releaseTypes": [
            {"value": value, "label": label} for value, label in release_type_choices
        ],
        "initialFeed": "releases",
        "initialReleaseMediaTypes": [
            media_type
            for media_type in media_types
            if media_type in enabled_media_types
        ],
        "initialHistoryMediaTypes": list(DEFAULT_MEDIA_TYPES),
        "initialStatuses": list(context["event_statuses"]),
        "initialReleaseTypes": [value for value, _label in release_type_choices],
        "labels": {
            "title": gettext("Calendar links"),
            "releasesHelp": gettext(
                "Add this link to any calendar app to see upcoming releases there.",
            ),
            "historyHelp": gettext(
                "Add this link to any calendar app to see what you watched, read and played, on the day you logged it.",
            ),
            "releasesTab": gettext("Upcoming releases"),
            "historyTab": gettext("Watch history"),
            "mediaTypes": gettext("Media Types"),
            "status": gettext("Status"),
            "releaseDates": gettext("Movie Release Dates"),
            "releaseMediaHelp": gettext(
                "Choose which media types to include in this calendar URL.",
            ),
            "historyMediaHelp": gettext(
                "Music, podcasts and videos start off because they add many entries.",
            ),
            "statusHelp": gettext(
                "Choose which statuses to include in this calendar URL.",
            ),
            "releaseDatesHelp": gettext(
                "Choose which digital and physical release dates to include in this calendar URL.",
            ),
            "subscription": gettext("Subscription link"),
            "ics": gettext("ICS"),
            "copy": gettext("Copy link"),
            "copied": gettext("Copied"),
            "download": gettext("Download calendar"),
            "unavailable": gettext("No calendar link is available."),
        },
    }


def _release_calendar_props(request, context):
    """Return the serialisable props for the shared release calendar."""
    month = context["month"]
    year = context["year"]
    release_dict = context["release_dict"]
    release_type_choices = context["release_type_choices"]
    available_statuses = context["available_statuses_by_type"]
    calendar_url = reverse("calendar")

    releases = {
        str(day): [_release_props(request, release) for release in day_releases]
        for day, day_releases in release_dict.items()
    }
    statuses_by_type = {
        media_type: [
            {
                "value": status,
                "label": media_status_readable(status),
                "inMonth": status in available_statuses.get(media_type, []),
            }
            for status in statuses
        ]
        for media_type, statuses in context["filter_statuses_by_type"].items()
    }
    return {
        "month": month,
        "year": year,
        "monthName": context["month_name"],
        "weekdayHeaders": context["weekday_headers"],
        "weeks": context["calendar"],
        "daysInMonth": list(context["days_in_month"]),
        "dayLabels": {
            str(day): formats.date_format(
                date(year, month, day),
                "l",
            )
            for day in context["days_in_month"]
        },
        "today": {
            "day": context["today"].day,
            "month": context["today"].month,
            "year": context["today"].year,
        },
        "selectedDay": context["selected_day"],
        "view": "list" if context["view_type"] == "list" else "grid",
        "viewIcons": {"grid": file_icon("grid"), "list": file_icon("list")},
        "regionUnset": context["region_unset"],
        "regionNotice": gettext(
            "Set your watch provider region in Preferences to see digital and physical dates.",
        ),
        "preferencesUrl": reverse("preferences"),
        "releases": releases,
        "mediaTypes": [
            {
                "value": media_type,
                "label": media_type_readable_plural(media_type),
                "shortLabel": media_type_readable(media_type),
            }
            for media_type in context["filter_media_types"]
        ],
        "releaseTypes": [
            {"value": value, "label": label} for value, label in release_type_choices
        ],
        "statusesByType": statuses_by_type,
        "initialFilters": {
            "selectedTypes": list(context["filter_media_types"]),
            "selectedStatusesByType": {
                media_type: [status["value"] for status in statuses]
                for media_type, statuses in statuses_by_type.items()
            },
            "selectedReleaseTypes": [value for value, _label in release_type_choices],
        },
        "navigation": {
            "previous": (
                f"{calendar_url}?month={context['prev_month']}"
                f"&year={context['prev_year']}"
            ),
            "next": (
                f"{calendar_url}?month={context['next_month']}"
                f"&year={context['next_year']}"
            ),
            "today": (
                f"{calendar_url}?month={context['today'].month}"
                f"&year={context['today'].year}"
            ),
            "views": {
                "grid": f"{calendar_url}?view=grid&month={month}&year={year}",
                "list": f"{calendar_url}?view=list&month={month}&year={year}",
            },
        },
        "menu": context["calendar_menu"],
        "filterStorageKey": f"yamtrack.calendar.filters.{request.user.id}",
        "labels": {
            "filters": gettext("Filters"),
            "all": gettext("All"),
            "none": gettext("None"),
            "mediaTypes": gettext("Media Types"),
            "status": gettext("Status"),
            "movieReleaseDates": gettext("Movie Release Dates"),
            "clearAll": gettext("Clear all filters"),
            "close": gettext("Close"),
            "agenda": gettext("Agenda"),
            "closeDay": gettext("Close day details"),
            "noDayReleases": gettext("No releases scheduled for this day."),
            "noFilteredReleases": gettext("No releases for selected media types."),
            "today": gettext("Today"),
            "release": gettext("release"),
            "releases": gettext("releases"),
            "viewMonth": gettext("Month"),
            "viewList": gettext("List"),
            "previousMonth": gettext("Previous month"),
            "nextMonth": gettext("Next month"),
            "filterCalendar": gettext("Filter calendar by media type"),
        },
    }


def _release_props(request, release):
    """Return one release as serialisable presentation data."""
    item = release.item
    event_time = user_event_time(release, request.user)
    episode_label = release.season_episode_label or ""
    release_type_label = (
        release.get_release_type_display() if release.release_type else ""
    )
    if episode_label:
        subtitle = episode_label
    elif release_type_label:
        subtitle = f"{release_type_label} {gettext('release')}"
    else:
        subtitle = ""
    if event_time:
        subtitle = f"{subtitle} {event_time}".strip()
    elif not episode_label and not release_type_label:
        subtitle = gettext("Time unknown")

    # The release list names the item itself ("Show S4") and shows the short
    # content number ("E1 at 2 a.m."); the month agenda uses the longer forms.
    list_parts = []
    if release.readable_content_number:
        list_parts.append(
            f"{release.readable_content_number} {event_time or ''}".strip()
        )
    if release_type_label:
        list_parts.append(f"{release_type_label} {gettext('release')}")

    image = release.calendar_backdrop or item.image
    return {
        "id": release.id,
        "mediaType": item.media_type,
        "mediaLabel": media_type_readable(item.media_type),
        "releaseType": release.release_type or "",
        "status": release.status or "",
        "title": release.calendar_title,
        "href": calendar_event_url(release),
        "image": image_url(image),
        "subtitle": subtitle,
        "listTitle": str(item),
        "listSubtitle": " ".join(list_parts) or subtitle,
        "unknownTime": release.is_sentinel_time,
        "sortTime": 0
        if release.is_sentinel_time
        else int(release.datetime.timestamp()),
        "cardLines": [
            line["text"]
            for line in card_lines(request.user, item.media_type, item)
            if line.get("text")
        ],
    }


@require_POST
def reload_calendar(request):
    """Refresh the calendar with the latest dates."""
    tasks.reload_calendar.delay(user_id=request.user.id)
    messages.info(request, "The task to refresh upcoming releases has been queued.")
    return redirect("calendar")


@login_not_required
@csrf_exempt
@require_http_methods(["GET", "HEAD", "PROPFIND"])
@cache_control(private=True, max_age=feed_validation.FEED_MAX_AGE_SECONDS)
@condition(etag_func=_upcoming_feed_etag)
def download_calendar(request, token: str):
    """Download the calendar as a iCalendar file."""
    user = _user_for_token(request, token)
    if user is None:
        return HttpResponse(status=401)

    now = timezone.now()

    # Calendar apps poll this feed on their own schedule. Clients that send
    # the ETag back get a 304 above; for the rest the rendered feed is kept
    # under its validator, so an unchanged poll is a cache read and any
    # change misses (docs/architecture/view-caches.md, "Token feeds").
    feed_cache_key = feed_validation.body_cache_key(
        "calendar_feed:v2",
        user.id,
        _upcoming_feed_etag(request, token),
    )
    cached_feed = cache.get(feed_cache_key)
    if cached_feed is not None:
        return _calendar_feed_response(cached_feed)

    # Define default start and end date (from past 30 days to incoming 90 days)
    start_date = now.date() - timedelta(days=30)
    end_date = now.date() + timedelta(days=90)

    # Retrieve release events
    releases = Event.objects.get_user_events(user, start_date, end_date)

    selected_media_types = request.GET.getlist("media_types")
    if selected_media_types:
        valid_media_types = {
            media_type
            for media_type in selected_media_types
            if media_type in {choice.value for choice in MediaTypes}
        }

        # TV release events are stored at the season level.
        if MediaTypes.TV.value in valid_media_types:
            valid_media_types.add(MediaTypes.SEASON.value)

        if valid_media_types:
            releases = releases.filter(item__media_type__in=valid_media_types)

    # No parameter means every release date; "none" leaves only main releases.
    if "release_types" in request.GET:
        allowed_release_types = set(request.GET.getlist("release_types")) & set(
            ReleaseTypes.values,
        )
        releases = releases.filter(
            Q(release_type="") | Q(release_type__in=allowed_release_types),
        )

    selected_statuses = request.GET.getlist("status")
    if selected_statuses:
        valid_statuses = {
            status
            for status in selected_statuses
            if status in {c.value for c in Status}
        }

        if valid_statuses:
            status_query = Q()
            for media_type in MediaTypes:
                if media_type in (MediaTypes.TV, MediaTypes.EPISODE):
                    continue
                status_query |= Q(
                    item__media_type=media_type.value,
                    **{f"item__{media_type.value}__status__in": valid_statuses},
                )
            releases = releases.filter(status_query)

    # An entry only needs its time and its title, and reading every Item
    # column (a dozen of them JSON) for each row was most of the query time.
    releases = releases.only(
        "datetime",
        "content_number",
        "release_type",
        "item__title",
        "item__media_type",
        "item__season_number",
        "item__episode_number",
    )

    # Written out directly: building an icalendar.Event per release spent most
    # of the request's time (about 0.3 ms each, on feeds of thousands).
    dtstamp = now.astimezone(UTC).strftime(ICS_DATETIME_FORMAT)
    lines = ["BEGIN:VCALENDAR", "PRODID:-//Floppy//EN", "VERSION:2.0"]
    for release in releases:
        if release.is_sentinel_time:
            start_date = release.datetime.date()
            dtstart = f"DTSTART;VALUE=DATE:{start_date:%Y%m%d}"
            dtend = f"DTEND;VALUE=DATE:{start_date + timedelta(days=1):%Y%m%d}"
        else:
            dt_tz_aware = release.datetime.replace(tzinfo=UTC)
            dtstart = f"DTSTART:{dt_tz_aware.strftime(ICS_DATETIME_FORMAT)}"
            dtend = f"DTEND:{dt_tz_aware.strftime(ICS_DATETIME_FORMAT)}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{release.id}",
            _fold_ics_line(f"SUMMARY:{_escape_ics_text(str(release))}"),
            dtstart,
            dtend,
            f"DTSTAMP:{dtstamp}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")

    feed = ("\r\n".join(lines) + "\r\n").encode()
    feed_validation.remember_body(feed_cache_key, feed)
    return _calendar_feed_response(feed)


@login_not_required
@csrf_exempt
@require_http_methods(["GET", "HEAD", "PROPFIND"])
@cache_control(private=True, max_age=feed_validation.FEED_MAX_AGE_SECONDS)
@condition(etag_func=_history_feed_etag)
def download_history_calendar(request, token: str):
    """Download finished plays (what the user watched, read, played) as iCal."""
    user = _user_for_token(request, token)
    if user is None:
        return HttpResponse(status=401)

    now = timezone.now()
    media_types = selected_history_media_types(request.GET.getlist("media_types"))

    # Kept under its validator, like the upcoming feed. The validator holds
    # the resolved types, so a link with no selection shares an entry with
    # one that lists the defaults.
    feed_cache_key = feed_validation.body_cache_key(
        "calendar_history_feed:v2",
        user.id,
        _history_feed_etag(request, token),
    )
    cached_feed = cache.get(feed_cache_key)
    if cached_feed is not None:
        return _calendar_feed_response(cached_feed, "history.ics")

    dtstamp = now.astimezone(UTC).strftime(ICS_DATETIME_FORMAT)
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Floppy//EN",
        "VERSION:2.0",
        "X-WR-CALNAME:Floppy history",
    ]
    for play in get_history_plays(user, media_types, now):
        if play.is_date_only:
            dtstart = f"DTSTART;VALUE=DATE:{play.local_date:%Y%m%d}"
            dtend = f"DTEND;VALUE=DATE:{play.local_date + timedelta(days=1):%Y%m%d}"
        else:
            played_at = play.played_at.astimezone(UTC).strftime(ICS_DATETIME_FORMAT)
            dtstart = f"DTSTART:{played_at}"
            dtend = f"DTEND:{played_at}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{play.uid}@floppy-history",
            _fold_ics_line(f"SUMMARY:{_escape_ics_text(play.summary)}"),
            dtstart,
            dtend,
            f"DTSTAMP:{dtstamp}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")

    feed = ("\r\n".join(lines) + "\r\n").encode()
    feed_validation.remember_body(feed_cache_key, feed)
    return _calendar_feed_response(feed, "history.ics")


def _calendar_feed_response(feed, filename="calendar.ics"):
    """Return the iCal file."""
    response = HttpResponse(feed, content_type="text/calendar")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
