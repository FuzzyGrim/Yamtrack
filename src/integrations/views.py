"""Contains views for importing and exporting media data from various sources."""

import logging

import requests
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
    except requests.exceptions.HTTPError:
        messages.error(
            request,
            "Something went wrong while importing your MyAnimeList.",
        )

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
    except UnicodeDecodeError:  # when the file is not a CSV file
        messages.error(
            request,
            "Couldn't import your TMDB ratings. Please make sure the file is a CSV file.",
        )
    except KeyError: # error parsing csv
        messages.error(
            request,
            "Something went wrong while parsing your TMDB ratings.",
        )

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
    except UnicodeDecodeError:  # when the file is not a CSV file
        messages.error(
            request,
            "Couldn't import your TMDB ratings. Please make sure the file is a CSV file.",
        )
    except KeyError: # error parsing csv
        messages.error(
            request,
            "Something went wrong while parsing your TMDB ratings.",
        )

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

    except requests.exceptions.HTTPError as error:
        if error.response.json().get("errors")[0].get("message") == "User not found":
            messages.error(
                request,
                f"User {request.POST['anilist']} not found in AniList.",
            )
        else:
            messages.error(
                request,
                "Something went wrong while importing your AniList.",
            )

    return redirect("profile")


def import_yamtrack(request: HttpRequest) -> HttpResponse:
    """View for importing anime and manga data from Yamtrack CSV."""

    try:
        imports.yamtrack_data(request.FILES["yamtrack_csv"], request.user)
        messages.success(request, "Your Yamtrack CSV file has been imported!")
    except UnicodeDecodeError:  # when the file is not a CSV file
        messages.error(
            request,
            "Couldn't import your Yamtrack CSV. Please make sure the file is a CSV file.",
        )
    except KeyError: # error parsing csv
        messages.error(
            request,
            "Something went wrong while parsing your Yamtrack CSV.",
        )

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
