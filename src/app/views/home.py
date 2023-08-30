from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from app.utils import helpers, metadata
from app.models import Season, Episode, Anime, Manga

from datetime import date
import logging


logger = logging.getLogger(__name__)


@login_required
def home(request):
    watching = {}

    seasons = Season.objects.filter(user_id=request.user, status="Watching")
    if seasons.exists():
        watching["season"] = seasons

    animes = Anime.objects.filter(user_id=request.user, status="Watching")
    if animes.exists():
        watching["anime"] = animes

    mangas = Manga.objects.filter(user_id=request.user, status="Watching")
    if mangas.exists():
        watching["manga"] = mangas

    context = {
        "watching": watching,
    }
    return render(request, "app/home.html", context)


@login_required
def progress_edit(request):
    media_type = request.POST.get("media_type")
    media_id = request.POST.get("media_id")
    operation = request.POST.get("operation")

    if media_type == "season":
        season_number = request.POST.get("season_number")
        # season_number = int(season_number)
        season_metadata = metadata.season(media_id, season_number)
        max_progress = len(season_metadata["episodes"])

        season = Season.objects.get(media_id=media_id, season_number=season_number)

        # save episode progress
        if operation == "increase":
            # next episode = current progress + 1, but 0-indexed so -1
            episode_number = season_metadata["episodes"][season.progress][
                "episode_number"
            ]
            Episode.objects.create(
                related_season=season,
                episode_number=episode_number,
                watch_date=date.today(),
            )
            logger.info(f"Watched {season}E{episode_number}")

        elif operation == "decrease":
            episode_number = season_metadata["episodes"][season.progress - 1][
                "episode_number"
            ]
            Episode.objects.get(
                related_season=season, episode_number=episode_number
            ).delete()
            logger.info(f"Unwatched {season}E{episode_number}")

        # change status to completed if progress is max
        if season.progress == max_progress:
            season.status = "Completed"
            season.save()
            logger.info(f"Finished watching {season}")

        response = {"progress": season.progress}

    else:
        media_mapping = helpers.media_type_mapper(media_type)
        media_metadata = metadata.get_media_metadata(media_type, media_id)

        max_progress = media_metadata.get("num_episodes", 1)

        media = media_mapping["model"].objects.get(
            media_id=media_id, user=request.user.id
        )
        if operation == "increase":
            media.progress += 1
            logger.info(f"Watched {media} E{media.progress}")

        elif operation == "decrease":
            logger.info(f"Unwatched {media} E{media.progress}")
            media.progress -= 1

        # before saving, if progress is max, set status to completed
        if media.progress == max_progress:
            media.status = "Completed"
            logger.info(f"Finished watching {media}")
        media.save()
        response = {"progress": media.progress}

    response["min"] = response["progress"] == 0
    response["max"] = response["progress"] == max_progress

    return JsonResponse(response)
