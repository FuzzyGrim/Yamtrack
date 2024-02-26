import logging

from django.apps import apps
from django.contrib import messages
from django.db.models import F
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme

from app.forms import FilterForm, get_form_class
from app.models import Anime, Episode, Manga, Movie, Season
from app.providers import mal, services, tmdb

logger = logging.getLogger(__name__)


def home(request: HttpRequest) -> HttpResponse:
    """Return the home page."""
    in_progress = {}

    movies = Movie.objects.filter(user=request.user, status="In progress")
    if movies.exists():
        in_progress["movie"] = movies

    seasons = Season.objects.filter(
        user=request.user,
        status="In progress",
    ).prefetch_related("episodes")

    if seasons.exists():
        in_progress["season"] = seasons

    animes = Anime.objects.filter(user=request.user, status="In progress")
    if animes.exists():
        in_progress["anime"] = animes

    mangas = Manga.objects.filter(user=request.user, status="In progress")
    if mangas.exists():
        in_progress["manga"] = mangas

    context = {
        "in_progress": in_progress,
    }
    return render(request, "app/home.html", context)


def progress_edit(request: HttpRequest) -> HttpResponse:
    """Increase or decrease the progress of a media item from home page."""

    media_type = request.POST["media_type"]
    media_id = request.POST["media_id"]
    operation = request.POST["operation"]

    if media_type == "season":
        season_number = request.POST["season_number"]
        season_metadata = tmdb.season(media_id, season_number)
        max_progress = len(season_metadata["episodes"])
        search_params = {
            "media_id": media_id,
            "user": request.user,
            "season_number": season_number,
        }

    else:
        media_metadata = services.get_media_metadata(media_type, media_id)
        max_progress = media_metadata["details"]["number_of_episodes"]
        search_params = {"media_id": media_id, "user": request.user}

    model = apps.get_model(app_label="app", model_name=media_type)

    try:
        media = model.objects.get(**search_params)

        if operation == "increase":
            media.increase_progress()

        elif operation == "decrease":
            media.decrease_progress()

        response = {
            "media_id": media_id,
            "progress": media.progress,
            "max": media.progress == max_progress,
        }

        if media_type == "season":
            response["season_number"] = season_number

        return render(
            request,
            "app/components/progress_changer.html",
            {"media": response, "media_type": media_type},
        )
    except model.DoesNotExist:
        messages.error(
            request,
            "Media item was deleted before trying to change progress",
        )
        logger.exception("Media item was deleted before trying to change progress")

        response = HttpResponse()
        response["HX-Redirect"] = reverse("home")
        return response


def media_list(request: HttpRequest, media_type: str) -> HttpResponse:
    """Return the media list page."""

    layout_user = request.user.get_layout(media_type)
    filter_form = FilterForm(layout=layout_user)

    # form submitted
    if request.GET:
        layout_request = request.GET.get("layout", layout_user)
        filter_form = FilterForm(request.GET, layout=layout_request)
        if filter_form.is_valid() and layout_request != layout_user:
            # update user default layout for media type
            request.user.set_layout(media_type, layout_request)
            layout_user = layout_request

    filter_params = {"user": request.user.id}

    # filter by status if status is not "all"
    status_filter = request.GET.get("status", "all")
    if status_filter != "all":
        filter_params["status"] = status_filter.capitalize()

    # default sort by descending score
    sort_filter = request.GET.get("sort", "score")

    model = apps.get_model(app_label="app", model_name=media_type)
    layout_is_table = layout_user == "table"
    sort_is_property = sort_filter in ("progress", "start_date", "end_date")

    if media_type == "tv" and (layout_is_table or sort_is_property):
        media_list = model.objects.filter(**filter_params).prefetch_related(
            "seasons",
            "seasons__episodes",
        )
    elif media_type == "season" and (layout_is_table or sort_is_property):
        media_list = Season.objects.filter(**filter_params).prefetch_related("episodes")
    else:
        media_list = model.objects.filter(**filter_params)

    # python for @property sorting
    if media_type in ("tv", "season") and sort_is_property:
        media_list = sorted(
            media_list,
            key=lambda x: getattr(x, sort_filter),
            reverse=True,
        )
    else:
        model = apps.get_model(app_label="app", model_name=media_type)
        # asc order
        if sort_filter == "title":
            media_list = media_list.order_by(F(sort_filter).asc())
        # desc order
        else:
            media_list = media_list.order_by(F(sort_filter).desc())

    return render(
        request,
        request.user.get_layout_template(media_type),
        {
            "media_type": media_type,
            "media_list": media_list,
            "filter_form": filter_form,
        },
    )


