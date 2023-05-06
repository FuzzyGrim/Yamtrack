from app.models import Media, Season
from app.utils import metadata, helpers
from app.database.media import add_media, edit_media
from app.database.season import add_season, edit_season

import logging

logger = logging.getLogger(__name__)


def media_season_form_handler(request):
    media_type = request.POST.get("media_type")
    media_id = request.POST.get("media_id")
    season_number = request.POST.get("season_number")

    media_metadata = metadata.get_media_metadata(media_type, media_id)

    if "delete" in request.POST:
        delete_handler(request, media_metadata, media_id, media_type, season_number)

    elif "status" in request.POST:
        request.POST = helpers.clean_data(request, media_metadata)
        if season_number:
            season_handler(request, media_metadata, media_id, media_type, season_number)
        else:
            media_handler(request, media_metadata, media_id, media_type)


def delete_handler(request, media_metadata, media_id, media_type, season_number):
    if season_number:
        Season.objects.get(
            parent__media_id=media_id,
            parent__media_type=media_type,
            parent__user=request.user,
            number=request.POST.get("season_number"),
        ).delete()

        logger.info(
            f"Deleted season {request.POST.get('season_number')} of {media_metadata['title']} ({media_id})"
        )
    else:
        Media.objects.get(
            media_id=media_id,
            media_type=media_type,
            user=request.user,
        ).delete()

        logger.info(f"Deleted {media_metadata['title']} ({media_id})")


def media_handler(request, media_metadata, media_id, media_type):
    if Media.objects.filter(
        media_id=media_id,
        media_type=media_type,
        user=request.user,
    ).exists():
        edit_media(
            media_id,
            media_metadata["title"],
            media_metadata["image"],
            media_type,
            request.POST.get("score"),
            request.POST.get("progress"),
            request.POST.get("status"),
            request.POST.get("start_date"),
            request.POST.get("end_date"),
            request.POST.get("notes"),
            request.user,
        )
    # else add it
    else:
        add_media(
            media_id,
            media_metadata["title"],
            media_metadata["image"],
            media_type,
            request.POST.get("score"),
            request.POST.get("progress"),
            request.POST.get("status"),
            request.POST.get("start_date"),
            request.POST.get("end_date"),
            request.POST.get("notes"),
            request.user,
        )


def season_handler(request, media_metadata, media_id, media_type, season_number):
    if Season.objects.filter(
        parent__media_id=media_id,
        parent__media_type=media_type,
        parent__user=request.user,
        number=request.POST.get("season_number"),
    ).exists():
        edit_season(
            media_id,
            media_type,
            request.POST.get("score"),
            request.POST.get("progress"),
            request.POST.get("status"),
            request.POST.get("start_date"),
            request.POST.get("end_date"),
            request.POST.get("notes"),
            request.user,
            request.POST.get("season_number"),
            media_metadata.get("seasons"),
        )
    else:
        add_season(
            media_id,
            media_metadata["title"],
            media_metadata["image"],
            media_type,
            request.POST.get("score"),
            request.POST.get("progress"),
            request.POST.get("status"),
            request.POST.get("start_date"),
            request.POST.get("end_date"),
            request.POST.get("notes"),
            request.user,
            request.POST.get("season_number"),
            media_metadata.get("seasons"),
        )


def episode_form_handler(request, season, episodes_db):
    return


# Used when updating progress from homepage.
def progress_handler(operation, curr_progress, max_progress, status):
    """
    Updates progress and status of media object based on operation.
    Sets status to "Completed" if progress is equal to max_progress.
    """
    if operation == "increment" and curr_progress < max_progress:
        curr_progress += 1
        if curr_progress == max_progress:
            status = "Completed"
    elif operation == "decrement" and curr_progress > 0:
        curr_progress -= 1
    return curr_progress, status
