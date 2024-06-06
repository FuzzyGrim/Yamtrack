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


def get_form_title_and_id(media_type, media_id, season_number, title):
    """Get form title and id based on media."""
    if media_type == "season":
        title = f"{title} S{season_number}"
        form_id = f"form-{media_type}_{media_id}_{season_number}"
    else:
        form_id = f"form-{media_type}_{media_id}"
    return title, form_id
