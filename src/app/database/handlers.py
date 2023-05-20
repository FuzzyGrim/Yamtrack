from app.models import Media, Season, Episode
from app.utils import metadata, helpers
from app.database import media, season, episode
from django.db.models import Sum

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
            f"Deleted {media_metadata['title']} ({media_id}) - S{season_number}"
        )

        # get status of last season
        last_season = Season.objects.filter(
            parent__media_id=media_id,
            parent__media_type=media_type,
            parent__user=request.user,
        ).order_by("-number").first()

        if last_season:
            logger.info(f"Proceeding to update media fields for {last_season.parent}")
            # media status will be the status of the last season
            media_status = last_season.status
            # update media fields
            season.edit_media_from_season(last_season.parent, media_status)

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
        media_edited, is_progress_edited = media.edit_media(
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
        # update seasons and episodes if progress is updated
        if media_type == "tv" and is_progress_edited:
            media_edited.seasons.all().delete()
            media.add_seasons_episodes_for_media(media_edited)
    else:
        media_added = media.add_media(
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
        # add seasons and episodes according to progress
        if media_type == "tv" and request.POST.get("progress") > 0:
            media.add_seasons_episodes_for_media(media_added)


def season_handler(request, media_metadata, media_id, media_type, season_number):
    if Season.objects.filter(
        parent__media_id=media_id,
        parent__media_type=media_type,
        parent__user=request.user,
        number=request.POST.get("season_number"),
    ).exists():
        season_edited, is_progress_edited = season.edit_season(
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

        # update media fields
        media_status = season.get_media_status_from_season(
            season_edited.status,
            request.POST.get("season_number"),
            media_metadata.get("seasons"),
        )
        season.edit_media_from_season(season_edited.parent, media_status)

        # update episodes if progress is updated
        if is_progress_edited:
            season_edited.episodes.all().delete()
            logger.info(f"Progress changed, deleting episodes for {season_edited}")
            season.add_episodes_for_season(season_edited)

    else:
        season_added, is_media_new = season.add_season(
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
        # update media fields if media is not new
        if not is_media_new:
            media_status = season.get_media_status_from_season(
                season_added.status, season_number, media_metadata.get("seasons")
            )
            season.edit_media_from_season(season_added.parent, media_status)

        # add episodes according to progress
        if season_added.progress > 0:
            season.add_episodes_for_season(season_added)


def episode_form_handler(request, tv, season_metadata, season_number):
    episodes_checked = request.POST.getlist("episode_number")

    if not Season.objects.filter(
        parent__media_id=request.POST.get("media_id"),
        parent__media_type="tv",
        parent__user=request.user,
        number=season_number,
    ).exists():
        season_db, is_media_new = season.add_season(
            request.POST.get("media_id"),
            tv["title"],
            season_metadata["image"],
            "tv",
            score=None,
            progress=0,
            status="Watching",
            start_date=None,
            end_date=None,
            notes="",
            user=request.user,
            season_number=season_number,
            seasons_metadata=tv["seasons"],
        )
    else:
        season_db = Season.objects.get(
            parent__media_id=request.POST.get("media_id"),
            parent__media_type="tv",
            parent__user=request.user,
            number=season_number,
        )

    if "unwatch" in request.POST:
        for episode_checked in episodes_checked:
            Episode.objects.filter(season=season_db, number=episode_checked).delete()
        logger.info(f"Deleted episodes for {season_db}")
    else:
        episode.add_update_episodes(
            request, episodes_checked, season_metadata, season_db
        )

    # update season progress
    season_db.progress = season_db.episodes.count()
    if season_db.progress == len(season_metadata["episodes"]):
        season_db.status = "Completed"
    season_db.save()
    logger.info(f"Updated progress field for {season_db}")

    # update media progress
    media_db = season_db.parent
    media_db.progress = media_db.seasons.aggregate(Sum("progress"))["progress__sum"]
    media_db.status = season.get_media_status_from_season(
        season_db.status, season_number, tv["seasons"]
    )
    media_db.save()
    logger.info(f"Updated progress field for {media_db}")


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
