from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.apps import apps

from app import forms
from app.models import TV, Episode, Season
from app.utils import metadata

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


def media_form_handler(
    request: HttpRequest,
    media_metadata: dict | None = None,
    season_number: int | None = None,
    title: str | None = None,
) -> None:
    """Handle the creation, updating and deletion of media."""

    media_id = request.POST["media_id"]
    media_type = request.POST.get("media_type")

    # when editing from home, medialist and media details
    if media_type == "season":
        if season_number is None:
            season_number = request.POST["season_number"]
        if media_metadata is None:
            media_metadata = metadata.season(media_id, season_number)
            media_metadata["title"] = title

    # if not season and media_metadata is None:
    elif media_metadata is None:
        media_metadata = metadata.get_media_metadata(media_type, media_id)

    if "save" in request.POST:
        media_save(request, media_id, media_type, media_metadata, season_number)
    elif "delete" in request.POST:
        media_delete(request, media_id, media_type, season_number)
    elif "episode_number" in request.POST:
        episode_form_handler(request, media_id, media_metadata, season_number)


def media_save(
    request: HttpRequest,
    media_id: int,
    media_type: str,
    media_metadata: dict,
    season_number: int | None = None,
) -> None:
    """Save or update media data to the database."""

    model = apps.get_model(app_label="app", model_name=media_type)

    try:
        search_params = {
            "media_id": media_id,
            "user": request.user,
        }

        if media_type == "season":
            search_params["season_number"] = season_number

        instance = model.objects.get(**search_params)
    except model.DoesNotExist:

        default_params = {
            "title": media_metadata["title"],
            "image": media_metadata["image"],
            "user": request.user,
        }
        if media_type == "season":
            related_tv = get_related_tv(request, media_id)
            default_params["season_number"] = season_number
            default_params["related_tv"] = related_tv

        instance = model(**default_params)

    # Validate the form and save the instance if it's valid
    form_class = forms.get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        logger.info("%s saved successfully.", form.instance)
    else:
        logger.error(form.errors.as_data())


def media_delete(
    request: HttpRequest,
    media_id: int,
    media_type: str,
    season_number: int | None = None,
) -> None:
    """Delete media data from the database."""

    search_params = {
        "media_id": media_id,
        "user": request.user,
    }

    if season_number is not None:
        search_params["season_number"] = season_number

    model = apps.get_model(app_label="app", model_name=media_type)
    try:
        model.objects.get(**search_params).delete()
        logger.info("%s deleted successfully.", media_type)
    except model.DoesNotExist:
        logger.exception("The %s was already deleted before.", media_type)


def episode_form_handler(
    request: HttpRequest,
    media_id: int,
    season_metadata: dict,
    season_number: int,
) -> None:
    """Handle the creation, deletion, and updating of episodes for a season."""

    try:
        related_season = Season.objects.get(
            media_id=media_id,
            user=request.user,
            season_number=season_number,
        )
    except Season.DoesNotExist:
        related_tv = get_related_tv(request, media_id)
        related_season = Season(
            media_id=media_id,
            title=season_metadata["title"],
            image=season_metadata["image"],
            score=None,
            status="In progress",
            notes="",
            user=request.user,
            season_number=season_number,
            related_tv=related_tv,
        )
        # save_base to avoid custom save method
        Season.save_base(related_season)
        logger.info("%s did not exist, it was created successfully.", related_season)

    episode_number = request.POST["episode_number"]
    if "unwatch" in request.POST:
        Episode.objects.filter(
            related_season=related_season,
            episode_number=episode_number,
        ).delete()

        logger.info("%s %s deleted successfully.", related_season, episode_number)

        if related_season.status == "Completed":
            related_season.status = "In progress"
            # save_base to avoid custom save method
            related_season.save_base(update_fields=["status"])
    else:

        if "release" in request.POST:
            watch_date = request.POST.get("release")
        else:
            # set watch date from form
            watch_date = request.POST.get("date")

        Episode.objects.update_or_create(
            related_season=related_season,
            episode_number=episode_number,
            defaults={
                "watch_date": watch_date,
            },
        )
        logger.info("%s %s saved successfully.", related_season, episode_number)


def get_related_tv(request: HttpRequest, media_id: int) -> TV:
    """Get related TV instance for a season or create it if it doesn't exist."""
    try:
        related_tv = TV.objects.get(media_id=media_id, user=request.user)
    except TV.DoesNotExist:
        tv_metadata = metadata.tv(media_id)

        # default to in progress for when handling episode form
        status = request.POST.get("status", "In progress")
        # creating tv with multiple seasons from a completed season
        if status == "Completed" and tv_metadata["season_number"] > 1:
            status = "In progress"

        related_tv = TV(
            media_id=media_id,
            title=tv_metadata["title"],
            image=tv_metadata["image"],
            score=None,
            status=status,
            notes="",
            user=request.user,
        )

        # save_base to avoid custom save method
        TV.save_base(related_tv)
        logger.info("%s did not exist, it was created successfully.", related_tv)

    return related_tv
