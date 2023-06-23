from app.utils import helpers, metadata
from app.models import Season, Episode

import logging

logger = logging.getLogger(__name__)


def media_form_handler(
    request,
    media_id,
    title,
    image,
    media_type,
    season_metadata=None,
    season_number=None,
):
    if media_type == "season" and season_metadata is None and season_number is None:
        season_number = request.POST["season_number"]
        season_metadata = metadata.season(media_id, season_number)

    if "save" in request.POST:
        media_save(request, media_id, title, image, media_type, season_number)
    elif "delete" in request.POST:
        media_delete(request, media_id, media_type, season_number)
    elif "episode_number" in request.POST:
        episode_handler(request, media_id, title, image, season_metadata, season_number)


def media_save(request, media_id, title, image, media_type, season_number=None):
    """
    Saves media data to the database.

    Args:
        request (HttpRequest): The HTTP request object.
        media_id (int): The ID of the media.
        title (str): The title of the media.
        image (str): The image path of the media.
        model (Model): The model to use for saving the media data.
        form (Form): The form to use for validating the media data.
        season_number (int, optional): The season number of the media. Defaults to None.
    """
    model, form = helpers.media_mapper(media_type)
    try:
        # Try to get an existing instance of the model with the given media_id and user
        search_params = {
            "media_id": media_id,
            "user": request.user,
        }
        if season_number is not None:
            search_params["season_number"] = season_number

        instance = model.objects.get(**search_params)
    except model.DoesNotExist:
        # If the model instance doesn't exist, create a new one
        if image != "none.svg":
            image = helpers.download_image(image, "media_type")
        default_params = {
            "user": request.user,
            "title": title,
            "image": image,
        }
        if season_number is not None:
            default_params["season_number"] = season_number
        instance = model(**default_params)

    # Validate the form and save the instance if it's valid
    form = form(request.POST, instance=instance)
    if form.is_valid():
        instance.save()
    else:
        logger.error(form.errors)


def media_delete(request, media_id, media_type, season_number=None):
    """
    Deletes media data from the database.

    Args:
        request (HttpRequest): The HTTP request object.
        media_id (int): The ID of the media.
        model (Model): The model to use for deleting the media data.
        season_number (int, optional): The season number of the media. Defaults to None.
    """
    model, _ = helpers.media_mapper(media_type)
    search_params = {
        "media_id": media_id,
        "user": request.user,
    }
    if season_number is not None:
        search_params["season_number"] = season_number
    try:
        instance = model.objects.get(**search_params)
        instance.delete()
    except model.DoesNotExist:
        logger.error("Instance does not exist")


def episode_handler(request, media_id, title, image, season_metadata, season_number):
    """
    Handles the creation, deletion, and updating of episodes for a given season of a TV show.

    Args:
        request (HttpRequest): The HTTP request object.
        media_id (int): The ID of the TV show.
        title (str): The title of the TV show.
        image (str): The URL of the TV show's image.
        season_metadata (dict): A dictionary containing metadata for the TV show's season.
        season_number (int): The season number of the TV show.
    """

    episodes_checked = request.POST.getlist("episode_number")
    try:
        season_db = Season.objects.get(
            media_id=media_id, user=request.user, season_number=season_number
        )
    except Season.DoesNotExist:
        season_db = Season.objects.create(
            media_id=media_id,
            title=title,
            image=image,
            score=None,
            status="Watching",
            notes="",
            user=request.user,
            season_number=season_number,
        )
    if "unwatch" in request.POST:
        for episode_checked in episodes_checked:
            Episode.objects.filter(season=season_db, number=episode_checked).delete()
    else:
        # convert list of strings to list of ints
        episodes_checked = [int(episode) for episode in episodes_checked]

        episodes_to_create = []
        episodes_to_update = []

        # create dict of episode number and air date pairs
        # cant get air date with season_metadata["episodes"][episode - 1]["air_date"]
        # because there could be missing or extra episodes in the middle
        if "release" in request.POST:
            air_dates = {}
            for episode in season_metadata["episodes"]:
                if episode["episode_number"] in episodes_checked:
                    air_dates[episode["episode_number"]] = episode["air_date"]

        for episode_num in episodes_checked:
            if "release" in request.POST:
                # set watch date to air date
                # air date could be null
                watch_date = air_dates.get(episode_num)
            else:
                # set watch date from form
                watch_date = request.POST.get("date")

            # update episode watch date
            try:
                episode_db = Episode.objects.get(
                    tv_season=season_db, episode_number=episode_num
                )
                episode_db.watch_date = watch_date
                episodes_to_update.append(episode_db)
            # create new episode if it doesn't exist
            except Episode.DoesNotExist:
                episode_db = Episode(
                    tv_season=season_db,
                    episode_number=episode_num,
                    watch_date=watch_date,
                )
                episodes_to_create.append(episode_db)

        Episode.objects.bulk_create(episodes_to_create)
        Episode.objects.bulk_update(episodes_to_update, ["watch_date"])

        # if all episodes are watched, set season status to completed
        if season_db.progress == len(season_metadata["episodes"]):
            season_db.status = "Completed"
            season_db.save()
