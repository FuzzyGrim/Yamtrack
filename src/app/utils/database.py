from app.models import Media, Season
from app.utils import helpers
from django.db.models import Avg, Sum, Min, Max


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
    api,
    user,
    season_selected,
    seasons,
):
    if image == "":
        image = "none.svg"
    else:
        if api == "tmdb":
            image = f"https://image.tmdb.org/t/p/w300{image}"
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
    api,
    user,
    season_selected,
    seasons,
):

    media = Media.objects.get(
        media_id=media_id,
        media_type=media_type,
        user=user,
        api=api,
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
