import csv
import datetime
import logging

from app import forms
from app.forms import MovieForm, SeasonForm, TVForm
from app.models import TV, Movie, Season
from app.providers import services
from django.apps import apps
from django.core.files.uploadedfile import InMemoryUploadedFile
from users.models import User

logger = logging.getLogger(__name__)


def tmdb_data(file: InMemoryUploadedFile, user: User, status: str) -> None:
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
            model = apps.get_model(app_label="app", model_name=media_type)

            # only import if not already in database
            if not model.objects.filter(user=user, media_id=media_id).exists():
                instance = create_instance(media_metadata, user)
                form = create_form(row, instance, media_metadata, status)

                if form.is_valid():
                    # not using bulk_create because need of custom save method
                    form.save()

                else:
                    error_message = (
                        f"{media_metadata['title']} ({media_type}): Import failed."
                    )
                    logger.error(error_message)


def create_instance(
    media_metadata: dict,
    user: User,
) -> TV | Season | Movie:
    """Create instance of media."""

    media_type = media_metadata["media_type"]
    model = apps.get_model(app_label="app", model_name=media_type)

    instance = model(
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
    status: str,
) -> TVForm | SeasonForm | MovieForm:
    """Create form for media."""

    media_type = media_metadata["media_type"]

    data = {
        "media_id": media_metadata["media_id"],
        "media_type": media_type,
        "score": row["Your Rating"],
        "status": status,
    }

    if status == "Completed":
        if media_type == "movie":  # tv doesn't have end date
            data["end_date"] = (
                datetime.datetime.strptime(row["Date Rated"], "%Y-%m-%dT%H:%M:%SZ")
                .astimezone()
                .date()
            )
        data["progress"] = media_metadata["details"]["number_of_episodes"]

    return forms.get_form_class(media_type)(
        data=data,
        instance=instance,
    )
