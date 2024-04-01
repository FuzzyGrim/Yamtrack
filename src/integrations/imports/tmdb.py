import contextlib
import csv
import datetime
import logging

from app import forms
from app.providers import services
from django.apps import apps
from django.db import IntegrityError

logger = logging.getLogger(__name__)


def importer(file, user, status):
    """Import movie and TV ratings or watchlist depending on status from TMDB."""
    decoded_file = file.read().decode("utf-8").splitlines()
    reader = csv.DictReader(decoded_file)

    logger.info("Importing from TMDB")

    for row in reader:
        media_type = row["Type"]
        episode_number = row["Episode Number"]
        media_id = row["TMDb ID"]

        # if movie or tv show (not episode), currently cant import episodes
        if media_type == "movie" or (media_type == "tv" and episode_number == ""):
            media_metadata = services.get_media_metadata(media_type, media_id)

            instance = create_instance(media_metadata, user)
            form = create_form(row, instance, media_metadata, status)

            if form.is_valid():
                # ignore if already in database
                with contextlib.suppress(IntegrityError):
                    # not using bulk_create because need of custom save method
                    form.save()

            else:
                error_message = (
                    f"{media_metadata['title']} ({media_type}): Import failed."
                )
                logger.error(error_message)


def create_instance(media_metadata, user):
    """Create instance of media."""
    media_type = media_metadata["media_type"]
    model = apps.get_model(app_label="app", model_name=media_type)

    return model(
        user=user,
        title=media_metadata["title"],
        image=media_metadata["image"],
    )


def create_form(row, instance, media_metadata, status):
    """Create form for media."""
    media_type = media_metadata["media_type"]

    data = {
        "media_id": media_metadata["media_id"],
        "media_type": media_type,
        "score": row["Your Rating"],
        "status": status,
        "repeats": 0,
    }

    if status == "Completed":
        if media_type == "movie":  # tv doesn't have end date
            data["end_date"] = (
                datetime.datetime.strptime(row["Date Rated"], "%Y-%m-%dT%H:%M:%SZ")
                .astimezone()
                .date()
            )
        data["progress"] = media_metadata["max_progress"]

    return forms.get_form_class(media_type)(
        data=data,
        instance=instance,
    )
