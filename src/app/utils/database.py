from app.models import Media, Season
from app.utils import helpers
from django.db.models import Avg, Sum, Min, Max


def media_form_handler(request):
    metadata = request.session.get("metadata")
    if "delete" in request.POST:
        Media.objects.get(
            media_id=metadata["id"],
            media_type=metadata["media_type"],
            user=request.user,
        ).delete()

    elif "status" in request.POST:
        request.POST = helpers.fix_inputs(request, metadata)

        if Media.objects.filter(
            media_id=metadata["id"],
            media_type=metadata["media_type"],
            user=request.user,
        ).exists():
            edit_media(
                metadata["id"],
                metadata["title"],
                metadata["image"],
                metadata["media_type"],
                request.POST["score"],
                request.POST["progress"],
                request.POST["status"],
                request.POST["start"],
                request.POST["end"],
                request.user,
                request.POST.get("season"),
                metadata.get("seasons"),
            )
        else:
            add_media(
                metadata["id"],
                metadata["title"],
                metadata["image"],
                metadata["media_type"],
                request.POST["score"],
                request.POST["progress"],
                request.POST["status"],
                request.POST["start"],
                request.POST["end"],
                request.user,
                request.POST.get("season"),
                metadata.get("seasons"),
            )

    # after form submission, metadata is no longer needed
    del request.session["metadata"]


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
    user,
    season_selected,
    seasons,
):

    if image != "none.svg":
        filename = helpers.download_image(image, media_type)
        image = f"{filename}"

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
        api=api,
        user=user
    )

    media.save()

    # if request is for a season, create a season object
    if season_selected and season_selected != "general":

        # when there are specials episodes, they are on season 0,
        # so offset everything by 1
        if seasons[0]["season_number"] == 0:
            offset = 0
        else:
            offset = 1

        # get the selected season from the metadata
        meta_sel_season = seasons[int(season_selected) - offset]

        # if completed and has episode count, set progress to episode count
        if (status == "Completed" and "episode_count" in meta_sel_season):
            media.progress = meta_sel_season["episode_count"]
            media.save()

        Season.objects.create(
            parent=media,
            title=media.title,
            number=season_selected,
            score=media.score,
            status=media.status,
            progress=media.progress,
            start_date=media.start_date,
            end_date=media.end_date,
        )


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
    user,
    season_selected,
    seasons,
):

    media = Media.objects.get(
        media_id=media_id,
        media_type=media_type,
        user=user,
    )

    if season_selected and season_selected != "general":
        # if media didn't have any seasons,
        # create first season with the same data as the media
        if Season.objects.filter(parent=media).count() == 0:
            Season.objects.create(
                parent=media,
                title=media.title,
                number=1,
                score=media.score,
                status=media.status,
                progress=media.progress,
                start_date=media.start_date,
                end_date=media.end_date,
            )

        # when there are specials episodes, they are on season 0,
        # so offset everything by 1
        if seasons[0]["season_number"] == 0:
            offset = 0
        else:
            offset = 1
        meta_curr_season = seasons[int(season_selected) - offset]

        if ("episode_count" in meta_curr_season and status == "Completed"):
            progress = meta_curr_season["episode_count"]

        Season.objects.update_or_create(
            parent=media,
            number=season_selected,
            defaults={
                "title": title,
                "score": score,
                "status": status,
                "progress": progress,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        # update media data based on the seasons
        seasons = Season.objects.filter(parent=media)
        media.score = seasons.aggregate(Avg("score"))["score__avg"]
        media.progress = seasons.aggregate(Sum("progress"))["progress__sum"]
        media.status = status
        media.start_date = seasons.aggregate(Min("start_date"))["start_date__min"]
        media.end_date = seasons.aggregate(Max("end_date"))["end_date__max"]
        media.save()

    else:
        media.score = score
        media.progress = progress
        media.status = status
        media.start_date = start_date
        media.end_date = end_date
        media.save()
