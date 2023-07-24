from django.shortcuts import redirect
from django.contrib import messages
from django.http import HttpResponse

from app.utils import exports
from app.utils.imports import (
    import_anilist,
    import_mal,
    import_tmdb_watchlist,
    import_tmdb_ratings,
    import_csv,
)
from app.exceptions import ImportSourceError

import logging


logger = logging.getLogger(__name__)


def import_media(request):
    if "mal" in request.POST:
        try:
            import_mal(request.POST["mal"], request.user)
            messages.success(request, "Your MyAnimeList has been imported!")
        except ImportSourceError:
            messages.error(
                request, f"User {request.POST['mal']} not found in MyAnimeList."
            )

    elif "tmdb_ratings" in request.FILES:
        try:
            import_tmdb_ratings(request.FILES["tmdb_ratings"], request.user)
            messages.success(request, "Your TMDB ratings have been imported!")
        except ImportSourceError:
            messages.error(
                request,
                "The file you uploaded is not a valid TMDB ratings export file.",
            )

    elif "tmdb_watchlist" in request.FILES:
        try:
            import_tmdb_watchlist(request.FILES["tmdb_watchlist"], request.user)
            messages.success(request, "Your TMDB watchlist has been imported!")
        except ImportSourceError:
            messages.error(
                request,
                "The file you uploaded is not a valid TMDB watchlist export file.",
            )

    elif "anilist" in request.POST:
        try:
            warning_message = import_anilist(request.POST["anilist"], request.user)
            if warning_message:
                title = "Couldn't find a matching MAL ID for: \n"
                messages.warning(request, title + warning_message)
            else:
                messages.success(request, "Your AniList has been imported!")

        except ImportSourceError:
            messages.error(
                request, f"User {request.POST['anilist']} not found in AniList."
            )

    elif "yamtrack_csv" in request.FILES:
        try:
            import_csv(request.FILES["yamtrack_csv"], request.user)
            messages.success(request, "Your Yamtrack CSV file has been imported!")
        except ImportSourceError:
            messages.error(
                request,
                "The file you uploaded is not a valid Yamtrack CSV export file.",
                )

    else:
        messages.error(request, "No import source selected")

    return redirect("profile")


def export_media(request):
    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(
        content_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="yamtrack.csv"'},
    )

    response = exports.export_csv(response, request.user)

    logger.info(f"User {request.user.username} successfully exported their data")

    return response
