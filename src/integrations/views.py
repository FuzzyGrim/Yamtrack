"""Contains views for importing and exporting media data from various sources."""

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from integrations.utils import exports, imports

logger = logging.getLogger(__name__)


def import_mal(request: HttpRequest) -> HttpResponse:
    """View for importing anime and manga data from MyAnimeList."""

    try:
        imports.mal_data(request.POST["mal"], request.user)
        messages.success(request, "Your MyAnimeList has been imported!")
    except ValueError as error:
        if str(error) == "not_found":
            messages.error(request, f"User {request.POST['mal']} not found in MyAnimeList.")
        else:
            messages.error(request, "Something went wrong while importing your MyAnimeList.")

    return redirect("profile")


def import_tmdb_ratings(request: HttpRequest) -> HttpResponse:
    """View for importing TMDB movie and TV ratings."""

    try:
        imports.tmdb_data(
            request.FILES["tmdb_ratings"],
            request.user,
            status="Completed",
        )
        messages.success(request, "Your TMDB ratings have been imported!")
    except ValueError as error:
        messages.error(request, error)

    return redirect("profile")


def import_tmdb_watchlist(request: HttpRequest) -> HttpResponse:
    """View for importing TMDB movie and TV watchlist."""

    try:
        imports.tmdb_data(
            request.FILES["tmdb_watchlist"],
            request.user,
            status="Planning",
        )
        messages.success(request, "Your TMDB watchlist has been imported!")
    except ValueError as error:
        messages.error(request, error)

    return redirect("profile")


def import_anilist(request: HttpRequest) -> HttpResponse:
    """View for importing anime and manga data from AniList."""

    try:
        warning_message = imports.anilist_data(request.POST["anilist"], request.user)
        if warning_message:
            title = "Couldn't import the following Anime or Manga: \n"
            messages.warning(request, title + warning_message)
        else:
            messages.success(request, "Your AniList has been imported!")

    except ValueError as error:
        if str(error) == "User not found":
            messages.error(request, f"User {request.POST['anilist']} not found in AniList.")
        else:
            messages.error(request, "Something went wrong while importing your AniList.")

    return redirect("profile")


def import_yamtrack(request: HttpRequest) -> HttpResponse:
    """View for importing anime and manga data from Yamtrack CSV."""

    try:
        imports.yamtrack_data(request.FILES["yamtrack_csv"], request.user)
        messages.success(request, "Your Yamtrack CSV file has been imported!")
    except ValueError as error:
        messages.error(request, error)

    return redirect("profile")


def export_csv(request: HttpRequest) -> HttpResponse:
    """View for exporting all media data to a CSV file."""

    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(
        content_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="yamtrack.csv"'},
    )

    response = exports.db_to_csv(response, request.user)

    logger.info("User %s successfully exported their data", request.user.username)

    return response
