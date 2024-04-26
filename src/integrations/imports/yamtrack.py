import logging
from csv import DictReader

from app import forms
from app.forms import EpisodeForm
from app.models import TV, Episode, Season
from django.apps import apps

from integrations import helpers

logger = logging.getLogger(__name__)


def importer(file, user):
    """Import media from CSV file."""
    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    logger.info("Importing from Yamtrack")

    bulk_media = {
        "anime": [],
        "manga": [],
        "movie": [],
        "tv": [],
        "season": [],
        "episodes": [],
        "game": [],
    }

    imported_counts = {}

    for row in reader:
        media_type = row["media_type"]
        if media_type == "episode":
            process_episode(row, bulk_media)
        else:
            add_bulk_media(row, user, bulk_media)

    for media_type in ["anime", "manga", "movie", "game", "tv"]:
        imported_counts[media_type] = import_media(
            media_type,
            bulk_media[media_type],
            user,
        )

    imported_counts["season"] = import_seasons(bulk_media["season"], user)
    imported_counts["episode"] = import_episodes(bulk_media["episodes"], user)

    return imported_counts


def process_episode(row, bulk_media):
    """Process episode data."""
    form = EpisodeForm(row)
    if form.is_valid():
        # media_id and season_number needed for later fetching related season
        bulk_media["episodes"].append(
            {
                "instance": form.instance,
                "media_id": int(row["media_id"]),
                "season_number": int(row["season_number"]),
            },
        )
    else:
        logger.error(form.errors.as_json())


def add_bulk_media(row, user, bulk_media):
    """Add media to list for bulk creation."""
    media_type = row["media_type"]
    model = apps.get_model(app_label="app", model_name=media_type)
    instance = model(user=user, title=row["title"], image=row["image"])

    if media_type == "season":
        instance.season_number = row["season_number"]

    form = forms.get_form_class(media_type)(
        row,
        instance=instance,
        initial={"media_type": media_type},
    )

    if form.is_valid():
        bulk_media[media_type].append(form.instance)
    else:
        logger.error("Error importing %s: %s", row["title"], form.errors.as_json())


def import_media(media_type, bulk_data, user):
    """Import media and return number of imported objects."""
    model = apps.get_model(app_label="app", model_name=media_type)

    num_objects_before = model.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_data, model)
    num_objects_after = model.objects.filter(user=user).count()
    return num_objects_after - num_objects_before


def import_seasons(bulk_data, user):
    """Import seasons and return number of imported objects."""
    unique_media_ids = {season.media_id for season in bulk_data}
    tv_objects = TV.objects.filter(media_id__in=unique_media_ids, user=user)
    tv_mapping = {tv.media_id: tv for tv in tv_objects}

    for season in bulk_data:
        season.related_tv = tv_mapping[season.media_id]

    num_seasons_before = Season.objects.filter(user=user).count()
    helpers.bulk_chunk_import(bulk_data, Season)
    num_seasons_after = Season.objects.filter(user=user).count()
    return num_seasons_after - num_seasons_before


def import_episodes(bulk_data, user):
    """Import episodes and return number of imported objects."""
    unique_season_keys = {
        (episode["media_id"], episode["season_number"]) for episode in bulk_data
    }

    season_objects = Season.objects.filter(
        user=user,
        media_id__in=[key[0] for key in unique_season_keys],
        season_number__in=[key[1] for key in unique_season_keys],
    )

    season_mapping = {
        (season.media_id, season.season_number): season for season in season_objects
    }

    for episode in bulk_data:
        season_key = (episode["media_id"], episode["season_number"])
        episode["instance"].related_season = season_mapping[season_key]

    episode_instances = [episode["instance"] for episode in bulk_data]

    num_episodes_before = Episode.objects.filter(related_season__user=user).count()
    helpers.bulk_chunk_import(episode_instances, Episode)
    num_episodes_after = Episode.objects.filter(related_season__user=user).count()
    return num_episodes_after - num_episodes_before
