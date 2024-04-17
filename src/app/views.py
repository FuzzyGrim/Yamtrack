import logging

from django.apps import apps
from django.contrib import messages
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import (
    require_GET,
    require_POST,
)

from app import helpers
from app.forms import FilterForm, get_form_class
from app.models import Anime, Episode, Game, Manga, Movie, Season
from app.providers import igdb, mal, services, tmdb

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress and repeating."""
    list_by_type = {}

    # Define a list of media types to iterate over
    media_types = [
        (Movie, "movie", None),
        (Season, "season", "episodes"),  # Season needs prefetch_related for episodes
        (Anime, "anime", None),
        (Manga, "manga", None),
        (Game, "game", None),
    ]

    for model, media_type, prefetch_related in media_types:
        media_list = model.objects.filter(
            user=request.user,
            status__in=["Repeating", "In progress"],
        )
        if prefetch_related:
            media_list = media_list.prefetch_related(prefetch_related)
        if media_list:
            list_by_type[media_type] = media_list

    context = {
        "list_by_type": list_by_type,
    }
    return render(request, "app/home.html", context)


@require_POST
def progress_edit(request):
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
        max_progress = media_metadata["max_progress"]
        search_params = {"media_id": media_id, "user": request.user}

    model = apps.get_model(app_label="app", model_name=media_type)

    try:
        media = model.objects.get(**search_params)

        if operation == "increase":
            media.increase_progress()

        elif operation == "decrease":
            media.decrease_progress()

        response = {"media_id": media_id}

        if media_type == "season":
            response["season_number"] = season_number
            response["current_episode"] = media.current_episode
            if media.current_episode:
                response["max"] = media.current_episode.episode_number == max_progress
                response["min"] = False
            else:
                response["max"] = False
                response["min"] = True
        else:
            response["progress"] = media.progress
            response["max"] = media.progress == max_progress
            response["min"] = media.progress == 0

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

        response = HttpResponse()
        response["HX-Redirect"] = reverse("home")
        return response


@require_GET
def media_list(request, media_type):
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
    sort_is_property = sort_filter in ("progress", "start_date", "end_date", "repeats")

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
            media_list = media_list.order_by(F(sort_filter).desc(nulls_last=True))

    return render(
        request,
        request.user.get_layout_template(media_type),
        {
            "media_type": media_type,
            "media_list": media_list,
            "filter_form": filter_form,
        },
    )


@require_GET
def media_search(request):
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
    elif media_type == "game":
        query_list = igdb.search(query)

    context = {"query_list": query_list}

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, media_type, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a media item."""
    media_metadata = services.get_media_metadata(media_type, media_id)

    context = {"media": media_metadata}
    return render(request, "app/media_details.html", context)


@require_GET
def season_details(request, media_id, title, season_number):  # noqa: ARG001 title for URL
    """Return the details page for a season."""
    tv_metadata = tmdb.tv_with_seasons(media_id, [season_number])
    season_metadata = tv_metadata[f"season/{season_number}"]

    episodes_in_db = Episode.objects.filter(
        related_season__media_id=media_id,
        related_season__season_number=season_number,
        related_season__user=request.user,
    ).values("episode_number", "watch_date", "repeats")

    season_metadata["episodes"] = tmdb.process_episodes(
        season_metadata,
        episodes_in_db,
    )

    context = {"season": season_metadata, "tv": tv_metadata}
    return render(request, "app/season_details.html", context)


@require_GET
def track_form(request):
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
        if media_type == "game":
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)

        form = get_form_class(media_type)(instance=media, initial=initial_data)

        form.helper.form_id = form_id
        media_exists = True
    except model.DoesNotExist:
        form = get_form_class(media_type)(initial=initial_data)
        form.helper.form_id = form_id
        media_exists = False

    return render(
        request,
        "app/components/fill_track_form.html",
        {
            "title": title,
            "form_id": form_id,
            "form": form,
            "media_exists": media_exists,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_save(request):
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

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    media_type = request.POST["media_type"]

    search_params = {
        "media_id": request.POST["media_id"],
        "user": request.user,
    }

    if media_type == "season":
        search_params["season_number"] = request.POST["season_number"]

    model = apps.get_model(app_label="app", model_name=media_type)
    try:
        media = model.objects.get(**search_params)
        media.delete()
        logger.info("%s deleted successfully.", media)

    except model.DoesNotExist:
        logger.warning("The %s was already deleted before.", media_type)

    return helpers.redirect_back(request)


@require_POST
def episode_handler(request):
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
        related_season.unwatch(episode_number)

    else:
        if "release" in request.POST:
            watch_date = request.POST["release"]
        else:
            # set watch date from form
            watch_date = request.POST["date"]

        related_season.watch(episode_number, watch_date)

    return helpers.redirect_back(request)


@require_GET
def media_history(request):
    """Return the history page for a media item."""
    media_type = request.GET["media_type"]
    media_id = request.GET["media_id"]
    season_number = request.GET.get("season_number")
    episode_number = request.GET.get("episode_number")

    if media_type == "season":
        model = Season
        search_params = {
            "media_id": media_id,
            "user": request.user,
            "season_number": season_number,
        }
    elif media_type == "episode":
        model = Episode
        search_params = {
            "related_season__media_id": media_id,
            "related_season__user": request.user,
            "related_season__season_number": season_number,
            "episode_number": episode_number,
        }
    else:
        model = apps.get_model(app_label="app", model_name=media_type)
        search_params = {"media_id": media_id, "user": request.user}

    changes = []
    try:
        media = model.objects.get(**search_params)
        history = media.history.all()
        if history is not None:
            last = history.first()
            for _ in range(history.count()):
                new_record, old_record = last, last.prev_record
                if old_record is not None:
                    delta = new_record.diff_against(old_record)
                    changes.append(delta)
                    last = old_record
                else:
                    # If there is no previous record, it's a creation entry
                    history_model = apps.get_model(
                        app_label="app",
                        model_name=f"historical{media_type}",
                    )
                    creation_changes = [
                        {
                            "field": field.verbose_name,
                            "new": getattr(new_record, field.attname),
                        }
                        for field in history_model._meta.get_fields()  # noqa: SLF001
                        if not field.name.startswith("history") and field.name != "id"
                    ]
                    changes.append(
                        {
                            "new_record": new_record,
                            "changes": creation_changes,
                        },
                    )
    except model.DoesNotExist:
        pass

    return render(
        request,
        "app/components/fill_history.html",
        {
            "media_type": media_type,
            "changes": changes,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_history_delete(request):
    """Delete a history record for a media item."""
    history_id = request.POST["history_id"]
    media_type = request.POST["media_type"]

    model_name = f"historical{media_type}"

    history = apps.get_model(app_label="app", model_name=model_name).objects.get(
        history_id=history_id,
    )

    if history.history_user_id == request.user.id:
        history.delete()
        logger.info("History record deleted successfully.")
    else:
        logger.warning("User does not have permission to delete this history record.")

    return helpers.redirect_back(request)
