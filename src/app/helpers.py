from django.shortcuts import redirect
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme


def minutes_to_hhmm(total_minutes):
    """Convert total minutes to HH:MM format."""
    hours = int(total_minutes / 60)
    minutes = int(total_minutes % 60)
    if hours == 0:
        return f"{minutes}min"
    return f"{hours}h {minutes:02d}min"


def redirect_back(request):
    """Redirect to the previous page."""
    if url_has_allowed_host_and_scheme(request.GET.get("next"), None):
        url = iri_to_uri(request.GET["next"])
        return redirect(url)
    return redirect("home")

