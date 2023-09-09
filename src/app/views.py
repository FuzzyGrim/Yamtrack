import datetime
import logging
from itertools import chain

from config import settings
from crispy_forms.utils import render_crispy_form
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware import csrf
from django.shortcuts import redirect, render

from app.forms import FilterForm
from app.models import TV, Anime, Episode, Manga, Season
from app.utils import form_handlers, helpers, metadata, search

logger = logging.getLogger(__name__)


@login_required
def home(request: HttpRequest) -> HttpResponse:
    """Return the home page."""

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
def progress_edit(request: HttpRequest) -> HttpResponse:
    """Increase or decrease the progress of a media item from home page."""

    media_type = request.POST.get("media_type")
    media_id = request.POST.get("media_id")
    operation = request.POST.get("operation")

    if media_type == "season":
        season_number = request.POST.get("season_number")
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
                watch_date=datetime.datetime.now(tz=settings.TZ).date(),
            )
            logger.info("Watched %sE%s", season, episode_number)

        elif operation == "decrease":
            episode_number = season_metadata["episodes"][season.progress - 1][
                "episode_number"
            ]
            Episode.objects.get(
                related_season=season,
                episode_number=episode_number,
            ).delete()
            logger.info("Unwatched %sE%s", season, episode_number)

        # change status to completed if progress is max
        if season.progress == max_progress:
            season.status = "Completed"
            season.save()
            logger.info("Finished watching %s", season)

        response = {
            "media_id": media_id,
            "progress": season.progress,
            "season_number": season_number,
        }

    else:
        media_mapping = helpers.media_type_mapper(media_type)
        media_metadata = metadata.get_media_metadata(media_type, media_id)

        max_progress = media_metadata.get("num_episodes", 1)

        media = media_mapping["model"].objects.get(
            media_id=media_id,
            user=request.user.id,
        )
        if operation == "increase":
            media.progress += 1
            logger.info("Watched %sE%s", media, media.progress)

        elif operation == "decrease":
            logger.info("Unwatched %sE%s", media, media.progress)
            media.progress -= 1

        # before saving, if progress is max, set status to completed
        if media.progress == max_progress:
            media.status = "Completed"
            logger.info("Finished watching %s", media)
        media.save()
        response = {"media_id": media_id, "progress": media.progress}

    response["max"] = response["progress"] == max_progress

    return render(
        request,
        "app/components/progress.html",
        {"media": response, "media_type": media_type},
    )


@login_required
def media_list(request: HttpRequest, media_type: str) -> HttpResponse:
    """Return the media list page."""

    if request.method == "POST":
        form_handlers.media_form_handler(request)
        return redirect("medialist", media_type=media_type)

    filter_params = {"user_id": request.user.id}

    # filter by status if status is not "all", default to "all"
    status_filter = request.GET.get("status", "all")
    if status_filter != "all":
        filter_params["status"] = status_filter.capitalize()

    # default sort by descending score
    sort_filter = request.GET.get("sort", "-score")

    media_mapping = helpers.media_type_mapper(media_type)
    filter_form = FilterForm(
        # fill form with current values if they exist
        request.GET or None,
        sort_choices=media_mapping["sort_choices"],
    )

    # if form valid or no form submitted
    if filter_form.is_valid() or not request.GET:
        if media_type == "tv":
            if "status" in filter_params:
                # as tv doesn't have a status field, only filter seasons
                media_list = Season.objects.filter(**filter_params).order_by(
                    sort_filter,
                )

            else:
                # show both tv and seasons in the list
                tv_list = TV.objects.filter(user_id=request.user)
                season_list = Season.objects.filter(user_id=request.user)

                media_list = sorted(
                    chain(tv_list, season_list),
                    # when sorting by score, if score is null, use -inf
                    key=lambda item: getattr(item, sort_filter, float("-inf")),
                )
        else:
            media_list = (
                media_mapping["model"]
                .objects.filter(**filter_params)
                .order_by(sort_filter)
            )

        return render(
            request,
            media_mapping["list_layout"],
            {
                "media_type": media_type,
                "media_list": media_list,
                "filter_form": filter_form,
            },
        )

    logger.error("Invalid filter parameters: %s", {filter_form.errors.as_data})
    return redirect("medialist", media_type=media_type)


