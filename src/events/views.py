from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from events import tasks
from events.models import Event


@require_GET
def calendar(request):
    """Display the calendar page."""
    user_events = Event.objects.user_events(request.user).select_related("item")

    colors = {
        "anime": "#0d6efd", # blue
        "manga": "#dc3545", # pink
        "game": "#d63384", # pink
        "tv": "#198754", # green
        "season": "#6f42c1", # purple
        "movie": "#fd7e14", # orange
    }

    calendar_events = [
        {
            "title": event.__str__(),
            "start": event.date.strftime("%Y-%m-%d"),
            "color": colors[event.item.media_type],
            "url": event.item.url,
        }
        for event in user_events
    ]

    context = {
        "events": calendar_events,
    }
    return render(request, "events/calendar.html", context)


@require_POST
def refresh_events(request): # noqa: ARG001
    """Refresh the calendar with the latest dates."""
    tasks.refresh_events()
    return redirect("profile")
