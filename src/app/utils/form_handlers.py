from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

from django.apps import apps
from django.conf import settings

from app import forms
from app.models import Episode, Season
from app.utils import metadata

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


def media_form_handler(
    request: HttpRequest,
    media_metadata: dict | None = None,  # dict or default None
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
    form = forms.get_form_class(media_type)
    try:
        # Try get existing instance of model with given media_id and user
        search_params = {
            "media_id": media_id,
            "user": request.user,
        }
        if season_number is not None:
            search_params["season_number"] = season_number

        instance = model.objects.get(**search_params)
    except model.DoesNotExist:
        # If the model instance doesn't exist, create a new one
        default_params = {
            "user": request.user,
            "title": media_metadata["title"],
            "image": media_metadata["image"],
        }
        if season_number is not None:
            default_params["season_number"] = season_number
        instance = model(**default_params)

    # Validate the form and save the instance if it's valid
    form = form(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        # if season status completed but episode count is < total episodes
        if (
            model == Season
            and form.instance.status == "Completed"
            and form.instance.episodes.count() < len(media_metadata["episodes"])
        ):
            create_remaining_episodes(instance, media_metadata)
    else:
        logger.error(form.errors.as_data())


def media_delete(
    request: HttpRequest,
    media_id: int,
    media_type: str,
    season_number: int | None = None,
) -> None:
    """Delete media data from the database."""

    media_mapping = helpers.media_type_mapper(media_type)
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
        related_season = Season.objects.create(
            media_id=media_id,
            title=season_metadata["title"],
            image=season_metadata["image"],
            score=None,
            status="In progress",
            notes="",
            user=request.user,
            season_number=season_number,
        )

    episode_number = request.POST["episode_number"]
    if "unwatch" in request.POST:
        Episode.objects.filter(
            related_season=related_season,
            episode_number=episode_number,
        ).delete()
    else:

        # create dict of episode number and air date pairs
        # cant get air date with season_metadata["episodes"][episode - 1]["air_date"]
        # because there could be missing or extra episodes in the middle
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


def create_remaining_episodes(instance: Season, season_metadata: dict) -> None:
    """Create remaining episodes for a season based on the season metadata."""
    episodes_to_create = []

    # Get the episode numbers of the episodes already in the database
    episodes_in_db = Episode.objects.filter(related_season=instance).values_list(
        "episode_number",
        flat=True,
    )

    # Create Episode objects for the remaining episodes
    for episode in season_metadata["episodes"]:
        if episode["episode_number"] not in episodes_in_db:
            episode_db = Episode(
                related_season=instance,
                episode_number=episode["episode_number"],
                watch_date=datetime.datetime.now(
                    tz=settings.TZ,
                ).date(),
            )
            episodes_to_create.append(episode_db)

    Episode.objects.bulk_create(episodes_to_create)
