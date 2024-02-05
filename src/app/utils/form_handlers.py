from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from django.apps import apps
from django.conf import settings
from django.db.models import Max

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
            try:
                related_tv = TV.objects.get(media_id=media_id, user=request.user)
            except TV.DoesNotExist:
                tv_metadata = metadata.tv(media_id)
                related_tv = TV.objects.create(
                    title=tv_metadata["title"],
                    image=tv_metadata["image"],
                    score=None,
                    status=request.POST.get("status"),
                    notes="",
                    user=request.user,
                    media_id=media_id,
                )
            default_params["season_number"] = season_number
            default_params["related_tv"] = related_tv

        instance = model(**default_params)

    # Validate the form and save the instance if it's valid
    form_class = forms.get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        # if tv status completed but episode count is < total episodes
        if (
            model == TV
            and form.instance.status == "Completed"
            and form.instance.progress < media_metadata["number_of_episodes"]
        ):
            create_remaining_seasons(request, media_id, media_metadata, form.instance)
        # if season status completed but episode count is < total episodes
        elif (
            model == Season
            and form.instance.status == "Completed"
            and form.instance.progress
            < len(
                media_metadata["episodes"],
            )
        ):
            Episode.objects.bulk_create(remaining_ep_list(instance, media_metadata))
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
        instance = model.objects.get(**search_params)
        instance.delete()
    except model.DoesNotExist:
        logger.exception("Instance does not exist")


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

        try:
            related_tv = TV.objects.get(media_id=media_id, user=request.user)
        except TV.DoesNotExist:
            tv_metadata = metadata.tv(media_id)

            related_tv = TV.objects.create(
                media_id=media_id,
                title=tv_metadata["title"],
                image=tv_metadata["image"],
                score=None,
                status="In progress",
                notes="",
                user=request.user,
            )

        related_season = Season.objects.create(
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

    episode_number = request.POST["episode_number"]
    if "unwatch" in request.POST:
        Episode.objects.filter(
            related_season=related_season,
            episode_number=episode_number,
        ).delete()

        if related_season.status == "Completed":
            related_season.status = "In progress"
            related_season.save()
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

        # if all episodes are watched, set season status to completed
        if related_season.progress == len(season_metadata["episodes"]):
            related_season.status = "Completed"
            related_season.save()


def create_remaining_seasons(
    request: HttpRequest, media_id: int, media_metadata: dict, instance: TV
) -> None:
    """Create remaining seasons and episodes for a TV show."""

    seasons_to_create = []
    episodes_to_create = []
    for season_number in range(1, media_metadata["number_of_seasons"]):
        season_metadata = metadata.season(media_id, season_number)
        try:
            season_instance = Season.objects.get(
                media_id=media_id,
                user=request.user,
                season_number=season_number,
            )
        except Season.DoesNotExist:
            season_instance = Season(
                media_id=media_id,
                title=media_metadata["title"],
                image=season_metadata["image"],
                score=None,
                status="Completed",
                notes="",
                season_number=season_number,
                related_tv=instance,
                user=request.user,
            )
            seasons_to_create.append(season_instance)
        episodes_to_create.extend(
            remaining_ep_list(season_instance, season_metadata),
        )

    Season.objects.bulk_create(seasons_to_create)
    Episode.objects.bulk_create(episodes_to_create)


def remaining_ep_list(instance: Season, season_metadata: dict) -> None:
    """Return remaining episodes to complete for a season."""

    # Get the maximum episode number already in the database
    max_episode_number = Episode.objects.filter(related_season=instance).aggregate(
        max_episode_number=Max("episode_number"),
    )["max_episode_number"]

    if max_episode_number is None:
        max_episode_number = 0

    # Initialize the list to store new episodes
    episodes_to_create = []

    # Create Episode objects for the remaining episodes
    for episode in season_metadata["episodes"]:
        if episode["episode_number"] > max_episode_number:
            episode_db = Episode(
                related_season=instance,
                episode_number=episode["episode_number"],
                watch_date=datetime.datetime.now(tz=settings.TZ).date(),
            )
            episodes_to_create.append(episode_db)

    return episodes_to_create
