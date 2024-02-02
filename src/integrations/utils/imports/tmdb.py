from __future__ import annotations

import datetime
import logging
from csv import DictReader
from typing import TYPE_CHECKING

from app import forms
from app.utils import helpers, metadata

if TYPE_CHECKING:
    from app.forms import MovieForm, SeasonForm, TVForm
    from app.models import TV, Movie, Season
    from django.core.files.uploadedfile import InMemoryUploadedFile
    from users.models import User


logger = logging.getLogger(__name__)


def tmdb_data(file: InMemoryUploadedFile, user: User, status: str) -> None:
    """Import movie and TV ratings or watchlist depending on status from TMDB."""

    if not file.name.endswith(".csv"):
        error = "Invalid file format. Please upload a CSV file."
        logger.error(error)
        raise ValueError(error)

    logger.info("Importing from TMDB")

    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    bulk_media = {
        "movie": [],
        "tv": [],
        "season": [],
    }

    for row in reader:
        media_type = row["Type"]
        episode_number = row["Episode Number"]
        media_id = row["TMDb ID"]

        # if movie or tv show (not episode), currently cant import episodes
        if media_type == "movie" or (media_type == "tv" and episode_number == ""):
            media_metadata = metadata.get_media_metadata(media_type, media_id)

            # if tv show watchlist, add first season as planning
            if media_type == "tv" and status == "Planning":
                media_type = "season"

                # get title and id from tv metadata as it's not in season metadata
                tv_title = media_metadata["title"]
                media_id = media_metadata["id"]

                media_metadata = metadata.season(media_id, season_number=1)
                media_metadata["media_type"] = "season"
                media_metadata["title"] = tv_title
                media_metadata["id"] = media_id

            media_mapping = helpers.media_type_mapper(media_type)
            instance = create_instance(media_metadata, media_mapping, user)
            form = create_form(row, instance, media_metadata, media_mapping, status)

            if form.is_valid():
                bulk_media[media_type].append(form.instance)
            else:
                error_message = f"Error importing {media_metadata['title']}: {form.errors.as_data()}"
                logger.error(error_message)

    # bulk create tv, seasons and movie
    for media_type, medias in bulk_media.items():
        model_type = helpers.media_type_mapper(media_type)["model"]

        model_type.objects.bulk_create(medias, ignore_conflicts=True)
        logger.info("Imported %s %ss", len(medias), media_type)


def create_instance(
    media_metadata: dict,
    media_mapping: dict,
    user: User,
) -> TV | Season | Movie:
    """Create instance of media."""

    media_type = media_metadata["media_type"]

    instance = media_mapping["model"](
        user=user,
        title=media_metadata["title"],
        image=media_metadata["image"],
    )

    # if tv watchlist, create first season
    if media_type == "season":
        instance.season_number = 1

    return instance


def create_form(
    row: dict,
    instance: TV | Movie,
    media_metadata: dict,
    media_mapping: dict,
    status: str,
) -> TVForm | SeasonForm | MovieForm:
    """Create form for media."""

    media_type = media_metadata["media_type"]

    data = {
        "media_id": media_metadata["id"],
        "media_type": media_type,
        "score": row["Your Rating"],
    }
    if media_type == "movie":
        data["status"] = status
        data["end_date"] = (
            datetime.datetime.strptime(row["Date Rated"], "%Y-%m-%dT%H:%M:%SZ")
            .astimezone()
            .date()
        )

    # if tv watchlist, add first season as planning
    if media_type == "season":
        data["status"] = "Planning"
        data["season_number"] = 1

    return forms.get_form_class(media_type)(
        data=data,
        instance=instance,
        post_processing=False,
    )
