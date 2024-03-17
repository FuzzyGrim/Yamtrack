"""Contains views for importing and exporting media data from various sources."""

import logging
from functools import wraps
from typing import ParamSpec, TypeVar

import requests
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect

from integrations import exports
from integrations.imports import anilist, mal, tmdb, yamtrack

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


def check_demo(view):
    """Check if the user is a demo account, used as decorator."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.is_demo:
            messages.error(request, "Demo accounts are not allowed to import.")
            return redirect("profile")

        return view(request, *args, **kwargs)

    return wrapper


@check_demo
def import_mal(request):
    """View for importing anime and manga data from MyAnimeList."""
    try:
        mal.importer(request.POST["mal"], request.user)
        messages.success(request, "Your MyAnimeList has been imported!")
    except requests.exceptions.HTTPError:
        messages.error(
            request,
            "Something went wrong while importing your MyAnimeList.",
        )

    return redirect("profile")


@check_demo
def import_tmdb_ratings(request):
    """View for importing TMDB movie and TV ratings."""
    try:
        tmdb.importer(
            request.FILES["tmdb_ratings"],
            request.user,
            status="Completed",
        )
        messages.success(request, "Your TMDB ratings have been imported!")
    except UnicodeDecodeError:  # when the file is not a CSV file
        messages.error(
            request,
            "Couldn't import your TMDB ratings. Make sure the file is a CSV file.",
        )
    except KeyError:  # error parsing csv
        messages.error(
            request,
            "Something went wrong while parsing your TMDB ratings.",
        )

    return redirect("profile")


@check_demo
def import_tmdb_watchlist(request):
    """View for importing TMDB movie and TV watchlist."""
    try:
        tmdb.importer(
            request.FILES["tmdb_watchlist"],
            request.user,
            status="Planning",
        )
        messages.success(request, "Your TMDB watchlist has been imported!")
    except UnicodeDecodeError:  # when the file is not a CSV file
        messages.error(
            request,
            "Couldn't import your TMDB ratings. Make sure the file is a CSV file.",
        )
    except KeyError:  # error parsing csv
        messages.error(
            request,
            "Something went wrong while parsing your TMDB ratings.",
        )

    return redirect("profile")


@check_demo
def import_anilist(request):
    """View for importing anime and manga data from AniList."""
    try:
        warning_message = anilist.importer(request.POST["anilist"], request.user)
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


@check_demo
def import_yamtrack(request):
    """View for importing anime and manga data from Yamtrack CSV."""
    try:
        yamtrack.importer(request.FILES["yamtrack_csv"], request.user)
        messages.success(request, "Your Yamtrack CSV file has been imported!")
    except UnicodeDecodeError:  # when the file is not a CSV file
        messages.error(
            request,
            "Couldn't import your Yamtrack CSV. Make sure the file is a CSV file.",
        )
    except KeyError:  # error parsing csv
        messages.error(
            request,
            "Something went wrong while parsing your Yamtrack CSV.",
        )

    return redirect("profile")


def export_csv(request):
    """View for exporting all media data to a CSV file."""
    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(
        content_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="yamtrack.csv"'},
    )

    response = exports.db_to_csv(response, request.user)

    logger.info("User %s successfully exported their data", request.user.username)

    return response
