from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from events import tasks


@require_POST
def refresh_calendar(request):
    """View for importing anime and manga data from MyAnimeList."""
    tasks.refresh_calendar()
    return redirect("profile")
