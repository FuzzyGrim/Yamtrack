from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from events import tasks
from events.models import Event


@require_GET
def calendar(request):
    """Display the calendar page."""
    user_events = Event.objects.user_events(request.user).select_related("item")

    calendar_events = [
        {
            "title": event.__str__(),
            "start": event.date.strftime("%Y-%m-%d"),
            "color": event.item.event_color,
            "url": event.item.url,
        }
        for event in user_events
    ]

    context = {
        "events": calendar_events,
    }
    return render(request, "events/calendar.html", context)


@require_POST
def reload_calendar(request):
    """Refresh the calendar with the latest dates."""
    tasks.reload_calendar.delay(request.user)
    messages.success(request, "Calendar refresh task successfully scheduled.")
    return redirect("profile")
