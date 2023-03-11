from app.models import Media, Season
from app.utils import helpers
from django.db.models import Avg, Sum, Min, Max


def add_media(request):
    metadata = request.session.get("metadata")

    request.POST = helpers.fix_inputs(request, metadata)

    media = Media(
        media_id=metadata["id"],
        title=metadata["title"],
        media_type=metadata["media_type"],
        score=request.POST["score"],
        progress=request.POST["progress"],
        user=request.user,
        status=request.POST["status"],
        api=metadata["api"],
        start_date=request.POST["start"],
        end_date=request.POST["end"],
    )

    if metadata["image"] == "" or metadata["image"] is None:
        media.image = "images/none.svg"
    else:
        if media.api == "tmdb":
            metadata["image"] = f"https://image.tmdb.org/t/p/w92{metadata['image']}"
        filename = helpers.download_image(metadata["image"], media.media_type)
        media.image = f"images/{filename}"

    media.save()

    # if request is for a season, create a season object
    if "season" in request.POST and request.POST["season"] != "general":
        if metadata["seasons"][0]["season_number"] == 0:
            offset = 0
        else:
            offset = 1

        # if completed and has episode count, set progress to episode count
        if (
            request.POST["status"] == "Completed"
            and "episode_count"
            in metadata["seasons"][int(request.POST["season"]) - offset]
        ):
            media.progress = metadata["seasons"][int(request.POST["season"]) - offset][
                "episode_count"
            ]
            media.save()

        Season.objects.create(
            media=media,
            title=media.title,
            number=request.POST["season"],
            score=media.score,
            status=media.status,
            progress=media.progress,
            start_date=media.start_date,
            end_date=media.end_date,
        )

    del request.session["metadata"]


def edit_media(request):
    metadata = request.session.get("metadata")

    request.POST = helpers.fix_inputs(request, metadata)

    media = Media.objects.get(
        media_id=metadata["id"],
        media_type=metadata["media_type"],
        user=request.user,
        api=metadata["api"],
    )

    if "season" in request.POST and request.POST["season"] != "general":
        # if media didn't have any seasons, create first season with the same data as the media
        if Season.objects.filter(media=media).count() == 0:
            Season.objects.create(
                media=media,
                title=media.title,
                number=1,
                score=media.score,
                status=media.status,
                progress=media.progress,
                start_date=media.start_date,
                end_date=media.end_date,
            )

        if metadata["seasons"][0]["season_number"] == 0:
            offset = 0
        else:
            offset = 1
        metadata_curr_season = metadata["seasons"][int(request.POST["season"]) - offset]

        if (
            "episode_count" in metadata_curr_season
            and request.POST["status"] == "Completed"
        ):
            progress = metadata_curr_season["episode_count"]
        else:
            progress = request.POST["progress"]

        Season.objects.update_or_create(
            media=media,
            number=request.POST["season"],
            defaults={
                "title": metadata["title"],
                "score": request.POST["score"],
                "status": request.POST["status"],
                "progress": progress,
                "start_date": request.POST["start"],
                "end_date": request.POST["end"],
            },
        )

        # update media data based on the seasons
        seasons = Season.objects.filter(media=media)
        media.score = seasons.aggregate(Avg("score"))["score__avg"]
        media.progress = seasons.aggregate(Sum("progress"))["progress__sum"]
        media.status = request.POST["status"]
        media.start_date = seasons.aggregate(Min("start_date"))["start_date__min"]
        media.end_date = seasons.aggregate(Max("end_date"))["end_date__max"]
        media.save()

    else:
        media.score = request.POST["score"]
        media.progress = request.POST["progress"]
        media.status = request.POST["status"]
        media.start_date = request.POST["start"]
        media.end_date = request.POST["end"]
        media.save()

    del request.session["metadata"]
