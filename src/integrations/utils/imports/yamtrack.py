import asyncio
import logging
from csv import DictReader

from app.forms import EpisodeForm
from app.models import Episode, Season
from app.utils import helpers
from django.core.files.uploadedfile import InMemoryUploadedFile
from users.models import User

logger = logging.getLogger(__name__)


def yamtrack_data(file: InMemoryUploadedFile, user: User) -> None:
    """Import media from CSV file."""

    if not file.name.endswith(".csv"):
        logger.error("File must be a CSV file")
        raise ValueError(  # noqa: TRY003
            "Invalid file format. Please upload a CSV file.",  # noqa: EM101
        )

    logger.info("Importing from Yamtrack")

    decoded_file = file.read().decode("utf-8").splitlines()
    reader = DictReader(decoded_file)

    bulk_media = {
        "anime": [],
        "manga": [],
        "movie": [],
        "tv": [],
        "season": [],
    }

    bulk_images = {
        "anime": [],
        "manga": [],
        "movie": [],
        "tv": [],
        "season": [],
    }

    episodes = []

    for row in reader:
        media_type = row["media_type"]
        if media_type == "episode":
            form = EpisodeForm(row)
            if form.is_valid():
                episodes.append(
                    {
                        "instance": form.instance,
                        "media_id": row["media_id"],
                        "season_number": row["season_number"],
                    },
                )
            else:
                logger.error(form.errors.as_data())
        else:
            media_mapping = helpers.media_type_mapper(media_type)
            add_bulk_media(row, media_mapping, user, bulk_media)

            if row["image"] != "none.svg":
                add_bulk_image(row, media_mapping, bulk_images)

    # download images
    for media_type, images in bulk_images.items():
        asyncio.run(helpers.images_downloader(images, media_type))

    # bulk create tv, season, movie, anime and manga
    for media_type, medias in bulk_media.items():
        model_type = helpers.media_type_mapper(media_type)["model"]
        model_type.objects.bulk_create(medias, ignore_conflicts=True)

        logger.info("Imported %s %ss", len(medias), media_type)

    if episodes:
        # bulk create episodes
        for episode in episodes:
            media_id = episode["media_id"]
            season_number = episode["season_number"]
            episode["instance"].related_season = Season.objects.get(
                media_id=media_id,
                season_number=season_number,
                user=user,
            )

        episode_instances = [episode["instance"] for episode in episodes]
        Episode.objects.bulk_create(episode_instances, ignore_conflicts=True)

        logger.info("Imported %s episodes", len(episode_instances))


def add_bulk_media(
    row: dict,
    media_mapping: dict,
    user: User,
    bulk_media: dict,
) -> None:
    """Add media to list for bulk creation."""

    media_type = row["media_type"]

    instance = media_mapping["model"](
        user=user,
        title=row["title"],
        image=row["image"],
    )
    if media_type == "season":
        instance.season_number = row["season_number"]

    form = media_mapping["form"](
        row,
        instance=instance,
        initial={"media_type": media_type},
        post_processing=False,
    )

    if form.is_valid():
        bulk_media[media_type].append(form.instance)
    else:
        logger.error("Error importing %s: %s", row["title"], form.errors.as_data())


def add_bulk_image(row: dict, media_mapping: dict, bulk_image: dict) -> None:
    """Add image to list for bulk download."""
    media_type = row["media_type"]
    img_url_format = media_mapping["img_url"]
    img_filename = row["image"].split("-", 1)[-1]

    if media_type in ("anime", "manga"):
        # check if anilist or mal
        # mal -> anime-11111l.jpg (all digits except last letter "l")
        # anilist -> anime-dv332fds.jpg
        if row["image"][5:-5].isdigit() and row["image"][-5] == "l":
            bulk_image[media_type].append(
                img_url_format["mal"].format(
                    media_id=row["media_id"],
                    image_file=img_filename,
                ),
            )
        else:
            bulk_image[media_type].append(
                img_url_format["anilist"].format(image_file=img_filename),
            )
    else:
        # tv-f496cm9enuEsZkSPzCwnTESEK5s.jpg -> https://image.tmdb.org/t/p/w500/f496cm9enuEsZkSPzCwnTESEK5s.jpg
        bulk_image[media_type].append(img_url_format.format(image_file=img_filename))
