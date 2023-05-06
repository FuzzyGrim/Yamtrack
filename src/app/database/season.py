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
        media = add_media(
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
        title=title,
        image=season_image,
        number=season_number,
        score=score,
        status=status,
        progress=progress,
        start_date=start_date,
        end_date=end_date,
        notes=notes,
    )


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
