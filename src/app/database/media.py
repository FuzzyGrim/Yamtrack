from app.models import Media, Season
from app.database import season
from app.utils import helpers
from app.utils import metadata


import logging

logger = logging.getLogger(__name__)


def add_media(
    media_id,
    title,
    image,
    media_type,
    score,
    progress,
    status,
    start_date,
    end_date,
    notes,
    user,
):
    if image != "none.svg":
        image = helpers.download_image(image, media_type)

    media = Media(
        media_id=media_id,
        title=title,
        image=image,
        media_type=media_type,
        score=score,
        progress=progress,
        status=status,
        start_date=start_date,
        end_date=end_date,
        notes=notes,
        user=user,
    )

    media.save()
    logger.info(f"Added {media}")

    if media_type == "tv" and progress > 0:
        add_seasons_episodes_for_media(media)

    return media


def edit_media(
    media_id,
    title,
    image,
    media_type,
    score,
    progress,
    status,
    start_date,
    end_date,
    notes,
    user,
):

    media = Media.objects.get(media_id=media_id, media_type=media_type, user=user)
    old_progress = media.progress

    media.score = score
    media.progress = progress
    media.status = status
    media.start_date = start_date
    media.end_date = end_date
    media.notes = notes
    media.save()

    logger.info(f"Edited {media}")

    if media_type == "tv" and media.progress != old_progress:
        media.seasons.all().delete()
        add_seasons_episodes_for_media(media)

    return media


def add_seasons_episodes_for_media(media):
    """
    Add seasons and its episodes when media is added or when media's progress is updated
    """
    progress_remaining = media.progress
    season_num = 1
    media_metadata = metadata.tv(media.media_id)
    while progress_remaining > 0:
        season_metadata = metadata.get_season_metadata_from_tv(
            season_num, media_metadata["seasons"]
        )
        if season_metadata["poster_path"]:
            url = f"https://image.tmdb.org/t/p/w500{season_metadata['poster_path']}"
            image = helpers.download_image(url, media.media_type)
        else:
            image = "none.svg"

        season_max_episodes = season_metadata["episode_count"]
        if progress_remaining > season_max_episodes:
            season_progress = season_max_episodes
            season_status = "Completed"
        else:
            season_progress = progress_remaining
            season_status = media.status

        season_db = Season.objects.create(
            parent=media,
            image=image,
            number=season_num,
            score=media.score,
            status=season_status,
            progress=season_progress,
            start_date=media.start_date,
            end_date=media.end_date,
            notes="",
        )
        logger.info(f"Added {season_db}")

        season.add_episodes_for_season(season_db)

        progress_remaining -= season_progress
        season_num += 1