@login_required
def media_search(request: HttpRequest) -> HttpResponse:
    """Return the media search page."""

    media_type = request.GET.get("media_type")
    query = request.GET.get("q")

    if request.method == "POST":
        form_handlers.media_form_handler(request)
        return redirect("/search?media_type=" + media_type + "&q=" + query)

    if media_type and query:
        # update user default search type
        request.user.last_search_type = media_type
        request.user.save()

        if media_type in ("anime", "manga"):
            query_list = search.mal(media_type, query)
        elif media_type in ("tv", "movie"):
            query_list = search.tmdb(media_type, query)

        context = {"query_list": query_list}

    else:
        context = {}

    return render(request, "app/search.html", context)


@login_required
def media_details(
    request: HttpRequest,
    media_type: str,
    media_id: str,
    title: str,
) -> HttpResponse:
    """Return the details page for a media item."""

    media_metadata = metadata.get_media_metadata(media_type, media_id)

    if request.method == "POST":
        form_handlers.media_form_handler(request, title=media_metadata["title"])
        return redirect("media_details", media_type, media_id, title)

    related_data_list = [
        {"name": "Related Animes", "data": media_metadata.get("related_anime")},
        {"name": "Related Mangas", "data": media_metadata.get("related_manga")},
        {"name": "Recommendations", "data": media_metadata.get("recommendations")},
    ]

    context = {
        "media": media_metadata,
        "seasons": media_metadata.get("seasons"),
        "related_data_list": related_data_list,
    }
    return render(request, "app/media_details.html", context)


@login_required
def season_details(
    request: HttpRequest, media_id: str, title: str, season_number: str
) -> HttpResponse:
    """Return the details page for a season."""

    season_metadata = metadata.season(media_id, season_number)
    tv_metadata = metadata.tv(media_id)

    if request.method == "POST":
        # add tv show title to season metadata
        season_metadata["title"] = tv_metadata["title"]
        form_handlers.media_form_handler(
            request,
            season_metadata,
            season_number,
        )

        return redirect("season_details", media_id, title, season_number)

    # returns tuple of watched episodes in database
    watched_episodes = set(
        Episode.objects.filter(
            related_season__media_id=media_id,
            related_season__season_number=season_number,
            related_season__user=request.user,
        ).values_list("episode_number", flat=True),
    )
    for episode in season_metadata["episodes"]:
        episode["watched"] = episode["episode_number"] in watched_episodes

    # set previous and next season numbers
    min_season = tv_metadata["seasons"][0]["season_number"]
    max_season = tv_metadata["seasons"][-1]["season_number"]

    previous_season = season_number - 1 if season_number > min_season else None

    next_season = season_number + 1 if season_number < max_season else None

    context = {
        "media_id": media_id,
        "media_title": tv_metadata["title"],
        "season": season_metadata,
        "previous_season": previous_season,
        "next_season": next_season,
    }
    return render(request, "app/season_details.html", context)


def modal_data(request: HttpRequest) -> HttpResponse:
    """Return the form modal for a media item."""

    media_type = request.GET.get("media_type")
    media_id = request.GET.get("media_id")
    media_mapping = helpers.media_type_mapper(media_type)

    if media_type == "season":
        season_number = request.GET.get("season_number")
        # set up filters to retrieve the appropriate media object
        filters = {
            "media_id": media_id,
            "season_number": season_number,
            "user": request.user,
        }
        initial_data = {
            "media_id": media_id,
            "media_type": media_type,
            "season_number": season_number,
        }

    else:
        filters = {"media_id": media_id, "user": request.user}
        initial_data = {"media_id": media_id, "media_type": media_type}

    try:
        # try to retrieve the media object using the filters
        media = media_mapping["model"].objects.get(**filters)
        form = media_mapping["form"](instance=media, initial=initial_data)
        allow_delete = True
    except media_mapping["model"].DoesNotExist:
        form = media_mapping["form"](initial=initial_data)
        allow_delete = False

    return render(
        request,
        "app/components/form_modal_content.html",
        {"title": request.GET.get("title"), "form": form, "allow_delete": allow_delete},
    )
