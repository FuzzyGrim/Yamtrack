from app.utils import helpers, metadata
from app.exceptions import ImportSourceError

from django.core.exceptions import ValidationError
from decouple import config
from csv import DictReader

import datetime
import logging
import asyncio

TMDB_API = config("TMDB_API", default="")
logger = logging.getLogger(__name__)


def import_tmdb_watchlist(file, user):
    status = "Planning"
    tmdb_importer(file, user, status)


def import_tmdb_ratings(file, user):
    status = "Completed"
    tmdb_importer(file, user, status)


def tmdb_importer(file, user, status):
    if not file.name.endswith(".csv"):
        logger.error("File must be a CSV file")
        raise ImportSourceError("File must be a CSV file")

    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    bulk_media = {
        "movie": [],
        "tv": [],
        "season": [],
    }

    bulk_images = {
        "movie": [],
        "tv": [],
        "season": [],
    }

    for row in reader:
        media_type = row["Type"]
        episode_number = row["Episode Number"]
        media_id = row["TMDb ID"]

        # if movie or tv show (not episode)
        if media_type == "movie" or (media_type == "tv" and episode_number == ""):
            media_mapping = helpers.media_type_mapper(media_type)

            media_metadata = metadata.get_media_metadata(media_type, media_id)

            # if tv show watchlist, add first season as planning
            if media_type == "tv" and status == "Planning":
                # get title from tv show metadata as it's not available in season metadata
                title = media_metadata["title"]
                media_metadata = metadata.season(media_id, season_number=1)
                media_metadata["media_type"] = "season"
                media_metadata["title"] = title

            try:
                add_bulk_media(
                    row, media_metadata, media_mapping, status, user, bulk_media
                )
                if media_metadata["image"] != "none.svg":
                    bulk_images[media_type].append(media_metadata["image"])
            except ValidationError as error:
                logger.error(error)

    # download images
    for media_type, images in bulk_images.items():
        asyncio.run(helpers.images_downloader(images, media_type))

    # bulk create tv, seasons and movie
    for media_type, medias in bulk_media.items():
        model_type = helpers.media_type_mapper(media_type)["model"]
        model_type.objects.bulk_create(medias)


def add_bulk_media(row, media_metadata, media_mapping, status, user, bulk_media):
    media_type = media_metadata["media_type"]

    instance = media_mapping["model"](
        user=user,
        title=media_metadata["title"],
        image=helpers.get_image_filename(media_metadata["image"], media_type),
    )

    data = {
        "media_id": media_metadata["id"],
        "media_type": media_type,
        "score": row["Your Rating"],
    }
    if media_type == "movie":
        data["status"] = status
        data["end_date"] = datetime.datetime.strptime(
            row["Date Rated"], "%Y-%m-%dT%H:%M:%SZ"
        ).date()

    # if tv watchlist, add first season as planning
    if media_type == "season":
        instance.status = "Planning"
        instance.season_number = 1

    form = media_mapping["form"](
        data=data,
        instance=instance,
        post_processing=False,
    )

    if form.is_valid():
        bulk_media[media_type].append(form.instance)
    else:
        error_message = (
            f"Error importing {media_metadata['title']}: {form.errors.as_data()}"
        )
        logger.error(error_message)
