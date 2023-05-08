from app.models import Media
from app.utils import helpers

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

    media = Media.objects.get(media_id=media_id, media_type=media_type, user=user)

    media.score = score
    media.progress = progress
    media.status = status
    media.start_date = start_date
    media.end_date = end_date
    media.notes = notes
    media.save()

    logger.info(f"Edited {media}")
    return media
