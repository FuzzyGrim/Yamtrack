import logging
from csv import DictReader

import requests
from django.apps import apps
from django.conf import settings

import app
import app.providers
from app.models import TV, Episode, MediaTypes, Season, Sources
from integrations import helpers
from integrations.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(file, user, mode):
    """Import media from CSV file."""
    logger.info("Starting Yamtrack import with mode %s", mode)

    try:
        decoded_file = file.read().decode("utf-8").splitlines()
    except UnicodeDecodeError as e:
        msg = "Invalid file format. Please upload a CSV file."
        raise MediaImportError(msg) from e

    reader = DictReader(decoded_file)

    bulk_media = {media_type: [] for media_type in MediaTypes.values}
    imported_counts = {}
    warnings = []

    for row in reader:
        try:
            add_bulk_media(row, user, bulk_media, warnings)
        except Exception as error:
            error_msg = f"Error processing entry: {row}"
            raise MediaImportUnexpectedError(error_msg) from error

    for media_type in MediaTypes.values:
        imported_counts[media_type] = import_media(
            media_type,
            bulk_media[media_type],
            user,
            mode,
        )

    return imported_counts, "\n".join(warnings) if warnings else None


def add_bulk_media(row, user, bulk_media, warnings):
    """Add media to list for bulk creation."""
    media_type = row["media_type"]

    season_number = row["season_number"] if row["season_number"] != "" else None
    episode_number = row["episode_number"] if row["episode_number"] != "" else None

    if row["progress"] == "":
        row["progress"] = 0
    if row["repeats"] == "":
        row["repeats"] = 0

    if row["title"] == "" or row["image"] == "":
        if row["source"] == Sources.MANUAL.value and row["image"] == "":
            row["image"] = settings.IMG_NONE
        else:
            try:
                metadata = app.providers.services.get_media_metadata(
                    media_type,
                    row["media_id"],
                    row["source"],
                    season_number,
                    episode_number,
                )
                row["title"] = metadata["title"]
                row["image"] = metadata["image"]
            except requests.exceptions.HTTPError as e:
                warnings.append(
                    f"Failed to fetch metadata for {row['media_id']}: {e!s}",
                )
                return

    item, _ = app.models.Item.objects.update_or_create(
        media_id=row["media_id"],
        source=row["source"],
        media_type=media_type,
        season_number=season_number,
        episode_number=episode_number,
        defaults={
            "title": row["title"],
            "image": row["image"],
        },
    )

    model = apps.get_model(app_label="app", model_name=media_type)
    instance = model(item=item)
    if media_type != MediaTypes.EPISODE.value:  # episode has no user field
        instance.user = user

    row["item"] = item
    form = app.forms.get_form_class(media_type)(
        row,
        instance=instance,
    )

    if form.is_valid():
        bulk_media[media_type].append(form.instance)
    else:
        error_msg = f"{row['title']} ({media_type}): {form.errors.as_json()}"
        warnings.append(error_msg)
        logger.error(error_msg)


def import_media(media_type, bulk_data, user, mode):
    """Import media and return number of imported objects."""
    if media_type == MediaTypes.SEASON.value:
        return import_seasons(bulk_data, user, mode)
    if media_type == MediaTypes.EPISODE.value:
        return import_episodes(bulk_data, user, mode)

    model = apps.get_model(app_label="app", model_name=media_type)

    return helpers.bulk_chunk_import(bulk_data, model, user, mode)


def import_seasons(bulk_data, user, mode):
    """Import seasons and return number of imported objects."""
    unique_media_ids = {season.item.media_id for season in bulk_data}
    tv_objects = TV.objects.filter(item__media_id__in=unique_media_ids, user=user)
    tv_mapping = {tv.item.media_id: tv for tv in tv_objects}

    for season in bulk_data:
        season.related_tv = tv_mapping[season.item.media_id]

    return helpers.bulk_chunk_import(bulk_data, Season, user, mode)


def import_episodes(bulk_data, user, mode):
    """Import episodes and return number of imported objects."""
    unique_season_keys = {
        (episode.item.media_id, episode.item.season_number) for episode in bulk_data
    }

    season_objects = Season.objects.filter(
        user=user,
        item__media_id__in=[key[0] for key in unique_season_keys],
        item__season_number__in=[key[1] for key in unique_season_keys],
    )

    season_mapping = {
        (season.item.media_id, season.item.season_number): season
        for season in season_objects
    }

    for episode in bulk_data:
        season_key = (episode.item.media_id, int(episode.item.season_number))
        episode.related_season = season_mapping[season_key]

    return helpers.bulk_chunk_import(bulk_data, Episode, user, mode)
