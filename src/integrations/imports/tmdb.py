import csv
import datetime
import logging

from django.apps import apps
from django.conf import settings
from django.db import IntegrityError

from app import models
from app.providers import services

logger = logging.getLogger(__name__)


def importer(file, user, status):
    """Import movie and TV ratings or watchlist depending on status from TMDB."""
    decoded_file = file.read().decode("utf-8").splitlines()
    reader = csv.DictReader(decoded_file)

    logger.info("Importing from TMDB")

    num_imported = {"tv": 0, "movie": 0}

    for row in reader:
        media_type = row["Type"]
        episode_number = row["Episode Number"]
        media_id = row["TMDb ID"]

        # if movie or tv show (not episode)
        if media_type == "movie" or (media_type == "tv" and episode_number == ""):
            media_metadata = services.get_media_metadata(media_type, media_id, "tmdb")

            item, _ = models.Item.objects.get_or_create(
                media_id=media_metadata["media_id"],
                source="tmdb",
                media_type=media_type,
                defaults={
                    "title": media_metadata["title"],
                    "image": media_metadata["image"],
                },
            )

            model = apps.get_model(app_label="app", model_name=media_type)

            # watchlist has no rating
            score = row["Your Rating"] if row["Your Rating"] else None

            instance = model(
                item=item,
                user=user,
                score=score,
                status=status,
            )

            if status == "Completed" and media_type == "movie":
                instance.end_date = (
                    datetime.datetime.strptime(
                        row["Date Rated"],
                        "%Y-%m-%dT%H:%M:%SZ",
                    )
                    .replace(tzinfo=datetime.UTC)
                    .astimezone(settings.TZ)
                    .date()
                )
                instance.progress = media_metadata["max_progress"]

            try:
                instance.save()
                num_imported[media_type] += 1
            except IntegrityError:
                msg = f"Failed to import {media_type} {media_id}, already exists"
                logger.exception(msg)

    logger.info(
        "Imported %s TV and %s movies",
        num_imported["tv"],
        num_imported["movie"],
    )
    return num_imported["tv"], num_imported["movie"]
