import csv
import logging

from django.apps import apps
from django.db.models import Field

import app
from app import helpers
from app.models import TV, Anime, Episode, Game, Item, Manga, Movie, Season

logger = logging.getLogger(__name__)


def db_to_csv(response, user):
    """Export a CSV file of the user's media."""
    fields = {
        "item": get_model_fields(Item),
        "track": get_track_fields(),
    }

    writer = csv.writer(response, quoting=csv.QUOTE_ALL)
    writer.writerow(fields["item"] + fields["track"])

    write_model_to_csv(writer, fields, Movie.objects.filter(user=user), "movie")
    write_model_to_csv(writer, fields, TV.objects.filter(user=user), "tv")
    write_model_to_csv(writer, fields, Season.objects.filter(user=user), "season")
    write_model_to_csv(
        writer,
        fields,
        Episode.objects.filter(related_season__user=user),
        "episode",
    )
    write_model_to_csv(writer, fields, Anime.objects.filter(user=user), "anime")
    write_model_to_csv(writer, fields, Manga.objects.filter(user=user), "manga")
    write_model_to_csv(writer, fields, Game.objects.filter(user=user), "game")

    return response


def get_model_fields(model):
    """Get a list of fields names from a model."""
    return [
        field.name
        for field in model._meta.get_fields()  # noqa: SLF001
        if isinstance(field, Field) and not field.auto_created and not field.is_relation
    ]


def get_track_fields():
    """Get a list of all track fields from all media models."""
    all_fields = []

    for media_type in app.models.MEDIA_TYPES:
        model = apps.get_model("app", media_type)
        for field in get_model_fields(model):
            if field not in all_fields:
                all_fields.append(field)
    return list(all_fields)


def write_model_to_csv(writer, fields, queryset, media_type):
    """Export entries from a model to a CSV file."""
    logger.info("Adding %ss to CSV", media_type)

    for media in queryset:
        # row with item and track fields
        row = [getattr(media.item, field, "") for field in fields["item"]] + [
            getattr(media, field, "") for field in fields["track"]
        ]

        if media_type == "game":
            # calculate index of progress field
            progress_index = fields["track"].index("progress")
            row[progress_index + len(fields["item"])] = helpers.minutes_to_hhmm(
                media.progress,
            )

        writer.writerow(row)