def media_search(request: HttpRequest) -> HttpResponse:
    """Return the media search page."""

    media_type = request.GET["media_type"]
    query = request.GET["q"]
    # update user default search type
    request.user.last_search_type = media_type
    request.user.save(update_fields=["last_search_type"])

    if media_type in ("anime", "manga"):
        query_list = mal.search(media_type, query)
    elif media_type in ("tv", "movie"):
        query_list = tmdb.search(media_type, query)

    context = {"query_list": query_list}

    return render(request, "app/search.html", context)


def media_details(
    request: HttpRequest,
    media_type: str,
    media_id: str,
    title: str,  # noqa: ARG001 for URL
) -> HttpResponse:
    """Return the details page for a media item."""

    media_metadata = services.get_media_metadata(media_type, media_id)

    context = {"media": media_metadata}
    return render(request, "app/media_details.html", context)


def season_details(
    request: HttpRequest,
    media_id: str,
    title: str,  # noqa: ARG001 for URL
    season_number: str,
) -> HttpResponse:
    """Return the details page for a season."""

    tv_metadata = tmdb.tv_with_seasons(media_id, [season_number])
    season_metadata = tv_metadata[f"season/{season_number}"]

    watched_episodes = dict(
        Episode.objects.filter(
            related_season__media_id=media_id,
            related_season__season_number=season_number,
            related_season__user=request.user,
        ).values_list("episode_number", "watch_date"),
    )

    season_metadata["episodes"] = tmdb.process_episodes(
        season_metadata,
        watched_episodes,
    )

    context = {"season": season_metadata, "tv": tv_metadata}
    return render(request, "app/season_details.html", context)


def track_form(request: HttpRequest) -> HttpResponse:
    """Return the tracking form for a media item."""

    media_type = request.GET["media_type"]
    media_id = request.GET["media_id"]

    if media_type == "season":
        season_number = request.GET["season_number"]
        # set up filters to retrieve the appropriate media object
        filters = {
            "media_id": media_id,
            "user": request.user,
            "season_number": season_number,
        }
        initial_data = {
            "media_id": media_id,
            "media_type": media_type,
            "season_number": season_number,
        }
        title = f"{request.GET['title']} S{season_number}"
        form_id = f"form-{media_type}_{media_id}_{season_number}"

    else:
        filters = {"media_id": media_id, "user": request.user}
        initial_data = {"media_id": media_id, "media_type": media_type}
        title = request.GET["title"]
        form_id = f"form-{media_type}_{media_id}"

    model = apps.get_model(app_label="app", model_name=media_type)

    try:
        # try to retrieve the media object using the filters
        media = model.objects.get(**filters)
        form = get_form_class(media_type)(instance=media, initial=initial_data)
        form.helper.form_id = form_id
        allow_delete = True
    except model.DoesNotExist:
        form = get_form_class(media_type)(initial=initial_data)
        form.helper.form_id = form_id
        allow_delete = False

    return render(
        request,
        "app/components/fill_track_form.html",
        {
            "title": title,
            "form_id": form_id,
            "form": form,
            "allow_delete": allow_delete,
            "return_url": request.GET["return_url"],
        },
    )


