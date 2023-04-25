from django.db.models import Avg, Sum, Min, Max
from app.models import Media, Season
from app.utils import helpers, details

import logging

logger = logging.getLogger(__name__)


def media_form_handler(request):
    media_type = request.POST.get("media_type")
    media_id = request.POST.get("media_id")

    if media_type == "anime" or media_type == "manga":
        metadata = details.anime_manga(media_type, media_id)
    elif media_type == "tv":
        metadata = details.tv(media_id)
    elif media_type == "movie":
        metadata = details.movie(media_id)

    if "delete" in request.POST:
        if request.POST.get("season_number") is not None:
            Season.objects.get(
                parent__media_id=media_id,
                parent__media_type=media_type,
                parent__user=request.user,
                number=request.POST.get("season_number"),
            ).delete()

            logger.info(f"Deleted season {request.POST.get('season_number')} of {metadata['title']} ({media_id})")
        else:
            Media.objects.get(
                media_id=media_id,
                media_type=media_type,
                user=request.user,
            ).delete()

            logger.info(f"Deleted {metadata['title']} ({media_id})")

    elif "status" in request.POST:
        request.POST = helpers.clean_data(request, metadata)

        if Media.objects.filter(
            media_id=media_id,
            media_type=media_type,
            user=request.user,
        ).exists():

            logger.info(f"Media {metadata['title']} ({media_id}) already exists in database. Updating...")

            edit_media(
                media_id,
                metadata["title"],
                metadata["image"],
                media_type,
                request.POST["score"],
                request.POST["progress"],
                request.POST["status"],
                request.POST["start"],
                request.POST["end"],
                request.POST["notes"],
                request.user,
                request.POST.get("season_number"),
                metadata.get("seasons"),
            )
        else:

            logger.info(f"Media {metadata['title']} ({media_id}) does not exist in database. Adding...")

            add_media(
                media_id,
                metadata["title"],
                metadata["image"],
                media_type,
                request.POST["score"],
                request.POST["progress"],
                request.POST["status"],
                request.POST["start"],
                request.POST["end"],
                request.POST["notes"],
                request.user,
                request.POST.get("season_number"),
                metadata.get("seasons"),
            )


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
    season_number,
    seasons,
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
        user=user
    )

    media.save()

    logger.info(f"Added {title} ({media_id})")

    # if request is for a season, create a season object
    if season_number:

        # when there are specials episodes, they are on season 0,
        # so offset everything by 1
        if seasons[0]["season_number"] == 0:
            offset = 0
        else:
            offset = 1

        # get the selected season from the metadata
        season_selected = seasons[season_number - offset]

        # if completed and has episode count, set progress to episode count
        if (status == "Completed" and "episode_count" in season_selected):
            media.progress = season_selected["episode_count"]
            media.save()

        if season_selected["poster_path"]:
            url = f"https://image.tmdb.org/t/p/w500{season_selected['poster_path']}"
            season_image = helpers.download_image(url, media_type)
        else:
            season_image = "none.svg"

        Season.objects.create(
            parent=media,
            title=media.title,
            image=season_image,
            number=season_number,
            score=media.score,
            status=media.status,
            progress=media.progress,
            start_date=media.start_date,
            end_date=media.end_date,
            notes=notes
        )

        logger.info(f"Added season {season_number} of {title} ({media_id})")


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
    season_number,
    seasons,
):
    media = Media.objects.get(
        media_id=media_id,
        media_type=media_type,
        user=user,
    )

    if season_number is not None:
        # when there are specials episodes, they are season 0,
        # so offset everything by 1
        if seasons[0]["season_number"] == 0:
            offset = 0
        else:
            offset = 1
        season_selected = seasons[season_number - offset]

        if (status == "Completed" and "episode_count" in season_selected):
            progress = season_selected["episode_count"]

        if season_selected["poster_path"]:
            url = f"https://image.tmdb.org/t/p/w500{season_selected['poster_path']}"
            season_image = helpers.download_image(url, media_type)
        else:
            season_image = "none.svg"

        Season.objects.update_or_create(
            parent=media,
            number=season_number,
            defaults={
                "title": title,
                "image": season_image,
                "score": score,
                "status": status,
                "progress": progress,
                "start_date": start_date,
                "end_date": end_date,
                "notes": notes,
            },
        )

        logger.info(f"Edited season {season_number} of {title} ({media_id})")

        # update media object with season data
        seasons = Season.objects.filter(parent=media)
        media.score = seasons.aggregate(Avg("score"))["score__avg"]
        media.progress = seasons.aggregate(Sum("progress"))["progress__sum"]
        media.start_date = seasons.aggregate(Min("start_date"))["start_date__min"]
        media.end_date = seasons.aggregate(Max("end_date"))["end_date__max"]
        # if completed and not last season, set status to watching
        if status == "Completed" and season_number != seasons[-1]["season_number"]:
            media.status = "Watching"
        # else set status to last season status
        else:
            media.status = status

        media.save()

        logger.info(f"Updated {title} ({media_id}) with season data")

    else:
        media.score = score
        media.progress = progress
        media.status = status
        media.start_date = start_date
        media.end_date = end_date
        media.notes = notes
        media.save()

        logger.info(f"Edited {title} ({media_id})")
