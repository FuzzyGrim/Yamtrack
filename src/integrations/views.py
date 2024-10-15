"""Contains views for importing and exporting media data from various sources."""

import logging
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST

from integrations import exports, tasks
from integrations.imports import simkl

logger = logging.getLogger(__name__)


@require_GET
def import_trakt(request):
    """View for importing anime and manga data from Trakt."""
    username = request.GET["trakt"]
    tasks.import_trakt.delay(username, request.user)
    messages.success(request, "Trakt import task queued.")
    return redirect("profile")


@require_GET
def simkl_oauth(request):
    """View for initiating the SIMKL OAuth2 authorization flow."""
    domain = request.get_host()
    scheme = request.scheme
    url = "https://simkl.com/oauth/authorize"

    return redirect(
        f"{url}?client_id={settings.SIMKL_ID}&redirect_uri={scheme}://{domain}/import/simkl&response_type=code",
    )


@require_GET
def import_simkl(request):
    """View for getting the SIMKL OAuth2 token."""
    token = simkl.get_token(request)
    tasks.import_simkl.delay(token, request.user)
    messages.success(request, "SIMKL import task queued.")
    return redirect("profile")


@require_GET
def import_mal(request):
    """View for importing anime and manga data from MyAnimeList."""
    username = request.GET["mal"]
    tasks.import_mal.delay(username, request.user)
    messages.success(request, "MyAnimeList import task queued.")
    return redirect("profile")


@require_POST
def import_tmdb_ratings(request):
    """View for importing TMDB movie and TV ratings."""
    tasks.import_tmdb.delay(
        request.FILES["tmdb_ratings"],
        request.user,
        "Completed",
    )
    messages.success(request, "TMDB ratings import task queued.")
    return redirect("profile")


@require_POST
def import_tmdb_watchlist(request):
    """View for importing TMDB movie and TV watchlist."""
    tasks.import_tmdb.delay(
        request.FILES["tmdb_watchlist"],
        request.user,
        "Planning",
    )
    messages.success(request, "TMDB watchlist import task queued.")
    return redirect("profile")


@require_GET
def import_anilist(request):
    """View for importing anime and manga data from AniList."""
    username = request.GET["anilist"]
    tasks.import_anilist.delay(username, request.user)
    messages.success(request, "AniList import task queued.")
    return redirect("profile")


@require_GET
def import_kitsu_name(request):
    """View for importing anime and manga data from Kitsu by username."""
    username = request.GET["kitsu_username"]
    tasks.import_kitsu_name.delay(username, request.user)
    messages.success(request, "Kitsu import task queued.")
    return redirect("profile")


@require_GET
def import_kitsu_id(request):
    """View for importing anime and manga data from Kitsu by user ID."""
    user_id = request.GET["kitsu_id"]
    tasks.import_kitsu_id.delay(user_id, request.user)
    messages.success(request, "Kitsu import task queued.")
    return redirect("profile")


@require_POST
def import_yamtrack(request):
    """View for importing anime and manga data from Yamtrack CSV."""
    tasks.import_yamtrack.delay(request.FILES["yamtrack_csv"], request.user)
    messages.success(request, "Yamtrack import task queued.")
    return redirect("profile")


@require_GET
def export_csv(request):
    """View for exporting all media data to a CSV file."""
    today = datetime.now(tz=settings.TZ).strftime("%Y-%m-%d")
    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="yamtrack_{today}.csv"'},
    )

    response = exports.db_to_csv(response, request.user)

    logger.info("User %s successfully exported their data", request.user.username)

    return response
