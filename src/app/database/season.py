from django.db.models import Avg, Sum, Min, Max
from app.models import Media, Season
from app.utils import helpers, metadata
from app.database.media import add_media

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
        media = Media.objects.get(media_id=media_id, media_type=media_type, user=user)
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

        media = add_media(
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
    Season.objects.create(
        parent=media,
        image=season_image,
        number=season_number,
        score=score,
        status=status,
        progress=progress,
        start_date=start_date,
        end_date=end_date,
        notes=notes,
    )
    logger.info(f"Created season {season_number} of {title}")


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
    seasons_metadata
):
    media = Media.objects.get(
        media_id=media_id,
        media_type=media_type,
        user=user,
    )

    season = Season.objects.get(
        parent=media,
        number=season_number,
    )

    season.score = score
    season.status = status
    season.progress = progress
    season.start_date = start_date
    season.end_date = end_date
    season.notes = notes
    season.save()
    logger.info(f"Updated season {season_number} of {media.title}")

    # Get all the seasons for the parent media instance
    seasons_all = Season.objects.filter(parent=media)

    # Update the media fields based on the aggregated values of the seasons
    media.score = seasons_all.aggregate(Avg("score"))["score__avg"]
    media.progress = seasons_all.aggregate(Sum("progress"))["progress__sum"]
    media.start_date = seasons_all.aggregate(Min("start_date"))["start_date__min"]
    media.end_date = seasons_all.aggregate(Max("end_date"))["end_date__max"]

    # if season completed, but not last season, set media status watching
    if (
        status == "Completed"
        # check if last season has aired
        and seasons_metadata[-1]["air_date"]
        and season_number != seasons_metadata[-1]["season_number"]
    ):
        media.status = "Watching"
    else:
        media.status = status

    # Save the updated media instance
    media.save()

    logger.info(f"Updated {media.title} ({media_id}) with season {season_number} data")
