from django.db.models import Avg, Sum, Min, Max
from app.models import Media, Season, Episode
from app.utils import helpers, metadata
from app.database import media

from datetime import date
import logging

logger = logging.getLogger(__name__)


def add_season(
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
    season_number,
    seasons_metadata,
):
    if Media.objects.filter(
        media_id=media_id, media_type=media_type, user=user
    ).exists():
        media_db = Media.objects.get(
            media_id=media_id, media_type=media_type, user=user
        )
    else:
        # if season completed, but not last season, set media status watching
        if (
            status == "Completed"
            # check if last season has aired
            and seasons_metadata[-1]["air_date"]
            and season_number != seasons_metadata[-1]["season_number"]
        ):
            media_status = "Watching"
        else:
            media_status = status

        media_db = media.add_media(
            media_id,
            title,
            image,
            media_type,
            score,
            progress,
            media_status,
            start_date,
            end_date,
            notes,
            user,
        )

    # get the selected season from the metadata
    selected_season_metadata = metadata.get_season_metadata_from_tv(
        season_number, seasons_metadata
    )

    if selected_season_metadata["poster_path"]:
        url = (
            f"https://image.tmdb.org/t/p/w500{selected_season_metadata['poster_path']}"
        )
        season_image = helpers.download_image(url, media_type)
    else:
        season_image = "none.svg"

    season = Season.objects.create(
        parent=media_db,
        image=season_image,
        number=season_number,
        score=score,
        status=status,
        progress=progress,
        start_date=start_date,
        end_date=end_date,
        notes=notes,
    )
    logger.info(f"Created {season}")

    if season.progress > 0:
        add_episodes_for_season(season)


def edit_season(
    media_id,
    media_type,
    score,
    progress,
    status,
    start_date,
    end_date,
    notes,
    user,
    season_number,
    seasons_metadata,
):
    media_db = Media.objects.get(
        media_id=media_id,
        media_type=media_type,
        user=user,
    )

    season_db = Season.objects.get(
        parent=media,
        number=season_number,
    )
    old_progress = season_db.progress

    # update season fields
    season_db.score = score
    season_db.status = status
    season_db.progress = progress
    season_db.start_date = start_date
    season_db.end_date = end_date
    season_db.notes = notes
    season_db.save()
    logger.info(f"Updated {season_db}")

    # Get all the seasons for the parent media instance
    seasons_all = Season.objects.filter(parent=media)

    # Update the media fields based on the aggregated values of the seasons
    media_db.score = seasons_all.aggregate(Avg("score"))["score__avg"]
    media_db.progress = seasons_all.aggregate(Sum("progress"))["progress__sum"]
    media_db.start_date = seasons_all.aggregate(Min("start_date"))["start_date__min"]
    media_db.end_date = seasons_all.aggregate(Max("end_date"))["end_date__max"]

    # if season completed, but not last season, set media status watching
    if (
        status == "Completed"
        # check if last season has aired
        and seasons_metadata[-1]["air_date"]
        and season_number != seasons_metadata[-1]["season_number"]
    ):
        media_db.status = "Watching"
    else:
        media_db.status = status

    # Save the updated media instance
    media_db.save()

    logger.info(f"Updated {media} with new aggregated values")

    if old_progress != season_db.progress:
        season_db.episodes.all().delete()
        logger.info(f"Progress changed, deleting episodes for {season_db}")
        add_episodes_for_season(season_db)


def add_episodes_for_season(season):
    """
    Adds episodes when season is added or when season's progress is updated
    """

    for ep_num in range(1, season.progress + 1):
        episode = Episode.objects.create(
            season=season, number=ep_num, watch_date=date.today()
        )
        logger.info(f"Added {episode}")
