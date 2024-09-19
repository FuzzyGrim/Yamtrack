import logging
from csv import DictReader

import app
from app.models import TV, Episode, Season
from django.apps import apps

from integrations import helpers

logger = logging.getLogger(__name__)


def importer(file, user):
    """Import media from CSV file."""
    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    logger.info("Importing from Yamtrack")

    bulk_media = {media_type: [] for media_type in app.models.MEDIA_TYPES}

    imported_counts = {}

    for row in reader:
        add_bulk_media(row, user, bulk_media)

    for media_type in app.models.MEDIA_TYPES:
        imported_counts[media_type] = import_media(
            media_type,
            bulk_media[media_type],
            user,
        )

    return imported_counts


def add_bulk_media(row, user, bulk_media):
    """Add media to list for bulk creation."""
    media_type = row["media_type"]

    season_number = row["season_number"] if row["season_number"] != "" else None
    episode_number = row["episode_number"] if row["episode_number"] != "" else None

    item, _ = app.models.Item.objects.get_or_create(
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
    if media_type != "episode":  # episode has no user field
        instance.user = user

    row["item"] = item
    form = app.forms.get_form_class(media_type)(
        row,
        instance=instance,
    )

    if form.is_valid():
        bulk_media[media_type].append(form.instance)
    else:
        logger.error("Error importing %s: %s", row["title"], form.errors.as_json())


def import_media(media_type, bulk_data, user):
    """Import media and return number of imported objects."""
    if media_type == "season":
        return import_seasons(bulk_data, user)
    if media_type == "episode":
        return import_episodes(bulk_data, user)

    model = apps.get_model(app_label="app", model_name=media_type)

    num_objects_before = model.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_data, model, user)
    num_objects_after = model.objects.filter(user=user).count()
    return num_objects_after - num_objects_before


def import_seasons(bulk_data, user):
    """Import seasons and return number of imported objects."""
    unique_media_ids = {season.item.media_id for season in bulk_data}
    tv_objects = TV.objects.filter(item__media_id__in=unique_media_ids, user=user)
    tv_mapping = {tv.item.media_id: tv for tv in tv_objects}

    for season in bulk_data:
        season.related_tv = tv_mapping[season.item.media_id]

    num_seasons_before = Season.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_data, Season, user)
    num_seasons_after = Season.objects.filter(user=user).count()
    return num_seasons_after - num_seasons_before


def import_episodes(bulk_data, user):
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
        season_key = (episode.item.media_id, episode.item.season_number)
        episode.related_season = season_mapping[season_key]

    num_episodes_before = Episode.objects.filter(related_season__user=user).count()
    helpers.bulk_chunk_import(bulk_data, Episode, user)
    num_episodes_after = Episode.objects.filter(related_season__user=user).count()
    return num_episodes_after - num_episodes_before
