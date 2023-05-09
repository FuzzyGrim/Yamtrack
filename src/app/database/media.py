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

    media_db = Media.objects.get(media_id=media_id, media_type=media_type, user=user)

    if progress != media_db.progress:
        is_progress_edited = True
    else:
        is_progress_edited = False

    media_db.score = score
    media_db.progress = progress
    media_db.status = status
    media_db.start_date = start_date
    media_db.end_date = end_date
    media_db.notes = notes
    media_db.save()

    logger.info(f"Edited {media_db}")

    return media_db, is_progress_edited


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

        # # Determine the progress and status of the current season
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
        logger.info(f"Added {season_db} because of {media}'s progress update")

        # Add episodes for the current season to the database
        season.add_episodes_for_season(season_db)

        # Update the remaining progress and move on to the next season
        progress_remaining -= season_progress
        season_num += 1