def media_save(request: HttpRequest) -> HttpResponse:
    """Save or update media data to the database."""

    media_id = request.POST["media_id"]
    media_type = request.POST["media_type"]
    model = apps.get_model(app_label="app", model_name=media_type)

    if media_type == "season":
        season_number = request.POST["season_number"]
        tv_metadata = tmdb.tv_with_seasons(media_id, [season_number])
        media_metadata = tv_metadata[f"season/{season_number}"]
        # get title from tv metadata
        media_metadata["title"] = tv_metadata["title"]
    else:
        media_metadata = services.get_media_metadata(media_type, media_id)

    search_params = {
        "media_id": media_id,
        "user": request.user,
    }

    if media_type == "season":
        search_params["season_number"] = season_number

    try:
        instance = model.objects.get(**search_params)
    except model.DoesNotExist:
        default_params = {
            "title": media_metadata["title"],
            "image": media_metadata["image"],
            "user": request.user,
        }
        if media_type == "season":
            default_params["season_number"] = season_number

        instance = model(**default_params)

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        logger.info("%s saved successfully.", form.instance)
    else:
        logger.error(form.errors.as_data())
        messages.error(
            request,
            "Could not save the media item, there were errors in the form.",
        )

    if url_has_allowed_host_and_scheme(request.GET.get("next"), None):
        url = iri_to_uri(request.GET["next"])
        return redirect(url)
    return redirect("home")


def media_delete(request: HttpRequest) -> HttpResponse:
    """Delete media data from the database."""

    media_id = request.POST["media_id"]
    media_type = request.POST["media_type"]

    search_params = {
        "media_id": media_id,
        "user": request.user,
    }

    if media_type == "season":
        search_params["season_number"] = request.POST["season_number"]

    model = apps.get_model(app_label="app", model_name=media_type)
    try:
        model.objects.get(**search_params).delete()
        logger.info("%s deleted successfully.", media_type)
    except model.DoesNotExist:
        logger.exception("The %s was already deleted before.", media_type)

    if url_has_allowed_host_and_scheme(request.GET.get("next"), None):
        url = iri_to_uri(request.GET["next"])
        return redirect(url)
    return redirect("home")


def episode_handler(request: HttpRequest) -> HttpResponse:
    """Handle the creation, deletion, and updating of episodes for a season."""

    media_id = request.POST["media_id"]
    season_number = request.POST["season_number"]

    try:
        related_season = Season.objects.get(
            media_id=media_id,
            user=request.user,
            season_number=season_number,
        )
    except Season.DoesNotExist:
        season_metadata = tmdb.season(media_id, season_number)
        related_season = Season(
            media_id=media_id,
            image=season_metadata["image"],
            score=None,
            status="In progress",
            notes="",
            user=request.user,
            season_number=season_number,
        )
        related_season.related_tv = related_season.get_tv()
        related_season.title = related_season.related_tv.title

        Season.save(related_season)
        logger.info("%s did not exist, it was created successfully.", related_season)

    episode_number = request.POST["episode_number"]
    if "unwatch" in request.POST:
        Episode.objects.filter(
            related_season=related_season,
            episode_number=episode_number,
        ).delete()

        logger.info("%s %s deleted successfully.", related_season, episode_number)

        if related_season.status == "Completed":
            related_season.status = "In progress"
            related_season.save(update_fields=["status"])
    else:
        if "release" in request.POST:
            watch_date = request.POST["release"]
        else:
            # set watch date from form
            watch_date = request.POST["date"]

        Episode.objects.update_or_create(
            related_season=related_season,
            episode_number=episode_number,
            defaults={
                "watch_date": watch_date,
            },
            create_defaults={
                "related_season": related_season,
                "episode_number": episode_number,
                "watch_date": watch_date,
            },
        )

        logger.info("%sE%s saved successfully.", related_season, episode_number)

    if url_has_allowed_host_and_scheme(request.GET.get("next"), None):
        url = iri_to_uri(request.GET["next"])
        return redirect(url)
    return redirect("home")
