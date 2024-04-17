import logging
from csv import DictReader

from app import forms
from app.forms import EpisodeForm
from app.models import TV, Episode, Season
from django.apps import apps
from simple_history.utils import bulk_create_with_history

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

    for row in reader:
        media_type = row["media_type"]
        if media_type == "episode":
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
                logger.error(form.errors.as_data())
        else:
            add_bulk_media(row, user, bulk_media)

    for media_type in ["anime", "manga", "movie", "tv", "game"]:
        if bulk_media[media_type]:
            model = apps.get_model(app_label="app", model_name=media_type)
            bulk_create_with_history(
                bulk_media[media_type],
                model,
                ignore_conflicts=True,
            )

    if bulk_media["season"]:
        # Extract unique media IDs from the seasons
        unique_media_ids = {season.media_id for season in bulk_media["season"]}

        # Fetch TV for all unique media IDs
        tv_objects = TV.objects.filter(media_id__in=unique_media_ids, user=user)

        # Create a mapping from media ID to TV
        tv_mapping = {tv.media_id: tv for tv in tv_objects}

        # Assign related_tv to each season using the mapping
        for season in bulk_media["season"]:
            season.related_tv = tv_mapping[season.media_id]

        bulk_create_with_history(
            bulk_media["season"],
            Season,
            ignore_conflicts=True,
        )

    if bulk_media["episodes"]:
        # Extract unique media IDs and season numbers from the episodes
        unique_season_keys = {
            (episode["media_id"], episode["season_number"])
            for episode in bulk_media["episodes"]
        }

        # Fetch Season objects for all unique combinations of media ID and season number
        season_objects = Season.objects.filter(
            user=user,
            media_id__in=[key[0] for key in unique_season_keys],
            season_number__in=[key[1] for key in unique_season_keys],
        )

        # Create a mapping from (media_id, season_number) tuple to Season
        season_mapping = {
            (season.media_id, season.season_number): season for season in season_objects
        }

        # Assign related_season to each episode using the mapping
        for episode in bulk_media["episodes"]:
            season_key = (episode["media_id"], episode["season_number"])
            episode["instance"].related_season = season_mapping[season_key]

        episode_instances = [episode["instance"] for episode in bulk_media["episodes"]]
        bulk_create_with_history(
            episode_instances,
            Episode,
            ignore_conflicts=True,
        )


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
        logger.error("Error importing %s: %s", row["title"], form.errors.as_data())
