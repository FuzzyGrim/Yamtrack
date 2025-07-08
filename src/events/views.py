import calendar as cal
import logging
from datetime import UTC, date, timedelta

import icalendar
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from events import tasks
from events.models import Event
from users.models import User

logger = logging.getLogger(__name__)


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
    calendar_format = cal.monthcalendar(year, month)
    month_name = cal.month_name[month]

    # Get events and organize by day
    releases = Event.objects.get_user_events(request.user, first_day, last_day)

    release_dict = {}
    for release in releases:
        # Convert UTC datetime to user's timezone and extract day
        local_datetime = timezone.localtime(release.datetime)
        day = local_datetime.day
        if day not in release_dict:
            release_dict[day] = []
        release_dict[day].append(release)

    # Get today's date for highlighting
    today = timezone.localdate()

    context = {
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
    }
    return render(request, "events/calendar.html", context)


@require_POST
def reload_calendar(request):
    """Refresh the calendar with the latest dates."""
    tasks.reload_calendar.delay(request.user)
    messages.info(request, "The task to refresh upcoming releases has been queued.")
    return redirect("calendar")


@login_not_required
@csrf_exempt
@require_http_methods(["GET", "HEAD", "PROPFIND"])
def download_calendar(_, token: str):
    """Download the calendar as a iCalendar file."""
    try:
        user = User.objects.get(token=token)
    except ObjectDoesNotExist:
        logger.warning(
            "Could not process Calendar request: Invalid token: %s",
            token,
        )
        return HttpResponse(status=401)

    now = timezone.now()

    # Define default start and end date (from past 30 days to incoming 90 days)
    start_date = now.date() - timedelta(days=30)
    end_date = now.date() + timedelta(days=90)

    # Retrieve release events
    releases = Event.objects.get_user_events(user, start_date, end_date)

    # Create iCalendar object
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Yamtrack//EN")
    cal.add("version", "2.0")

    for release in releases:
        cal_event = icalendar.Event()
        cal_event.add("uid", release.id)
        cal_event.add("summary", str(release))
        dt_tz_aware = release.datetime.replace(tzinfo=UTC)
        cal_event.add("dtstart", dt_tz_aware)
        cal_event.add("dtend", dt_tz_aware)
        cal_event.add("dtstamp", now)
        cal.add_component(cal_event)

    # Return the iCal file
    response = HttpResponse(cal.to_ical(), content_type="text/calendar")
    response["Content-Disposition"] = 'attachment; filename="calendar.ics"'
    return response
